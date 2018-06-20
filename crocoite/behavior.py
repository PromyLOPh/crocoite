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

import logging, time
from io import BytesIO
from urllib.parse import urlsplit
import os.path
import pkg_resources
from base64 import b64decode
from collections import OrderedDict

from html5lib.serializer import HTMLSerializer
from warcio.statusandheaders import StatusAndHeaders
from pychrome.exceptions import TimeoutException

from .util import randomString, packageUrl, getFormattedViewportMetrics
from . import html
from .html import StripAttributeFilter, StripTagFilter, ChromeTreeWalker
from .browser import SiteLoader

logger = logging.getLogger(__name__)

class Script:
    """ A JavaScript resource """

    __slots__ = ('path', 'data')

    def __init__ (self, path=None, encoding='utf-8'):
        self.path = path
        if path:
            self.data = pkg_resources.resource_string (__name__, os.path.join ('data', path)).decode (encoding)

    def __repr__ (self):
        return '<Script {}>'.format (self.path)

    def __str__ (self):
        return self.data

    @classmethod
    def fromStr (cls, data):
        s = Script ()
        s.data = data
        return s

class Behavior:
    __slots__ = ('loader')

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

    def __repr__ (self):
        return '<Behavior {}>'.format (self.name)

    def onload (self):
        """ Before loading the page """
        yield from ()

    def onstop (self):
        """ Before page loading is stopped """
        yield from ()

    def onfinish (self):
        """ After the site has stopped loading """
        yield from ()

class HostnameFilter:
    """ Limit behavior script to hostname """

    hostname = None

    def __contains__ (self, url):
        url = urlsplit (url)
        hostname = url.hostname.split ('.')[::-1]
        return hostname[:2] == self.hostname

class JsOnload (Behavior):
    """ Execute JavaScript on page load """

    __slots__ = ('script', 'scriptHandle')

    scriptPath = None

    def __init__ (self, loader):
        super ().__init__ (loader)
        self.script = Script (self.scriptPath)
        self.scriptHandle = None

    def onload (self):
        yield self.script
        self.scriptHandle = self.loader.tab.Page.addScriptToEvaluateOnNewDocument (source=str (self.script))['identifier']

    def onstop (self):
        self.loader.tab.Page.removeScriptToEvaluateOnNewDocument (identifier=self.scriptHandle)
        yield from ()

### Generic scripts ###

class Scroll (JsOnload):
    __slots__ = ('stopVarname')

    name = 'scroll'
    scriptPath = 'scroll.js'

    def __init__ (self, loader):
        super ().__init__ (loader)
        stopVarname = '__' + __package__ + '_stop__'
        newStopVarname = randomString ()
        self.script.data = self.script.data.replace (stopVarname, newStopVarname)
        self.stopVarname = newStopVarname

    def onstop (self):
        super ().onstop ()
        # removing the script does not stop it if running
        script = Script.fromStr ('{} = true; window.scrollTo (0, 0);'.format (self.stopVarname))
        yield script
        self.loader.tab.Runtime.evaluate (expression=str (script), returnByValue=True)

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
            time.sleep (1)
        # XXX: this seems to be broken, it does not clear the override
        #tab.Emulation.clearDeviceMetricsOverride ()
        yield from ()

class DomSnapshotEvent:
    __slots__ = ('url', 'document', 'viewport')

    def __init__ (self, url, document, viewport):
        self.url = url
        self.document = document
        self.viewport = viewport

class DomSnapshot (Behavior):
    """
    Get a DOM snapshot of tab and write it to WARC.

    We could use DOMSnapshot.getSnapshot here, but the API is not stable
    yet. Also computed styles are not really necessary here.

    XXX: Currently writes a response, when it should use “resource”. pywb
    can’t handle that though.
    """

    __slots__ = ('script')

    name = 'domSnapshot'

    def __init__ (self, loader):
        super ().__init__ (loader)
        self.script = Script ('canvas-snapshot.js')

    def onfinish (self):
        tab = self.loader.tab

        yield self.script
        tab.Runtime.evaluate (expression=str (self.script), returnByValue=True)

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
                yield DomSnapshotEvent (doc['documentURL'], serializer.render (stream, 'utf-8'), viewport)

class ScreenshotEvent:
    __slots__ = ('yoff', 'data')

    def __init__ (self, yoff, data):
        self.yoff = yoff
        self.data = data

class Screenshot (Behavior):
    """
    Create screenshot from tab and write it to WARC
    """

    name = 'screenshot'

    def onfinish (self):
        tab = self.loader.tab

        # see https://github.com/GoogleChrome/puppeteer/blob/230be28b067b521f0577206899db01f0ca7fc0d2/examples/screenshots-longpage.js
        # Hardcoded max texture size of 16,384 (crbug.com/770769)
        maxDim = 16*1024
        metrics = tab.Page.getLayoutMetrics ()
        contentSize = metrics['contentSize']
        width = min (contentSize['width'], maxDim)
        # we’re ignoring horizontal scroll intentionally. Most horizontal
        # layouts use JavaScript scrolling and don’t extend the viewport.
        for yoff in range (0, contentSize['height'], maxDim):
            height = min (contentSize['height'] - yoff, maxDim)
            clip = {'x': 0, 'y': yoff, 'width': width, 'height': height, 'scale': 1}
            data = b64decode (tab.Page.captureScreenshot (format='png', clip=clip)['data'])
            yield ScreenshotEvent (yoff, data)

class Click (JsOnload):
    """ Generic link clicking """

    name = 'click'
    scriptPath = 'click.js'

class ExtractLinksEvent:
    __slots__ = ('links')

    def __init__ (self, links):
        self.links = links

class ExtractLinks (Behavior):
    """
    Extract links from a page using JavaScript
    
    We could retrieve a HTML snapshot and extract links here, but we’d have to
    manually resolve relative links.
    """

    __slots__ = ('script')

    name = 'extractLinks'

    def __init__ (self, loader):
        super ().__init__ (loader)
        self.script = Script ('extract-links.js')

    def onfinish (self):
        tab = self.loader.tab
        yield self.script
        result = tab.Runtime.evaluate (expression=str (self.script), returnByValue=True)
        yield ExtractLinksEvent (list (set (result['result']['value'])))

class Crash (Behavior):
    """ Crash the browser. For testing only. Obviously. """

    name = 'crash'

    def onstop (self):
        try:
            self.loader.tab.Page.crash (_timeout=1)
        except TimeoutException:
            pass
        yield from ()

# available behavior scripts. Order matters, move those modifying the page
# towards the end of available
generic = [Scroll, EmulateScreenMetrics, Click, ExtractLinks]
perSite = []
available = generic + perSite + [Screenshot, DomSnapshot]
#available.append (Crash)
# order matters, since behavior can modify the page (dom snapshots, for instance)
availableMap = OrderedDict (map (lambda x: (x.name, x), available))

