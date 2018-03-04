# Copyright (c) 2017 crocoite contributors
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
Generic and per-site behavior scripts
"""

import logging
from io import BytesIO
from urllib.parse import urlsplit
import os.path
import pkg_resources
from base64 import b64decode

from .util import randomString, packageUrl, getFormattedViewportMetrics
from . import html
from .html import StripAttributeFilter, StripTagFilter, ChromeTreeWalker
from html5lib.serializer import HTMLSerializer
from warcio.statusandheaders import StatusAndHeaders

logger = logging.getLogger(__name__)

class Behavior:
    # unique behavior name
    name = None

    def __init__ (self, loader):
        assert self.name is not None
        self.loader = loader

    def __contains__ (self, url):
        """
        Accept every URL by default
        """
        return True

    def loadScript (self, path, encoding='utf-8'):
        return pkg_resources.resource_string (__name__, os.path.join ('data', path)).decode (encoding)

    def useScript (self, script, encoding='utf-8'):
        writer = self.loader.writer
        record = writer.create_warc_record (packageUrl ('script'), 'metadata',
                payload=BytesIO (script.encode (encoding)),
                warc_headers_dict={'Content-Type': 'application/javascript; charset={}'.format (encoding)})
        writer.write_record (record)

    def onload (self):
        """ Before loading the page """
        pass

    def onstop (self):
        """ Before page loading is stopped """
        pass

    def onfinish (self):
        """ After the site has stopped loading """
        pass

class HostnameFilter:
    """ Limit behavior script to hostname """

    hostname = None

    def __contains__ (self, url):
        url = urlsplit (url)
        hostname = url.hostname.split ('.')[::-1]
        return hostname[:2] == self.hostname

class JsOnload (Behavior):
    """ Execute JavaScript on page load """

    scriptPath = None

    def __init__ (self, loader):
        super ().__init__ (loader)
        self.script = self.loadScript (self.scriptPath)
        self.scriptHandle = None

    def onload (self):
        self.useScript (self.script)
        self.scriptHandle = self.loader.tab.Page.addScriptToEvaluateOnNewDocument (source=self.script)['identifier']

    def onstop (self):
        self.loader.tab.Page.removeScriptToEvaluateOnNewDocument (identifier=self.scriptHandle)

### Generic scripts ###

class Scroll (JsOnload):
    name = 'scroll'
    scriptPath = 'scroll.js'

    def __init__ (self, loader):
        super ().__init__ (loader)
        stopVarname = '__' + __package__ + '_stop__'
        newStopVarname = randomString ()
        self.script = self.script.replace (stopVarname, newStopVarname)
        self.stopVarname = newStopVarname

    def onstop (self):
        super ().onstop ()
        # removing the script does not stop it if running
        script = '{} = true; window.scrollTo (0, 0);'.format (self.stopVarname)
        self.useScript (script)
        self.loader.tab.Runtime.evaluate (expression=script, returnByValue=True)

class EmulateScreenMetrics (Behavior):
    name = 'emulateScreenMetrics'

    def onstop (self):
        """
        Emulate different screen sizes, causing the site to fetch assets (img
        srcset and css, for example) for different screen resolutions.
        """
        cssPpi = 96
        sizes = [
                {'width': 1920, 'height': 1080, 'deviceScaleFactor': 1.5, 'mobile': False},
                {'width': 1920, 'height': 1080, 'deviceScaleFactor': 2, 'mobile': False},
                # very dense display
                {'width': 1920, 'height': 1080, 'deviceScaleFactor': 4, 'mobile': False},
                # just a few samples:
                # 1st gen iPhone (portrait mode)
                {'width': 320, 'height': 480, 'deviceScaleFactor': 163/cssPpi, 'mobile': True},
                # 6th gen iPhone (portrait mode)
                {'width': 750, 'height': 1334, 'deviceScaleFactor': 326/cssPpi, 'mobile': True},
                # and reset
                {'width': 1920, 'height': 1080, 'deviceScaleFactor': 1, 'mobile': False},
                ]
        l = self.loader
        tab = l.tab
        for s in sizes:
            tab.Emulation.setDeviceMetricsOverride (**s)
            # give the browser time to re-eval page and start requests
            l.wait (1)
        # XXX: this seems to be broken, it does not clear the override
        #tab.Emulation.clearDeviceMetricsOverride ()

class DomSnapshot (Behavior):
    """
    Get a DOM snapshot of tab and write it to WARC.

    We could use DOMSnapshot.getSnapshot here, but the API is not stable
    yet. Also computed styles are not really necessary here.

    XXX: Currently writes a response, when it should use “resource”. pywb
    can’t handle that though.
    """

    name = 'domSnapshot'

    def __init__ (self, loader):
        super ().__init__ (loader)
        self.script = self.loadScript ('canvas-snapshot.js')

    def onfinish (self):
        tab = self.loader.tab
        writer = self.loader.writer

        self.useScript (self.script)
        tab.Runtime.evaluate (expression=self.script, returnByValue=True)

        viewport = getFormattedViewportMetrics (tab)
        dom = tab.DOM.getDocument (depth=-1, pierce=True)
        haveUrls = set ()
        for doc in ChromeTreeWalker (dom['root']).split ():
            rawUrl = doc['documentURL']
            if rawUrl in haveUrls:
                # ignore duplicate URLs. they are usually caused by
                # javascript-injected iframes (advertising) with no(?) src
                logger.warning ('have DOM snapshot for URL {}, ignoring'.format (rawUrl))
                continue
            url = urlsplit (rawUrl)
            if url.scheme in ('http', 'https'):
                logger.debug ('saving DOM snapshot for url {}, base {}'.format (doc['documentURL'], doc['baseURL']))
                haveUrls.add (rawUrl)
                walker = ChromeTreeWalker (doc)
                # remove script, to make the page static and noscript, because at the
                # time we took the snapshot scripts were enabled
                disallowedTags = ['script', 'noscript']
                disallowedAttributes = html.eventAttributes
                stream = StripAttributeFilter (StripTagFilter (walker, disallowedTags), disallowedAttributes)
                serializer = HTMLSerializer ()
                httpHeaders = StatusAndHeaders('200 OK', {}, protocol='HTTP/1.1')
                record = writer.create_warc_record (doc['documentURL'], 'response',
                        payload=BytesIO (serializer.render (stream, 'utf-8')),
                        http_headers=httpHeaders,
                        warc_headers_dict={'X-DOM-Snapshot': str (True),
                                'X-Chrome-Viewport': viewport})
                writer.write_record (record)

class Screenshot (Behavior):
    """
    Create screenshot from tab and write it to WARC
    """

    name = 'screenshot'

    def onfinish (self):
        tab = self.loader.tab
        writer = self.loader.writer

        viewport = getFormattedViewportMetrics (tab)
        data = b64decode (tab.Page.captureScreenshot (format='png')['data'])
        record = writer.create_warc_record (packageUrl ('screenshot.png'), 'resource',
                payload=BytesIO (data), warc_headers_dict={'Content-Type': 'image/png',
                'X-Chrome-Viewport': viewport})
        writer.write_record (record)

### Site-specific scripts ###

class Twitter (HostnameFilter, JsOnload):
    name = 'twitter'
    scriptPath = 'per-site/twitter.js'
    hostname = ['com', 'twitter']

# available behavior scripts. Order matters, move those modifying the page
# towards the end of available
generic = [Scroll, EmulateScreenMetrics]
perSite = [Twitter]
available = generic + perSite + [Screenshot, DomSnapshot]
availableNames = set (map (lambda x: x.name, available))

