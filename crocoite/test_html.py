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

import asyncio
import pytest, html5lib
from html5lib.serializer import HTMLSerializer
from html5lib.treewalkers import getTreeWalker
from aiohttp import web

from .html import StripTagFilter, StripAttributeFilter, ChromeTreeWalker
from .test_devtools import tab, browser

def test_strip_tag ():
    d = html5lib.parse ('<a>barbaz<b>foobar</b>.</a><b>foobar</b>.<b attr=1><c></c>')
    stream = StripTagFilter (getTreeWalker ('etree')(d), ['b', 'c'])
    serializer = HTMLSerializer ()
    assert serializer.render (stream) == '<a>barbaz.</a>.'

def test_strip_attribute ():
    d = html5lib.parse ('<a b=1 c="yes" d></a><br b=2 c="no" d keep=1>')
    stream = StripAttributeFilter (getTreeWalker ('etree')(d), ['b', 'c', 'd'])
    serializer = HTMLSerializer ()
    assert serializer.render (stream) == '<a></a><br keep=1>'

@pytest.mark.asyncio
async def test_treewalker (tab):
    frames = await tab.Page.getFrameTree ()

    framehtml = '<HTML><HEAD></HEAD><BODY></BODY></HTML>'
    html = '<HTML><HEAD><META charset=utf-8></HEAD><BODY><H1>Hello</H1><!-- comment --><IFRAME></IFRAME></BODY></HTML>'
    rootframe = frames['frameTree']['frame']['id']
    await tab.Page.setDocumentContent (frameId=rootframe, html=html)

    dom = await tab.DOM.getDocument (depth=-1, pierce=True)
    docs = list (ChromeTreeWalker (dom['root']).split ())
    assert len(docs) == 2
    for i, doc in enumerate (docs):
        walker = ChromeTreeWalker (doc)
        serializer = HTMLSerializer ()
        result = serializer.render (iter(walker))
        if i == 0:
            assert result == html
        elif i == 1:
            assert result == framehtml

cdataDoc = '<test><![CDATA[Hello world]]></test>'
xmlHeader = '<?xml version="1.0" encoding="UTF-8"?>'
async def hello(request):
    return web.Response(text=xmlHeader + cdataDoc, content_type='text/xml')

@pytest.fixture
async def server ():
    """ Simple HTTP server for testing notifications """
    app = web.Application()
    app.add_routes([web.get('/test.xml', hello)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', 8080)
    await site.start()
    yield app
    await runner.cleanup ()

@pytest.mark.asyncio
async def test_treewalker_cdata (tab, server):
    ret = await tab.Page.navigate (url='http://localhost:8080/test.xml')
    # wait until loaded XXX: replace with idle check
    await asyncio.sleep (0.5)
    dom = await tab.DOM.getDocument (depth=-1, pierce=True)
    docs = list (ChromeTreeWalker (dom['root']).split ())
    assert len(docs) == 1
    for i, doc in enumerate (docs):
        walker = ChromeTreeWalker (doc)
        serializer = HTMLSerializer ()
        result = serializer.render (iter(walker))
        # chrome will display a pretty-printed viewer *plus* the original
        # source (stripped of its xml header)
        assert cdataDoc in result


