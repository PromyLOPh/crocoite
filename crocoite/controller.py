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

import time, tempfile, asyncio, json, os, shutil
from itertools import islice
from datetime import datetime
from operator import attrgetter
from abc import ABC, abstractmethod
from yarl import URL

from . import behavior as cbehavior
from .browser import SiteLoader, RequestResponsePair, PageIdle, FrameNavigated
from .util import getFormattedViewportMetrics, getSoftwareInfo
from .behavior import ExtractLinksEvent

class ControllerSettings:
    __slots__ = ('idleTimeout', 'timeout', 'insecure')

    def __init__ (self, idleTimeout=2, timeout=10, insecure=False):
        self.idleTimeout = idleTimeout
        self.timeout = timeout
        self.insecure = insecure

    def toDict (self):
        return dict (
                idleTimeout=self.idleTimeout,
                timeout=self.timeout,
                insecure=self.insecure,
                )

defaultSettings = ControllerSettings ()

class EventHandler (ABC):
    """ Abstract base class for event handler """

    __slots__ = ()

    @abstractmethod
    async def push (self, item):
        raise NotImplementedError ()

class StatsHandler (EventHandler):
    __slots__ = ('stats', )

    def __init__ (self):
        self.stats = {'requests': 0, 'finished': 0, 'failed': 0, 'bytesRcv': 0}

    async def push (self, item):
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

    async def push (self, item):
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

class IdleStateTracker (EventHandler):
    """ Track SiteLoader’s idle state by listening to PageIdle events """

    __slots__ = ('_idle', '_loop', '_idleSince')

    def __init__ (self, loop):
        self._idle = True
        self._loop = loop

        self._idleSince = self._loop.time ()

    async def push (self, item):
        if isinstance (item, PageIdle):
            self._idle = bool (item)
            if self._idle:
                self._idleSince = self._loop.time ()

    async def wait (self, timeout):
        """ Wait until page has been idle for at least timeout seconds. If the
        page has been idle before calling this function it may return
        immediately. """

        assert timeout > 0
        while True:
            if self._idle:
                now = self._loop.time ()
                sleep = timeout-(now-self._idleSince)
                if sleep <= 0:
                    break
            else:
                # not idle, check again after timeout expires
                sleep = timeout
            await asyncio.sleep (sleep)

class InjectBehaviorOnload (EventHandler):
    """ Control behavior script injection based on frame navigation messages.
    When a page is reloaded (for whatever reason), the scripts need to be
    reinjected. """

    __slots__ = ('controller', '_loaded')

    def __init__ (self, controller):
        self.controller = controller
        self._loaded = False

    async def push (self, item):
        if isinstance (item, FrameNavigated):
            await self._runon ('load')
            self._loaded = True

    async def stop (self):
        if self._loaded:
            await self._runon ('stop')

    async def finish (self):
        if self._loaded:
            await self._runon ('finish')

    async def _runon (self, method):
        controller = self.controller
        for b in controller._enabledBehavior:
            f = getattr (b, 'on' + method)
            async for item in f ():
                await controller.processItem (item)

class SinglePageController:
    """
    Archive a single page url.

    Dispatches between producer (site loader and behavior scripts) and consumer
    (stats, warc writer).
    """

    __slots__ = ('url', 'service', 'behavior', 'settings', 'logger', 'handler',
            'warcinfo', '_enabledBehavior')

    def __init__ (self, url, logger, \
            service, behavior=cbehavior.available, \
            settings=defaultSettings, handler=None, \
            warcinfo=None):
        self.url = url
        self.service = service
        self.behavior = behavior
        self.settings = settings
        self.logger = logger.bind (context=type (self).__name__, url=url)
        self.handler = handler or []
        self.warcinfo = warcinfo

    async def processItem (self, item):
        for h in self.handler:
            await h.push (item)

    async def run (self):
        logger = self.logger
        async def processQueue ():
            async for item in l:
                await self.processItem (item)

        idle = IdleStateTracker (asyncio.get_event_loop ())
        self.handler.append (idle)
        behavior = InjectBehaviorOnload (self)
        self.handler.append (behavior)

        async with self.service as browser, SiteLoader (browser, logger=logger) as l:
            handle = asyncio.ensure_future (processQueue ())
            timeoutProc = asyncio.ensure_future (asyncio.sleep (self.settings.timeout))

            # configure browser
            tab = l.tab
            await tab.Security.setIgnoreCertificateErrors (ignore=self.settings.insecure)

            # not all behavior scripts are allowed for every URL, filter them
            self._enabledBehavior = list (filter (lambda x: self.url in x,
                    map (lambda x: x (l, logger), self.behavior)))

            version = await tab.Browser.getVersion ()
            payload = {
                    'software': getSoftwareInfo (),
                    'browser': {
                        'product': version['product'],
                        'useragent': version['userAgent'],
                        'viewport': await getFormattedViewportMetrics (tab),
                        },
                    'tool': 'crocoite-single', # not the name of the cli utility
                    'parameters': {
                        'url': self.url,
                        'idleTimeout': self.settings.idleTimeout,
                        'timeout': self.settings.timeout,
                        'behavior': list (map (attrgetter('name'), self._enabledBehavior)),
                        'insecure': self.settings.insecure,
                        },
                    }
            if self.warcinfo:
                payload['extra'] = self.warcinfo
            await self.processItem (ControllerStart (payload))

            await l.navigate (self.url)

            idleProc = asyncio.ensure_future (idle.wait (self.settings.idleTimeout))
            while True:
                try:
                    finished, pending = await asyncio.wait([idleProc, timeoutProc, handle],
                            return_when=asyncio.FIRST_COMPLETED)
                except asyncio.CancelledError:
                    idleProc.cancel ()
                    timeoutProc.cancel ()
                    break

                if handle in finished:
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
                    # idle timeout
                    logger.debug ('idle timeout',
                            uuid='90702590-94c4-44ef-9b37-02a16de444c3')
                    idleProc.result ()
                    timeoutProc.cancel ()
                    break

            await behavior.stop ()
            await tab.Page.stopLoading ()
            await asyncio.sleep (1)
            await behavior.finish ()

            # wait until loads from behavior scripts are done and browser is
            # idle for at least 1 second
            try:
                await asyncio.wait_for (idle.wait (1), timeout=1)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

            if handle.done ():
                handle.result ()
            else:
                handle.cancel ()

class SetEntry:
    """ A object, to be used with sets, that compares equality only on its
    primary property. """
    def __init__ (self, value, **props):
        self.value = value
        for k, v in props.items ():
            setattr (self, k, v)

    def __eq__ (self, b):
        assert isinstance (b, SetEntry)
        return self.value == b.value

    def __hash__ (self):
        return hash (self.value)

    def __repr__ (self):
        return f'<SetEntry {self.value!r}>'

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
        self.maxdepth = maxdepth

    def __call__ (self, urls):
        newurls = set ()
        for u in urls:
            if u.depth <= self.maxdepth:
                newurls.add (u)
        return newurls

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
        return set (filter (lambda u: str(u.value).startswith (str (self.prefix)), urls))

def hasTemplate (s):
    """ Return True if string s has string templates """
    return '{' in s and '}' in s

class RecursiveController:
    """
    Simple recursive controller

    Visits links acording to policy
    """

    __slots__ = ('url', 'output', 'command', 'logger', 'policy', 'have',
            'pending', 'stats', 'tempdir', 'running', 'concurrency',
            'copyLock')

    SCHEME_WHITELIST = {'http', 'https'}

    def __init__ (self, url, output, command, logger,
            tempdir=None, policy=DepthLimit (0), concurrency=1):
        self.url = url
        self.output = output
        self.command = command
        self.logger = logger.bind (context=type(self).__name__, seedurl=url)
        self.policy = policy
        self.tempdir = tempdir
        # A lock if only a single output file (no template) is requested
        self.copyLock = None if hasTemplate (output) else asyncio.Lock ()
        # some sanity checks. XXX move to argparse?
        if self.copyLock and os.path.exists (self.output):
                raise ValueError ('Output file exists')
        # tasks currently running
        self.running = set ()
        # max number of tasks running
        self.concurrency = concurrency
        # keep in sync with StatsHandler
        self.stats = {'requests': 0, 'finished': 0, 'failed': 0, 'bytesRcv': 0, 'crashed': 0, 'ignored': 0}

    async def fetch (self, entry, seqnum):
        """
        Fetch a single URL using an external command

        command is usually crocoite-single
        """

        assert isinstance (entry, SetEntry)

        url = entry.value
        depth = entry.depth
        logger = self.logger.bind (url=url)

        def formatCommand (e):
            # provide means to disable variable expansion
            if e.startswith ('!'):
                return e[1:]
            else:
                return e.format (url=url, dest=dest.name)

        def formatOutput (p):
            return p.format (host=url.host,
                    date=datetime.utcnow ().isoformat (), seqnum=seqnum)

        def logStats ():
            logger.info ('stats', uuid='24d92d16-770e-4088-b769-4020e127a7ff', **self.stats)

        if url.scheme not in self.SCHEME_WHITELIST:
            self.stats['ignored'] += 1
            logStats ()
            self.logger.warning ('scheme not whitelisted', url=url,
                    uuid='57e838de-4494-4316-ae98-cd3a2ebf541b')
            return

        dest = tempfile.NamedTemporaryFile (dir=self.tempdir,
                prefix=__package__, suffix='.warc.gz', delete=False)
        command = list (map (formatCommand, self.command))
        logger.info ('fetch', uuid='d1288fbe-8bae-42c8-af8c-f2fa8b41794f',
                command=command)
        try:
            process = await asyncio.create_subprocess_exec (*command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    stdin=asyncio.subprocess.DEVNULL,
                    start_new_session=True, limit=100*1024*1024)
            while True:
                data = await process.stdout.readline ()
                if not data:
                    break
                data = json.loads (data)
                uuid = data.get ('uuid')
                if uuid == '8ee5e9c9-1130-4c5c-88ff-718508546e0c':
                    links = set (self.policy (map (lambda x: SetEntry (URL(x).with_fragment(None), depth=depth+1), data.get ('links', []))))
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
                if self.copyLock is None:
                    # atomically move once finished
                    lastDestpath = None
                    while True:
                        # XXX: must generate a new name every time, otherwise
                        # this loop never terminates
                        destpath = formatOutput (self.output)
                        assert destpath != lastDestpath
                        lastDestpath = destpath

                        # python does not have rename(…, …, RENAME_NOREPLACE),
                        # but this is safe nontheless, since we’re
                        # single-threaded
                        if not os.path.exists (destpath):
                            # create the directory, so templates like
                            # /{host}/{date}/… are possible
                            os.makedirs (os.path.dirname (destpath), exist_ok=True)
                            os.rename (dest.name, destpath)
                            break
                else:
                    # atomically (in the context of this process) append to
                    # existing file
                    async with self.copyLock:
                        with open (dest.name, 'rb') as infd, \
                                open (self.output, 'ab') as outfd:
                            shutil.copyfileobj (infd, outfd)
                        os.unlink (dest.name)
            else:
                self.stats['crashed'] += 1
                logStats ()

    async def run (self):
        def log ():
            # self.have includes running jobs
            self.logger.info ('recursing',
                    uuid='5b8498e4-868d-413c-a67e-004516b8452c',
                    pending=len (self.pending),
                    have=len (self.have)-len(self.running),
                    running=len (self.running))

        seqnum = 1
        try:
            self.have = set ()
            self.pending = set ([SetEntry (self.url, depth=0)])

            while self.pending:
                # since pending is a set this picks a random item, which is fine
                u = self.pending.pop ()
                self.have.add (u)
                t = asyncio.ensure_future (self.fetch (u, seqnum))
                self.running.add (t)
                seqnum += 1

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
                    pending=len (self.pending),
                    have=len (self.have)-len (self.running),
                    running=len (self.running))
        finally:
            done = await asyncio.gather (*self.running,
                    return_exceptions=True)
            # propagate exceptions
            for r in done:
                if isinstance (r, Exception):
                    raise r
            self.running = set ()
            log ()

