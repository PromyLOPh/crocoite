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
    __slots__ = ('logBuffer', 'maxBodySize', 'idleTimeout', 'timeout')

    def __init__ (self, logBuffer=1000, maxBodySize=50*1024*1024, idleTimeout=2, timeout=10):
        self.logBuffer = logBuffer
        self.maxBodySize = maxBodySize
        self.idleTimeout = idleTimeout
        self.timeout = timeout

    def toDict (self):
        return dict (logBuffer=self.logBuffer, maxBodySize=self.maxBodySize,
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

import logging, time
from urllib.parse import urlsplit, urlunsplit

from . import behavior as cbehavior
from .browser import ChromeService, SiteLoader, Item
from .util import getFormattedViewportMetrics

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

    def __init__ (self, url, output, service=ChromeService (), behavior=cbehavior.available, \
            logger=logging.getLogger(__name__), settings=defaultSettings, handler=[]):
        self.url = url
        self.output = output
        self.service = service
        self.behavior = behavior
        self.settings = settings
        self.logger = logger
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
        def processQueue ():
            # XXX: this is very ugly code and does not work well. figure out a
            # better way to impose timeouts and still process all items in the
            # queue
            queue = l.queue
            self.logger.debug ('processing at least {} queue items'.format (len (queue)))
            while True:
                now = time.time ()
                elapsed = now-start
                maxTimeout = max (min (self.settings.idleTimeout, self.settings.timeout-elapsed), 0)
                self.logger.debug ('max timeout is {} with elapsed {}'.format (maxTimeout, elapsed))
                # skip waiting if there is work to do. processes all items in
                # queue, regardless of timeouts, i.e. you need to make sure the
                # queue will actually be empty at some point.
                if len (queue) == 0:
                    if not l.notify.wait (maxTimeout):
                        assert len (queue) == 0, "event must be sent"
                        # timed out
                        self.logger.debug ('timed out after {}'.format (elapsed))
                        break
                    else:
                        l.notify.clear ()
                # limit number of items processed here, otherwise timeout won’t
                # be checked frequently. this can happen if the site quickly
                # loads a lot of items.
                for i in range (1000):
                    try:
                        item = queue.popleft ()
                        self.logger.debug ('queue pop: {!r}, len now {}'.format (item, len (queue)))
                    except IndexError:
                        break
                    self.processItem (item)
                if maxTimeout == 0:
                    break

        with self.service as browser, SiteLoader (browser, self.url, logger=self.logger) as l:
            start = time.time ()

            version = l.tab.Browser.getVersion ()
            payload = {
                    'software': __package__,
                    'browser': version['product'],
                    'useragent': version['userAgent'],
                    'viewport': getFormattedViewportMetrics (l.tab),
                    }
            self.processItem (ControllerStart (payload))

            # not all behavior scripts are allowed for every URL, filter them
            enabledBehavior = list (filter (lambda x: self.url in x,
                    map (lambda x: x (l), self.behavior)))

            for b in enabledBehavior:
                self.logger.debug ('starting onload {}'.format (b))
                # I decided against using the queue here to limit memory
                # usage (screenshot behavior would put all images into
                # queue before we could process them)
                for item in b.onload ():
                    self.processItem (item)
            l.start ()

            processQueue ()

            for b in enabledBehavior:
                self.logger.debug ('starting onstop {}'.format (b))
                for item in b.onstop ():
                    self.processItem (item)

            # if we stopped due to timeout, wait for remaining assets
            processQueue ()

            for b in enabledBehavior:
                self.logger.debug ('starting onfinish {}'.format (b))
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
    
    depth==0 means no recursion, depth==1 is the page and outgoing links, …
    """

    __slots__ = ('maxdepth')

    def __init__ (self, maxdepth=0):
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

def removeFragment (u):
    """ Remove fragment from url (i.e. #hashvalue) """
    s = urlsplit (u)
    return urlunsplit ((s.scheme, s.netloc, s.path, s.query, ''))

from .behavior import ExtractLinksEvent

class RecursiveController (EventHandler):
    """
    Simple recursive controller

    Visits links acording to recursionPolicy
    """

    __slots__ = ('url', 'output', 'service', 'behavior', 'settings', 'logger',
            'recursionPolicy', 'handler', 'urls', 'have')

    def __init__ (self, url, output, service=ChromeService (), behavior=cbehavior.available, \
            logger=logging.getLogger(__name__), settings=defaultSettings,
            recursionPolicy=DepthLimit (0), handler=[]):
        self.url = url
        self.output = output
        self.service = service
        self.behavior = behavior
        self.settings = settings
        self.logger = logger
        self.recursionPolicy = recursionPolicy
        self.handler = handler
        self.handler.append (self)

    def fetch (self, urls):
        """
        Overrideable fetch action for URLs. Defaults to sequential
        SinglePageController.
        """
        for u in urls:
            try:
                c = SinglePageController (u, self.output, self.service,
                        self.behavior, self.logger, self.settings, self.handler)
                c.run ()
            except BrowserCrashed:
                # this is fine if reported
                self.logger.error ('browser crashed for {}'.format (u))

    def run (self):
        self.have = set ()
        self.urls = set ([self.url])

        while self.urls:
            self.logger.info ('retrieving {} urls'.format (len (self.urls)))

            self.have.update (self.urls)
            fetchurls = self.urls
            self.urls = set ()

            # handler appends new urls to self.urls through push()
            self.fetch (fetchurls)

            # remove urls we have and apply recursion policy
            self.urls.difference_update (self.have)
            self.urls = self.recursionPolicy (self.urls)

    def push (self, item):
        if isinstance (item, ExtractLinksEvent):
            self.logger.debug ('adding extracted links: {}'.format (item.links))
            self.urls.update (map (removeFragment, item.links))
        else:
            self.logger.debug ('{} got unhandled event {!r}'.format (self, item))

