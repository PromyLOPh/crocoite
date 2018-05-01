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

defaultSettings = ControllerSettings ()

import logging

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
        ret = {'stats': None, 'links': None}

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

