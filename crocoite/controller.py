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

class ControllerSettings:
    __slots__ = ('maxBodySize', 'idleTimeout', 'timeout')

    def __init__ (self, maxBodySize=50*1024*1024, idleTimeout=2, timeout=10):
        self.maxBodySize = maxBodySize
        self.idleTimeout = idleTimeout
        self.timeout = timeout

    def toDict (self):
        return dict (maxBodySize=self.maxBodySize,
                idleTimeout=self.idleTimeout, timeout=self.timeout)

defaultSettings = ControllerSettings ()

class EventHandler:
    """ Abstract base class for event handler """

    __slots__ = ()

    # this handler wants to know about exceptions before they are reraised by
    # the controller
    acceptException = False

    def push (self, item):
        raise NotImplementedError ()

from .browser import BrowserCrashed

class StatsHandler (EventHandler):
    __slots__ = ('stats')

    acceptException = True

    def __init__ (self):
        self.stats = {'requests': 0, 'finished': 0, 'failed': 0, 'bytesRcv': 0, 'crashed': 0}

    def push (self, item):
        if isinstance (item, Item):
            self.stats['requests'] += 1
            if item.failed:
                self.stats['failed'] += 1
            else:
                self.stats['finished'] += 1
                self.stats['bytesRcv'] += item.encodedDataLength
        elif isinstance (item, BrowserCrashed):
            self.stats['crashed'] += 1

from .behavior import ExtractLinksEvent
from itertools import islice

class LogHandler (EventHandler):
    """ Handle items by logging information about them """

    __slots__ = ('logger')

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

import time, platform

from . import behavior as cbehavior
from .browser import ChromeService, SiteLoader, Item
from .util import getFormattedViewportMetrics, getRequirements

class ControllerStart:
    __slots__ = ('payload')

    def __init__ (self, payload):
        self.payload = payload

class SinglePageController:
    """
    Archive a single page url to file output.

    Dispatches between producer (site loader and behavior scripts) and consumer
    (stats, warc writer).
    """

    __slots__ = ('url', 'output', 'service', 'behavior', 'settings', 'logger', 'handler')

    def __init__ (self, url, output, logger, \
            service=ChromeService (), behavior=cbehavior.available, \
            settings=defaultSettings, handler=[]):
        self.url = url
        self.output = output
        self.service = service
        self.behavior = behavior
        self.settings = settings
        self.logger = logger.bind (context=type (self).__name__, url=url)
        self.handler = handler

    def processItem (self, item):
        if isinstance (item, Exception):
            for h in self.handler:
                if h.acceptException:
                    h.push (item)
            raise item

        for h in self.handler:
            h.push (item)

    def run (self):
        logger = self.logger
        def processQueue ():
            # XXX: this is very ugly code and does not work well. figure out a
            # better way to impose timeouts and still process all items in the
            # queue
            queue = l.queue
            logger.debug ('process queue',
                    uuid='dafbf76b-a37e-44db-a021-efb5593b81f8',
                    queuelen=len (queue))
            while True:
                now = time.time ()
                elapsed = now-start
                maxTimeout = max (min (self.settings.idleTimeout, self.settings.timeout-elapsed), 0)
                logger.debug ('timeout status',
                        uuid='49550447-37e3-49ff-9a73-34da1c3e5984',
                        maxTimeout=maxTimeout, elapsed=elapsed)
                # skip waiting if there is work to do. processes all items in
                # queue, regardless of timeouts, i.e. you need to make sure the
                # queue will actually be empty at some point.
                if len (queue) == 0:
                    if not l.notify.wait (maxTimeout):
                        assert len (queue) == 0, "event must be sent"
                        # timed out
                        logger.debug ('timeout',
                                uuid='6a7e0083-7c1a-45ba-b1ed-dbc4f26697c6',
                                elapsed=elapsed)
                        break
                    else:
                        l.notify.clear ()
                # limit number of items processed here, otherwise timeout won’t
                # be checked frequently. this can happen if the site quickly
                # loads a lot of items.
                for i in range (1000):
                    try:
                        item = queue.popleft ()
                        logger.debug ('queue pop',
                                uuid='adc96bfa-026d-4092-b732-4a022a1a92ca',
                                item=item, queuelen=len (queue))
                    except IndexError:
                        break
                    self.processItem (item)
                if maxTimeout == 0:
                    break

        with self.service as browser, SiteLoader (browser, self.url, logger=logger) as l:
            start = time.time ()

            version = l.tab.Browser.getVersion ()
            payload = {
                    'software': {
                        'platform': platform.platform (),
                        'python': {
                            'implementation': platform.python_implementation(),
                            'version': platform.python_version (),
                            'build': platform.python_build ()
                            },
                        'self': getRequirements (__package__)
                        },
                    'browser': {
                        'product': version['product'],
                        'useragent': version['userAgent'],
                        'viewport': getFormattedViewportMetrics (l.tab),
                        },
                    }
            self.processItem (ControllerStart (payload))

            # not all behavior scripts are allowed for every URL, filter them
            enabledBehavior = list (filter (lambda x: self.url in x,
                    map (lambda x: x (l, logger), self.behavior)))

            for b in enabledBehavior:
                # I decided against using the queue here to limit memory
                # usage (screenshot behavior would put all images into
                # queue before we could process them)
                for item in b.onload ():
                    self.processItem (item)
            l.start ()

            processQueue ()

            for b in enabledBehavior:
                for item in b.onstop ():
                    self.processItem (item)

            # if we stopped due to timeout, wait for remaining assets
            processQueue ()

            for b in enabledBehavior:
                for item in b.onfinish ():
                    self.processItem (item)

            processQueue ()

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

    __slots__ = ('maxdepth')

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
        return '<DepthLimit {}>'.format (self.maxdepth)

class PrefixLimit (RecursionPolicy):
    """
    Limit recursion by prefix
    
    i.e. prefix=http://example.com/foo
    ignored: http://example.com/bar http://offsite.example/foo
    accepted: http://example.com/foobar http://example.com/foo/bar
    """

    __slots__ = ('prefix')

    def __init__ (self, prefix):
        self.prefix = prefix

    def __call__ (self, urls):
        return set (filter (lambda u: u.startswith (self.prefix), urls))

import tempfile, asyncio, json, os
from datetime import datetime
from urllib.parse import urlparse
from .behavior import ExtractLinksEvent
from .util import removeFragment

class RecursiveController:
    """
    Simple recursive controller

    Visits links acording to policy
    """

    __slots__ = ('url', 'output', 'command', 'logger', 'policy', 'have',
            'pending', 'stats', 'prefix', 'tempdir', 'running', 'concurrency', '_quit')

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
        # initiate graceful shutdown
        self._quit = False

    async def fetch (self, url):
        """
        Fetch a single URL using an external command

        command is usually crocoite-grab
        """

        def formatCommand (e):
            return e.format (url=url, dest=dest.name)

        def formatPrefix (p):
            return p.format (host=urlparse (url).hostname, date=datetime.utcnow ().isoformat ())

        if urlparse (url).scheme not in self.SCHEME_WHITELIST:
            self.stats['ignored'] += 1
            self.logger.warning ('scheme not whitelisted', url=url,
                    uuid='57e838de-4494-4316-ae98-cd3a2ebf541b')
            return

        dest = tempfile.NamedTemporaryFile (dir=self.tempdir,
                prefix=formatPrefix (self.prefix), suffix='.warc.gz',
                delete=False)
        destpath = os.path.join (self.output, os.path.basename (dest.name))
        logger = self.logger.bind (url=url)
        command = list (map (formatCommand, self.command))
        logger.info ('fetch', uuid='1680f384-744c-4b8a-815b-7346e632e8db', command=command, destfile=destpath)
        process = await asyncio.create_subprocess_exec (*command, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, stdin=asyncio.subprocess.DEVNULL,
                start_new_session=True)
        while True:
            data = await process.stdout.readline ()
            if not data:
                break
            data = json.loads (data)
            uuid = data.get ('uuid')
            if uuid == '8ee5e9c9-1130-4c5c-88ff-718508546e0c':
                links = set (self.policy (map (removeFragment, data.get ('links', []))))
                links.difference_update (self.have)
                self.pending.update (links)
            elif uuid == '24d92d16-770e-4088-b769-4020e127a7ff':
                for k in self.stats.keys ():
                    self.stats[k] += data.get (k, 0)
                logger.info ('stats', uuid='24d92d16-770e-4088-b769-4020e127a7ff', **self.stats)
        code = await process.wait()
        # atomically move once finished
        os.rename (dest.name, destpath)

    def cancel (self):
        """ Gracefully cancel this job, waiting for existing workers to shut down """
        self.logger.info ('cancel',
                uuid='d58154c8-ec27-40f2-ab9e-e25c1b21cd88',
                pending=len (self.pending), have=len (self.have),
                running=len (self.running))
        self._quit = True

    async def run (self):
        def log ():
            self.logger.info ('recursing',
                    uuid='5b8498e4-868d-413c-a67e-004516b8452c',
                    pending=len (self.pending), have=len (self.have),
                    running=len (self.running))

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

        log ()

