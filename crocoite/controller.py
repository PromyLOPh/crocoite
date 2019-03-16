# Copyright (c) 2017–2018 crocoite contributors
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

"""
Controller classes, handling actions required for archival
"""

import time, tempfile, asyncio, json, os
from itertools import islice
from datetime import datetime
from operator import attrgetter
from yarl import URL

from . import behavior as cbehavior
from .browser import SiteLoader, RequestResponsePair
from .util import getFormattedViewportMetrics, getSoftwareInfo
from .behavior import ExtractLinksEvent

class ControllerSettings:
    __slots__ = ('idleTimeout', 'timeout')

    def __init__ (self, idleTimeout=2, timeout=10):
        self.idleTimeout = idleTimeout
        self.timeout = timeout

    def toDict (self):
        return dict (idleTimeout=self.idleTimeout, timeout=self.timeout)

defaultSettings = ControllerSettings ()

class EventHandler:
    """ Abstract base class for event handler """

    __slots__ = ()

    def push (self, item):
        raise NotImplementedError ()

class StatsHandler (EventHandler):
    __slots__ = ('stats', )

    def __init__ (self):
        self.stats = {'requests': 0, 'finished': 0, 'failed': 0, 'bytesRcv': 0}

    def push (self, item):
        if isinstance (item, RequestResponsePair):
            self.stats['requests'] += 1
            if not item.response:
                self.stats['failed'] += 1
            else:
                self.stats['finished'] += 1
                self.stats['bytesRcv'] += item.response.bytesReceived

class LogHandler (EventHandler):
    """ Handle items by logging information about them """

    __slots__ = ('logger', )

    def __init__ (self, logger):
        self.logger = logger.bind (context=type (self).__name__)

    def push (self, item):
        if isinstance (item, ExtractLinksEvent):
            # limit number of links per message, so json blob won’t get too big
            it = iter (item.links)
            limit = 100
            while True:
                limitlinks = list (islice (it, 0, limit))
                if not limitlinks:
                    break
                self.logger.info ('extracted links', context=type (item).__name__,
                        uuid='8ee5e9c9-1130-4c5c-88ff-718508546e0c', links=limitlinks)


class ControllerStart:
    __slots__ = ('payload', )

    def __init__ (self, payload):
        self.payload = payload

class SinglePageController:
    """
    Archive a single page url.

    Dispatches between producer (site loader and behavior scripts) and consumer
    (stats, warc writer).
    """

    __slots__ = ('url', 'service', 'behavior', 'settings', 'logger', 'handler')

    def __init__ (self, url, logger, \
            service, behavior=cbehavior.available, \
            settings=defaultSettings, handler=None):
        self.url = url
        self.service = service
        self.behavior = behavior
        self.settings = settings
        self.logger = logger.bind (context=type (self).__name__, url=url)
        self.handler = handler or []

    def processItem (self, item):
        for h in self.handler:
            h.push (item)

    async def run (self):
        logger = self.logger
        async def processQueue ():
            async for item in l:
                self.processItem (item)

        async with self.service as browser, SiteLoader (browser, logger=logger) as l:
            handle = asyncio.ensure_future (processQueue ())

            start = time.time ()

            # not all behavior scripts are allowed for every URL, filter them
            enabledBehavior = list (filter (lambda x: self.url in x,
                    map (lambda x: x (l, logger), self.behavior)))

            version = await l.tab.Browser.getVersion ()
            payload = {
                    'software': getSoftwareInfo (),
                    'browser': {
                        'product': version['product'],
                        'useragent': version['userAgent'],
                        'viewport': await getFormattedViewportMetrics (l.tab),
                        },
                    'tool': 'crocoite-single', # not the name of the cli utility
                    'parameters': {
                        'url': self.url,
                        'idleTimeout': self.settings.idleTimeout,
                        'timeout': self.settings.timeout,
                        'behavior': list (map (attrgetter('name'), enabledBehavior)),
                        },
                    }
            self.processItem (ControllerStart (payload))

            await l.navigate (self.url)
            for b in enabledBehavior:
                async for item in b.onload ():
                    self.processItem (item)

            # wait until the browser has a) been idle for at least
            # settings.idleTimeout or b) settings.timeout is exceeded
            timeoutProc = asyncio.ensure_future (asyncio.sleep (self.settings.timeout))
            # the browser might have changed to idle from .navigate until here
            # due to awaits inbetween. Thus, idleProc may never be triggered.
            idleTimeout = None if not l.idle.get() else self.settings.idleTimeout
            while True:
                idleProc = asyncio.ensure_future (l.idle.wait ())
                try:
                    finished, pending = await asyncio.wait([idleProc, timeoutProc, handle],
                            return_when=asyncio.FIRST_COMPLETED, timeout=idleTimeout)
                except asyncio.CancelledError:
                    idleProc.cancel ()
                    timeoutProc.cancel ()
                    break

                if not finished:
                    # idle timeout
                    logger.debug ('idle timeout',
                            uuid='90702590-94c4-44ef-9b37-02a16de444c3')
                    idleProc.cancel ()
                    timeoutProc.cancel ()
                    break
                elif handle in finished:
                    # something went wrong while processing the data
                    logger.error ('fetch failed',
                        uuid='43a0686a-a3a9-4214-9acd-43f6976f8ff3')
                    idleProc.cancel ()
                    timeoutProc.cancel ()
                    handle.result ()
                    assert False # previous line should always raise Exception
                elif timeoutProc in finished:
                    # global timeout
                    logger.debug ('global timeout',
                            uuid='2f858adc-9448-4ace-94b4-7cd1484c0728')
                    idleProc.cancel ()
                    timeoutProc.result ()
                    break
                elif idleProc in finished:
                    # idle state change
                    isIdle = idleProc.result ()
                    logger.debug ('idle state',
                            uuid='e3eaff79-7b56-4d17-aa42-d32fa1ec268b',
                            idle=isIdle)
                    if isIdle:
                        # browser is idle, start the clock
                        idleTimeout = self.settings.idleTimeout
                    else:
                        idleTimeout = None

            for b in enabledBehavior:
                async for item in b.onstop ():
                    self.processItem (item)
            await l.tab.Page.stopLoading ()

            await asyncio.sleep (1)

            for b in enabledBehavior:
                async for item in b.onfinish ():
                    self.processItem (item)

            # wait until loads from behavior scripts are done
            await asyncio.sleep (1)
            if not l.idle.get ():
                while not await l.idle.wait (): pass

            if handle.done ():
                handle.result ()
            else:
                handle.cancel ()

class RecursionPolicy:
    """ Abstract recursion policy """

    __slots__ = ()

    def __call__ (self, urls):
        raise NotImplementedError

class DepthLimit (RecursionPolicy):
    """
    Limit recursion by depth.
    
    depth==0 means no recursion, depth==1 is the page and outgoing links
    """

    __slots__ = ('maxdepth', )

    def __init__ (self, maxdepth=0):
        if maxdepth < 0 or maxdepth > 1:
            raise ValueError ('Unsupported')
        self.maxdepth = maxdepth

    def __call__ (self, urls):
        if self.maxdepth <= 0:
            return {}
        else:
            self.maxdepth -= 1
            return urls

    def __repr__ (self):
        return f'<DepthLimit {self.maxdepth}>'

class PrefixLimit (RecursionPolicy):
    """
    Limit recursion by prefix
    
    i.e. prefix=http://example.com/foo
    ignored: http://example.com/bar http://offsite.example/foo
    accepted: http://example.com/foobar http://example.com/foo/bar
    """

    __slots__ = ('prefix', )

    def __init__ (self, prefix):
        self.prefix = prefix

    def __call__ (self, urls):
        return set (filter (lambda u: str(u).startswith (str (self.prefix)), urls))

class RecursiveController:
    """
    Simple recursive controller

    Visits links acording to policy
    """

    __slots__ = ('url', 'output', 'command', 'logger', 'policy', 'have',
            'pending', 'stats', 'prefix', 'tempdir', 'running', 'concurrency')

    SCHEME_WHITELIST = {'http', 'https'}

    def __init__ (self, url, output, command, logger, prefix='{host}-{date}-',
            tempdir=None, policy=DepthLimit (0), concurrency=1):
        self.url = url
        self.output = output
        self.command = command
        self.prefix = prefix
        self.logger = logger.bind (context=type(self).__name__, seedurl=url)
        self.policy = policy
        self.tempdir = tempdir
        # tasks currently running
        self.running = set ()
        # max number of tasks running
        self.concurrency = concurrency
        # keep in sync with StatsHandler
        self.stats = {'requests': 0, 'finished': 0, 'failed': 0, 'bytesRcv': 0, 'crashed': 0, 'ignored': 0}

    async def fetch (self, url):
        """
        Fetch a single URL using an external command

        command is usually crocoite-grab
        """

        logger = self.logger.bind (url=url)

        def formatCommand (e):
            return e.format (url=url, dest=dest.name)

        def formatPrefix (p):
            return p.format (host=url.host, date=datetime.utcnow ().isoformat ())

        def logStats ():
            logger.info ('stats', uuid='24d92d16-770e-4088-b769-4020e127a7ff', **self.stats)

        if url.scheme not in self.SCHEME_WHITELIST:
            self.stats['ignored'] += 1
            logStats ()
            self.logger.warning ('scheme not whitelisted', url=url,
                    uuid='57e838de-4494-4316-ae98-cd3a2ebf541b')
            return

        dest = tempfile.NamedTemporaryFile (dir=self.tempdir,
                prefix=formatPrefix (self.prefix), suffix='.warc.gz',
                delete=False)
        destpath = os.path.join (self.output, os.path.basename (dest.name))
        command = list (map (formatCommand, self.command))
        logger.info ('fetch', uuid='1680f384-744c-4b8a-815b-7346e632e8db', command=command, destfile=destpath)
        try:
            process = await asyncio.create_subprocess_exec (*command, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, stdin=asyncio.subprocess.DEVNULL,
                    start_new_session=True, limit=100*1024*1024)
            while True:
                data = await process.stdout.readline ()
                if not data:
                    break
                data = json.loads (data)
                uuid = data.get ('uuid')
                if uuid == '8ee5e9c9-1130-4c5c-88ff-718508546e0c':
                    links = set (self.policy (map (lambda x: URL(x).with_fragment(None), data.get ('links', []))))
                    links.difference_update (self.have)
                    self.pending.update (links)
                elif uuid == '24d92d16-770e-4088-b769-4020e127a7ff':
                    for k in self.stats.keys ():
                        self.stats[k] += data.get (k, 0)
                    logStats ()
        except asyncio.CancelledError:
            # graceful cancellation
            process.send_signal (signal.SIGINT)
        except Exception as e:
            process.kill ()
            raise e
        finally:
            code = await process.wait()
            if code == 0:
                # atomically move once finished
                os.rename (dest.name, destpath)
            else:
                self.stats['crashed'] += 1
                logStats ()

    async def run (self):
        def log ():
            self.logger.info ('recursing',
                    uuid='5b8498e4-868d-413c-a67e-004516b8452c',
                    pending=len (self.pending), have=len (self.have),
                    running=len (self.running))

        try:
            self.have = set ()
            self.pending = set ([self.url])

            while self.pending:
                # since pending is a set this picks a random item, which is fine
                u = self.pending.pop ()
                self.have.add (u)
                t = asyncio.ensure_future (self.fetch (u))
                self.running.add (t)

                log ()

                if len (self.running) >= self.concurrency or not self.pending:
                    done, pending = await asyncio.wait (self.running,
                            return_when=asyncio.FIRST_COMPLETED)
                    self.running.difference_update (done)
                    # propagate exceptions
                    for r in done:
                        r.result ()
        except asyncio.CancelledError:
            self.logger.info ('cancel',
                    uuid='d58154c8-ec27-40f2-ab9e-e25c1b21cd88',
                    pending=len (self.pending), have=len (self.have),
                    running=len (self.running))
        finally:
            done = await asyncio.gather (*self.running,
                    return_exceptions=True)
            # propagate exceptions
            for r in done:
                r.result ()
            self.running = set ()
            log ()

