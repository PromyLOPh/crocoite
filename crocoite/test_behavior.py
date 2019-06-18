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

import asyncio, os, yaml, re, math, struct
from functools import partial
from operator import attrgetter

import pytest
from yarl import URL
from aiohttp import web

import pkg_resources
from .logger import Logger
from .devtools import Process
from .behavior import Scroll, Behavior, ExtractLinks, ExtractLinksEvent, Crash, \
        Screenshot, ScreenshotEvent, DomSnapshot, DomSnapshotEvent, mapOrIgnore
from .controller import SinglePageController, EventHandler
from .devtools import Crashed

with pkg_resources.resource_stream (__name__, os.path.join ('data', 'click.yaml')) as fd:
    sites = list (yaml.safe_load_all (fd))
clickParam = []
for o in sites:
    for s in o['selector']:
        for u in s.get ('urls', []):
            clickParam.append ((u, s['selector']))

class ClickTester (Behavior):
    """
    Test adapter checking a given selector exists after loading the page
    """

    __slots__ = ('selector', )

    name = 'testclick'

    def __init__ (self, loader, logger, selector):
        super ().__init__ (loader, logger)
        self.selector = selector

    async def onfinish (self):
        tab = self.loader.tab
        results = await tab.DOM.getDocument ()
        rootNode = results['root']['nodeId']
        results = await tab.DOM.querySelectorAll (nodeId=rootNode, selector=self.selector)
        assert results['nodeIds'], self.selector

        # XXX: this is not true for every element we click. Github uses <button
        # type=submit> and <form> without an event listener on the <button>
#        # verify that an event listener exists
#        for nid in results['nodeIds']:
#            obj = (await tab.DOM.resolveNode (nodeId=nid))['object']
#            assert obj['type'] == 'object'
#            listeners = (await tab.DOMDebugger.getEventListeners (objectId=obj['objectId']))['listeners']
#            assert any (map (lambda x: x['type'] == 'click', listeners)), listeners

        return
        yield # pragma: no cover

@pytest.mark.parametrize("url,selector", clickParam)
@pytest.mark.asyncio
@pytest.mark.xfail(reason='depends on network access')
async def test_click_selectors (url, selector):
    """
    Make sure the CSS selector exists on an example url
    """
    logger = Logger ()
    # Some selectors are loaded dynamically and require scrolling
    controller = SinglePageController (url=url, logger=logger,
            service=Process (),
            behavior=[Scroll, partial(ClickTester, selector=selector)])
    await controller.run ()

matchParam = []
for o in sites:
    for s in o['selector']:
        for u in s.get ('urls', []):
            matchParam.append ((o['match'], URL (u)))

@pytest.mark.parametrize("match,url", matchParam)
@pytest.mark.asyncio
async def test_click_match (match, url):
    """ Test urls must match """
    # keep this aligned with click.js
    assert re.match (match, url.host, re.I)


class AccumHandler (EventHandler):
    """ Test adapter that accumulates all incoming items """
    __slots__ = ('data')

    def __init__ (self):
        super().__init__ ()
        self.data = []

    async def push (self, item):
        self.data.append (item)

async def simpleServer (url, response):
    async def f (req):
        return web.Response (body=response, status=200, content_type='text/html', charset='utf-8')

    app = web.Application ()
    app.router.add_route ('GET', url.path, f)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, url.host, url.port)
    await site.start()
    return runner

@pytest.mark.asyncio
async def test_extract_links ():
    """
    Make sure the CSS selector exists on an example url
    """

    url = URL.build (scheme='http', host='localhost', port=8080)
    runner = await simpleServer (url, """<html><head></head>
            <body>
            <div>
                <a href="/relative">foo</a>
                <a href="http://example.com/absolute/">foo</a>
                <a href="https://example.com/absolute/secure">foo</a>
                <a href="#anchor">foo</a>
                <a href="http://neue_preise_f%c3%bcr_zahnimplantate_k%c3%b6nnten_sie_%c3%bcberraschen">foo</a>

                <a href="/hidden/visibility" style="visibility: hidden">foo</a>
                <a href="/hidden/display" style="display: none">foo</a>
                <div style="display: none">
                <a href="/hidden/display/insidediv">foo</a>
                </div>
                <!--<a href="/hidden/comment">foo</a>-->

                <p><img src="shapes.png" usemap="#shapes">
                 <map name="shapes"><area shape=rect coords="50,50,100,100" href="/map/rect"></map></p>
            </div>
            </body></html>""")

    try:
        handler = AccumHandler ()
        logger = Logger ()
        controller = SinglePageController (url=url, logger=logger,
                service=Process (), behavior=[ExtractLinks], handler=[handler])
        await controller.run ()

        links = []
        for d in handler.data:
            if isinstance (d, ExtractLinksEvent):
                links.extend (d.links)
        assert sorted (links) == sorted ([
                url.with_path ('/relative'),
                url.with_fragment ('anchor'),
                URL ('http://example.com/absolute/'),
                URL ('https://example.com/absolute/secure'),
                url.with_path ('/hidden/visibility'), # XXX: shall we ignore these as well?
                url.with_path ('/map/rect'),
                ])
    finally:
        await runner.cleanup ()

@pytest.mark.asyncio
async def test_crash ():
    """
    Crashing through Behavior works?
    """

    url = URL.build (scheme='http', host='localhost', port=8080)
    runner = await simpleServer (url, '<html></html>')

    try:
        logger = Logger ()
        controller = SinglePageController (url=url, logger=logger,
                service=Process (), behavior=[Crash])
        with pytest.raises (Crashed):
            await controller.run ()
    finally:
        await runner.cleanup ()

@pytest.mark.asyncio
async def test_screenshot ():
    """
    Make sure screenshots are taken and have the correct dimensions. We can’t
    and don’t want to check their content.
    """
    # ceil(0) == 0, so starting with 1
    for expectHeight in (1, Screenshot.maxDim, Screenshot.maxDim+1, Screenshot.maxDim*2+Screenshot.maxDim//2):
        url = URL.build (scheme='http', host='localhost', port=8080)
        runner = await simpleServer (url, f'<html><body style="margin: 0; padding: 0;"><div style="height: {expectHeight}"></div></body></html>')

        try:
            handler = AccumHandler ()
            logger = Logger ()
            controller = SinglePageController (url=url, logger=logger,
                    service=Process (), behavior=[Screenshot], handler=[handler])
            await controller.run ()

            screenshots = list (filter (lambda x: isinstance (x, ScreenshotEvent), handler.data))
            assert len (screenshots) == math.ceil (expectHeight/Screenshot.maxDim)
            totalHeight = 0
            for s in screenshots:
                assert s.url == url
                # PNG ident is fixed, IHDR is always the first chunk
                assert s.data.startswith (b'\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR')
                width, height = struct.unpack ('>II', s.data[16:24])
                assert height <= Screenshot.maxDim
                totalHeight += height
            # screenshot height is at least canvas height (XXX: get hardcoded
            # value from devtools.Process)
            assert totalHeight == max (expectHeight, 1080)
        finally:
            await runner.cleanup ()

@pytest.mark.asyncio
async def test_dom_snapshot ():
    """
    Behavior plug-in works, <canvas> is replaced by static image, <script> is
    stripped. Actual conversion from Chrome DOM to HTML is validated by module
    .test_html
    """

    url = URL.build (scheme='http', host='localhost', port=8080)
    runner = await simpleServer (url, f'<html><body><p>ÄÖÜäöü</p><script>alert("yes");</script><canvas id="canvas" width="1" height="1">Alternate text.</canvas></body></html>')

    try:
        handler = AccumHandler ()
        logger = Logger ()
        controller = SinglePageController (url=url, logger=logger,
                service=Process (), behavior=[DomSnapshot], handler=[handler])
        await controller.run ()

        snapshots = list (filter (lambda x: isinstance (x, DomSnapshotEvent), handler.data))
        assert len (snapshots) == 1
        doc = snapshots[0].document
        assert doc.startswith ('<HTML><HEAD><meta charset=utf-8></HEAD><BODY><P>ÄÖÜäöü</P><IMG id=canvas width=1 height=1 src="data:image/png;base64,'.encode ('utf-8'))
        assert doc.endswith ('></BODY></HTML>'.encode ('utf-8'))
    finally:
        await runner.cleanup ()

def test_mapOrIgnore ():
    def fail (x):
        if x < 50:
            raise Exception ()
        return x+1

    assert list (mapOrIgnore (fail, range (100))) == list (range (51, 101))

