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
Behavior scripts (i.e. subclasses of Behavior) are a powerful method to
manipulate websites loaded into Chrome. They are executed by the controller
after the page started loading (onload), after it has been idle for a while
(onstop) and after loading was stopped (onfinish).

The script’s excercise their power either through DevTools API calls or by
injecting JavaScript into the page context. Thus they can manipulate both, the
browser itself (DevTools; modify resolution, get DOM snapshot) as well as the
page (JavaScript; trigger JavaScript events, call web API’s).

They also emit (yield) data processable by any consumer registered to the
controller. This allows storing captured screenshots inside WARC files, for
instance.
"""

import asyncio, json, os.path
from base64 import b64decode
from collections import OrderedDict
import pkg_resources

from html5lib.serializer import HTMLSerializer
from yarl import URL
import yaml

from .util import getFormattedViewportMetrics
from . import html
from .html import StripAttributeFilter, StripTagFilter, ChromeTreeWalker
from .devtools import Crashed, TabException

class Script:
    """ A JavaScript resource """

    __slots__ = ('path', 'data')
    datadir = 'data'

    def __init__ (self, path=None, encoding='utf-8'):
        self.path = path
        if path:
            self.data = pkg_resources.resource_string (__name__, os.path.join (self.datadir, path)).decode (encoding)

    def __repr__ (self):
        return f'<Script {self.path}>'

    def __str__ (self):
        return self.data

    @property
    def abspath (self):
        return pkg_resources.resource_filename (__name__,
                os.path.join (self.datadir, self.path))

    @classmethod
    def fromStr (cls, data, path=None):
        s = Script ()
        s.data = data
        s.path = path
        return s

class Behavior:
    __slots__ = ('loader', 'logger')

    # unique behavior name
    name = None

    def __init__ (self, loader, logger):
        assert self.name is not None
        self.loader = loader
        self.logger = logger.bind (context=type (self).__name__)

    def __contains__ (self, url):
        """
        Accept every URL by default
        """
        return True

    def __repr__ (self):
        return f'<Behavior {self.name}>'

    async def onload (self):
        """ After loading the page started """
        # this is a dirty hack to make this function an async generator
        return
        yield # pragma: no cover

    async def onstop (self):
        """ Before page loading is stopped """
        return
        yield # pragma: no cover

    async def onfinish (self):
        """ After the site has stopped loading """
        return
        yield # pragma: no cover

class JsOnload (Behavior):
    """ Execute JavaScript on page load """

    __slots__ = ('script', 'context', 'options')

    scriptPath = None

    def __init__ (self, loader, logger):
        super ().__init__ (loader, logger)
        self.script = Script (self.scriptPath)
        self.context = None
        # options passed to constructor
        self.options = {}

    async def onload (self):
        tab = self.loader.tab
        yield self.script

        # This is slightly awkward, since we cannot compile the class into an
        # objectId and then reference it. Therefore the script must return a
        # class constructor, which is then called with a generic options
        # parameter.
        # XXX: is there a better way to do this?
        result = await tab.Runtime.evaluate (expression=str (self.script))
        self.logger.debug ('behavior onload inject',
                uuid='a2da9b78-5648-44c5-bfa8-5c7573e13ad3', result=result)
        exception = result.get ('exceptionDetails', None)
        result = result['result']
        assert result['type'] == 'function', result
        assert result.get ('subtype') != 'error', exception
        constructor = result['objectId']

        if self.options:
            yield Script.fromStr (json.dumps (self.options, indent=2), f'{self.script.path}#options')
        result = await tab.Runtime.callFunctionOn (
                functionDeclaration='function(options){return new this(options);}',
                objectId=constructor,
                arguments=[{'value': self.options}])
        self.logger.debug ('behavior onload start',
                uuid='6c0605ae-93b3-46b3-b575-ba45790909a7', result=result)
        result = result['result']
        assert result['type'] == 'object', result
        assert result.get ('subtype') != 'error', result
        self.context = result['objectId']

    async def onstop (self):
        tab = self.loader.tab
        try:
            assert self.context is not None
            await tab.Runtime.callFunctionOn (functionDeclaration='function(){return this.stop();}',
                    objectId=self.context)
            await tab.Runtime.releaseObject (objectId=self.context)
        except TabException as e:
            # cannot do anything about that. Ignoring should be fine.
            self.logger.error ('jsonload onstop failed',
                    uuid='1786726f-c8ec-4f79-8769-30954d4e32f5',
                    exception=e.args,
                    objectId=self.context)

        return
        yield # pragma: no cover

### Generic scripts ###

class Scroll (JsOnload):
    name = 'scroll'
    scriptPath = 'scroll.js'

class EmulateScreenMetrics (Behavior):
    name = 'emulateScreenMetrics'

    async def onstop (self):
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
                ]
        l = self.loader
        tab = l.tab
        for s in sizes:
            self.logger.debug ('device override',
                    uuid='3d2d8096-1a75-4830-ad79-ae5f6f97071d', **s)
            await tab.Emulation.setDeviceMetricsOverride (**s)
            # give the browser time to re-eval page and start requests
            # XXX: should wait until loader is not busy any more
            await asyncio.sleep (1)
        self.logger.debug ('clear override',
                uuid='f9401683-eb3a-4b86-9bb2-c8c5d876fc8d')
        await tab.Emulation.clearDeviceMetricsOverride ()
        return
        yield # pragma: no cover

class DomSnapshotEvent:
    __slots__ = ('url', 'document', 'viewport')

    def __init__ (self, url, document, viewport):
        # XXX: document encoding?
        assert isinstance (document, bytes)

        self.url = url
        self.document = document
        self.viewport = viewport

class DomSnapshot (Behavior):
    """
    Get a DOM snapshot of tab and write it to WARC.

    We could use DOMSnapshot.getSnapshot here, but the API is not stable
    yet. Also computed styles are not really necessary here.
    """

    __slots__ = ('script', )

    name = 'domSnapshot'

    def __init__ (self, loader, logger):
        super ().__init__ (loader, logger)
        self.script = Script ('canvas-snapshot.js')

    async def onfinish (self):
        tab = self.loader.tab

        yield self.script
        await tab.Runtime.evaluate (expression=str (self.script), returnByValue=True)

        viewport = await getFormattedViewportMetrics (tab)
        dom = await tab.DOM.getDocument (depth=-1, pierce=True)
        self.logger.debug ('dom snapshot document',
                uuid='0c720784-8bd1-4fdc-a811-84394d753539', dom=dom)
        haveUrls = set ()
        for doc in ChromeTreeWalker (dom['root']).split ():
            url = URL (doc['documentURL'])
            if url in haveUrls:
                # ignore duplicate URLs. they are usually caused by
                # javascript-injected iframes (advertising) with no(?) src
                self.logger.warning ('dom snapshot duplicate',
                        uuid='d44de989-98d4-456e-82e7-9d4c49acab5e')
            elif url.scheme in ('http', 'https'):
                self.logger.debug ('dom snapshot',
                        uuid='ece7ff05-ccd9-44b5-b6a8-be25a24b96f4',
                        base=doc["baseURL"])
                haveUrls.add (url)
                walker = ChromeTreeWalker (doc)
                # remove script, to make the page static and noscript, because at the
                # time we took the snapshot scripts were enabled
                disallowedTags = ['script', 'noscript']
                disallowedAttributes = html.eventAttributes
                stream = StripAttributeFilter (StripTagFilter (walker, disallowedTags), disallowedAttributes)
                serializer = HTMLSerializer ()
                yield DomSnapshotEvent (url.with_fragment(None), serializer.render (stream, 'utf-8'), viewport)

class ScreenshotEvent:
    __slots__ = ('yoff', 'data', 'url')

    def __init__ (self, url, yoff, data):
        self.url = url
        self.yoff = yoff
        self.data = data

class Screenshot (Behavior):
    """
    Create screenshot from tab and write it to WARC
    """

    __slots__ = ('script')

    name = 'screenshot'

    # Hardcoded max texture size of 16,384 (crbug.com/770769)
    maxDim = 16*1024

    def __init__ (self, loader, logger):
        super ().__init__ (loader, logger)
        self.script = Script ('screenshot.js')

    async def onfinish (self):
        tab = self.loader.tab

        # for top-level/full-screen elements with position: fixed we need to
        # figure out their actual size (i.e. scrollHeight) and use that when
        # overriding the viewport size.
        # we could do this without javascript, but that would require several
        # round-trips to Chrome or pulling down the entire DOM+computed styles
        tab = self.loader.tab
        yield self.script
        result = await tab.Runtime.evaluate (expression=str (self.script), returnByValue=True)
        assert result['result']['type'] == 'object', result
        result = result['result']['value']

        # this is required to make the browser render more than just the small
        # actual viewport (i.e. entire page).  see
        # https://github.com/GoogleChrome/puppeteer/blob/45873ea737b4ebe4fa7d6f46256b2ea19ce18aa7/lib/Page.js#L805
        metrics = await tab.Page.getLayoutMetrics ()
        contentSize = metrics['contentSize']
        contentHeight = max (result + [contentSize['height']])

        override = {
                'width': 0,
                'height': 0,
                'deviceScaleFactor': 0,
                'mobile': False,
                'viewport': {'x': 0,
                    'y': 0,
                    'width': contentSize['width'],
                    'height': contentHeight,
                    'scale': 1}
                }
        self.logger.debug ('screenshot override',
                uuid='e0affa18-cbb1-4d97-9d13-9a88f704b1b2', override=override)
        await tab.Emulation.setDeviceMetricsOverride (**override)

        tree = await tab.Page.getFrameTree ()
        try:
            url = URL (tree['frameTree']['frame']['url']).with_fragment (None)
        except KeyError:
            self.logger.error ('frame without url',
                    uuid='edc2743d-b93e-4ba1-964e-db232f2f96ff', tree=tree)
            url = None

        width = min (contentSize['width'], self.maxDim)
        # we’re ignoring horizontal scroll intentionally. Most horizontal
        # layouts use JavaScript scrolling and don’t extend the viewport.
        for yoff in range (0, contentHeight, self.maxDim):
            height = min (contentHeight - yoff, self.maxDim)
            clip = {'x': 0, 'y': yoff, 'width': width, 'height': height, 'scale': 1}
            ret = await tab.Page.captureScreenshot (format='png', clip=clip)
            data = b64decode (ret['data'])
            yield ScreenshotEvent (url, yoff, data)

        await tab.Emulation.clearDeviceMetricsOverride ()

class Click (JsOnload):
    """ Generic link clicking """

    name = 'click'
    scriptPath = 'click.js'

    def __init__ (self, loader, logger):
        super ().__init__ (loader, logger)
        with pkg_resources.resource_stream (__name__, os.path.join ('data', 'click.yaml')) as fd:
            self.options['sites'] = list (yaml.safe_load_all (fd))

class ExtractLinksEvent:
    __slots__ = ('links', )

    def __init__ (self, links):
        self.links = links

    def __repr__ (self):
        return f'<ExtractLinksEvent {self.links!r}>'

def mapOrIgnore (f, l):
    for e in l:
        try:
            yield f (e)
        except:
            pass

class ExtractLinks (Behavior):
    """
    Extract links from a page using JavaScript
    
    We could retrieve a HTML snapshot and extract links here, but we’d have to
    manually resolve relative links.
    """

    __slots__ = ('script', )

    name = 'extractLinks'

    def __init__ (self, loader, logger):
        super ().__init__ (loader, logger)
        self.script = Script ('extract-links.js')

    async def onfinish (self):
        tab = self.loader.tab
        yield self.script
        result = await tab.Runtime.evaluate (expression=str (self.script), returnByValue=True)
        yield ExtractLinksEvent (list (set (mapOrIgnore (URL, result['result']['value']))))

class Crash (Behavior):
    """ Crash the browser. For testing only. Obviously. """

    name = 'crash'

    async def onstop (self):
        try:
            await self.loader.tab.Page.crash ()
        except Crashed:
            pass
        return
        yield # pragma: no cover

# available behavior scripts. Order matters, move those modifying the page
# towards the end of available
available = [Scroll, Click, ExtractLinks, Screenshot, EmulateScreenMetrics, DomSnapshot]
#available.append (Crash)
# order matters, since behavior can modify the page (dom snapshots, for instance)
availableMap = OrderedDict (map (lambda x: (x.name, x), available))

