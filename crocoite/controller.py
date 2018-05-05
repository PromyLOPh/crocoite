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
    def __init__ (self, logBuffer=1000, maxBodySize=50*1024*1024, idleTimeout=2, timeout=10):
        self.logBuffer = logBuffer
        self.maxBodySize = maxBodySize
        self.idleTimeout = idleTimeout
        self.timeout = timeout

    def toDict (self):
        return dict (logBuffer=self.logBuffer, maxBodySize=self.maxBodySize,
                idleTimeout=self.idleTimeout, timeout=self.timeout)

defaultSettings = ControllerSettings ()

import logging
from urllib.parse import urlsplit, urlunsplit

import pychrome

from . import behavior as cbehavior
from .browser import ChromeService
from .warc import WarcLoader, SerializingWARCWriter
from .util import getFormattedViewportMetrics

def firstOrNone (it):
    """ Return first item of iterator it or None if empty """
    try:
        return next (it)
    except StopIteration:
        return None

class SinglePageController:
    """
    Archive a single page url to file output.
    """

    def __init__ (self, url, output, service=ChromeService (), behavior=cbehavior.available, \
            logger=logging.getLogger(__name__), settings=defaultSettings):
        self.url = url
        self.output = output
        self.service = service
        self.behavior = behavior
        self.settings = settings
        self.logger = logger

    def run (self):
        ret = {'stats': None, 'links': []}

        with self.service as browser:
            browser = pychrome.Browser (url=browser)
            writer = SerializingWARCWriter (self.output, gzip=True)

            with WarcLoader (browser, self.url, writer,
                    logBuffer=self.settings.logBuffer,
                    maxBodySize=self.settings.maxBodySize) as l:
                version = l.tab.Browser.getVersion ()
                payload = {
                        'software': __package__,
                        'browser': version['product'],
                        'useragent': version['userAgent'],
                        'viewport': getFormattedViewportMetrics (l.tab),
                        }
                warcinfo = writer.create_warcinfo_record (filename=None, info=payload)
                writer.write_record (warcinfo)

                # not all behavior scripts are allowed for every URL, filter them
                enabledBehavior = list (filter (lambda x: self.url in x,
                        map (lambda x: x (l), self.behavior)))
                linksBehavior = firstOrNone (filter (lambda x: isinstance (x, cbehavior.ExtractLinks),
                        enabledBehavior))

                for b in enabledBehavior:
                    self.logger.debug ('starting onload behavior {}'.format (b.name))
                    b.onload ()
                l.start ()

                l.waitIdle (self.settings.idleTimeout, self.settings.timeout)

                for b in enabledBehavior:
                    self.logger.debug ('starting onstop behavior {}'.format (b.name))
                    b.onstop ()

                # if we stopped due to timeout, wait for remaining assets
                l.waitIdle (2, 60)
                l.stop ()

                for b in enabledBehavior:
                    self.logger.debug ('starting onfinish behavior {}'.format (b.name))
                    b.onfinish ()

                ret['stats'] = l.stats
                ret['links'] = linksBehavior.links if linksBehavior else None
            writer.flush ()
        return ret

from collections import UserDict

class IntegerDict (UserDict):
    """ Dict with dict/dict per-item arithmetic propagation, i.e. {1: 2}+{1: 1}={1: 3} """
    def __add__ (self, b):
        newdict = self.__class__ (self)
        for k, v in b.items ():
            if k in self:
                newdict[k] += v
            else:
                newdict[k] = v
        return newdict

class RecursionPolicy:
    """ Abstract recursion policy """
    def __call__ (self, urls):
        raise NotImplementedError

class DepthLimit (RecursionPolicy):
    """
    Limit recursion by depth.
    
    depth==0 means no recursion, depth==1 is the page and outgoing links, …
    """
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
    def __init__ (self, prefix):
        self.prefix = prefix

    def __call__ (self, urls):
        return set (filter (lambda u: u.startswith (self.prefix), urls))

def removeFragment (u):
    """ Remove fragment from url (i.e. #hashvalue) """
    s = urlsplit (u)
    return urlunsplit ((s.scheme, s.netloc, s.path, s.query, ''))

class RecursiveController:
    """
    Simple recursive controller

    Visits links acording to recursionPolicy
    """

    def __init__ (self, url, output, service=ChromeService (), behavior=cbehavior.available, \
            logger=logging.getLogger(__name__), settings=defaultSettings,
            recursionPolicy=DepthLimit (0)):
        self.url = url
        self.output = output
        self.service = service
        self.behavior = behavior
        self.settings = settings
        self.logger = logger
        self.recursionPolicy = recursionPolicy

    def fetch (self, urls):
        """
        Overrideable fetch action for URLs. Defaults to sequential
        SinglePageController.
        """
        result = []
        for u in urls:
            c = SinglePageController (u, self.output, self.service,
                    self.behavior, self.logger, self.settings)
            result.append (c.run ())
        return result

    def run (self):
        have = set ()
        urls = set ([self.url])
        ret = {'stats': IntegerDict ()}

        while urls:
            self.logger.info ('retrieving {} urls'.format (len (urls)))
            result = self.fetch (urls)

            have.update (urls)
            urls = set ()
            for r in result:
                ret['stats'] += r['stats']
                urls.update (map (removeFragment, r['links']))
            urls.difference_update (have)

            urls = self.recursionPolicy (urls)
        # everything in ret must be serializeable
        ret['stats'] = dict (ret['stats'])
        return ret

