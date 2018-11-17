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

import logging
import asyncio
import pytest
from operator import itemgetter
from aiohttp import web
from http.server import BaseHTTPRequestHandler

from .browser import Item, SiteLoader
from .logger import Logger, Consumer, JsonPrintConsumer
from .devtools import Crashed, Process

# if you want to know what’s going on:
#logging.basicConfig(level=logging.DEBUG)

class TItem (Item):
    """ This should be as close to Item as possible """

    __slots__ = ('bodySend', '_body', '_requestBody')
    base = 'http://localhost:8000/'

    def __init__ (self, path, status, headers, bodyReceive, bodySend=None, requestBody=None, failed=False, isRedirect=False):
        super ().__init__ (tab=None)
        self.chromeResponse = {'response': {'headers': headers, 'status': status, 'url': self.base + path}}
        self.body = bodyReceive, False
        self.bodySend = bodyReceive if not bodySend else bodySend
        self.requestBody = requestBody, False
        self.failed = failed
        self.isRedirect = isRedirect

testItems = [
    TItem ('binary', 200, {'Content-Type': 'application/octet-stream'}, b'\x00\x01\x02', failed=True),
    TItem ('attachment', 200, 
            {'Content-Type': 'text/plain; charset=utf-8',
            'Content-Disposition': 'attachment; filename="attachment.txt"',
            },
            'This is a simple text file with umlauts. ÄÖU.'.encode ('utf8'), failed=True),
    TItem ('encoding/utf8', 200, {'Content-Type': 'text/plain; charset=utf-8'},
            'This is a test, äöü μνψκ ¥¥¥¿ýý¡'.encode ('utf8')),
    TItem ('encoding/iso88591', 200, {'Content-Type': 'text/plain; charset=ISO-8859-1'},
            'This is a test, äöü.'.encode ('utf8'),
            'This is a test, äöü.'.encode ('ISO-8859-1')),
    TItem ('encoding/latin1', 200, {'Content-Type': 'text/plain; charset=latin1'},
            'This is a test, äöü.'.encode ('utf8'),
            'This is a test, äöü.'.encode ('latin1')),
    TItem ('image', 200, {'Content-Type': 'image/png'},
            # 1×1 png image
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x00\x00\x00\x00:~\x9bU\x00\x00\x00\nIDAT\x08\x1dc\xf8\x0f\x00\x01\x01\x01\x006_g\x80\x00\x00\x00\x00IEND\xaeB`\x82'),
    TItem ('empty', 200, {'Content-Type': 'text/plain'}, b''),
    TItem ('headers/duplicate', 200, [('Content-Type', 'text/plain'), ('Duplicate', '1'), ('Duplicate', '2')], b''),
    TItem ('headers/fetch/req', 200, {'Content-Type': 'text/plain'}, b''),
    TItem ('headers/fetch/html', 200, {'Content-Type': 'text/html'},
            r"""<html><body><script>
            let h = new Headers([["custom", "1"]]);
            fetch("/headers/fetch/req", {"method": "GET", "headers": h}).then(x => console.log("done"));
            </script></body></html>""".encode ('utf8')),
    TItem ('redirect/301/empty', 301, {'Location': '/empty'}, b'', isRedirect=True),
    TItem ('redirect/301/redirect/301/empty', 301, {'Location': '/redirect/301/empty'}, b'', isRedirect=True),
    TItem ('nonexistent', 404, {}, b''),
    TItem ('html', 200, {'Content-Type': 'text/html'},
            '<html><body><img src="/image"><img src="/nonexistent"></body></html>'.encode ('utf8')),
    TItem ('html/alert', 200, {'Content-Type': 'text/html'},
            '<html><body><script>window.addEventListener("beforeunload", function (e) { e.returnValue = "bye?"; return e.returnValue; }); alert("stopping here"); if (confirm("are you sure?") || prompt ("42?")) { window.location = "/nonexistent"; }</script><script>document.write(\'<img src="/image">\');</script></body></html>'.encode ('utf8')),
    TItem ('html/fetchPost', 200, {'Content-Type': 'text/html'},
            r"""<html><body><script>
            let a = fetch("/html/fetchPost/binary", {"method": "POST", "body": "\x00"});
            let b = fetch("/html/fetchPost/form", {"method": "POST", "body": new URLSearchParams({"data": "!"})});
            let c = fetch("/html/fetchPost/binary/large", {"method": "POST", "body": "\x00".repeat(100*1024)});
            let d = fetch("/html/fetchPost/form/large", {"method": "POST", "body": new URLSearchParams({"data": "!".repeat(100*1024)})});
            </script></body></html>""".encode ('utf8')),
    TItem ('html/fetchPost/binary', 200, {'Content-Type': 'application/octet-stream'}, b'\x00', requestBody=b'\x00'),
    TItem ('html/fetchPost/form', 200, {'Content-Type': 'application/octet-stream'}, b'\x00', requestBody=b'data=%21'),
    # XXX: these should trigger the need for getRequestPostData, but they don’t. oh well.
    TItem ('html/fetchPost/binary/large', 200, {'Content-Type': 'application/octet-stream'}, b'\x00', requestBody=(100*1024)*b'\x00'),
    TItem ('html/fetchPost/form/large', 200, {'Content-Type': 'application/octet-stream'}, b'\x00', requestBody=b'data=' + (100*1024)*b'%21'),
    ]
testItemMap = dict ([(item.parsedUrl.path, item) for item in testItems])

def itemToResponse (item):
    async def f (req):
        headers = item.response['headers']
        return web.Response(body=item.bodySend, status=item.response['status'],
                headers=headers)
    return f

@pytest.fixture
async def server ():
    """ Simple HTTP server for testing notifications """
    import logging
    logging.basicConfig(level=logging.DEBUG)
    app = web.Application(debug=True)
    for item in testItems:
        app.router.add_route ('*', item.parsedUrl.path, itemToResponse (item))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', 8080)
    await site.start()
    yield app
    await runner.cleanup ()

class AssertConsumer (Consumer):
    def __call__ (self, **kwargs):
        assert 'uuid' in kwargs
        assert 'msg' in kwargs
        assert 'context' in kwargs
        return kwargs

@pytest.fixture
def logger ():
    return Logger (consumer=[AssertConsumer ()])

@pytest.fixture
async def loader (server, logger):
    def f (path):
        if path.startswith ('/'):
            path = 'http://localhost:8080{}'.format (path)
        return SiteLoader (browser, path, logger)
    async with Process () as browser:
        yield f

async def itemsLoaded (l, items):
    items = dict ([(i.parsedUrl.path, i) for i in items])
    async for item in l:
        assert item.chromeResponse is not None
        golden = items.pop (item.parsedUrl.path)
        if not golden:
            assert False, 'url {} not supposed to be fetched'.format (item.url)
        assert item.failed == golden.failed
        if item.failed:
            # response will be invalid if request failed
            if not items:
                break
            else:
                continue
        assert item.isRedirect == golden.isRedirect
        if golden.isRedirect:
            assert item.body is None
        else:
            assert item.body[0] == golden.body[0]
        assert item.requestBody[0] == golden.requestBody[0]
        assert item.response['status'] == golden.response['status']
        assert item.statusText == BaseHTTPRequestHandler.responses.get (item.response['status'])[0]
        for k, v in golden.responseHeaders:
            actual = list (map (itemgetter (1), filter (lambda x: x[0] == k, item.responseHeaders)))
            assert v in actual
        
        # we’re done when everything has been loaded
        if not items:
            break

async def literalItem (lf, item, deps=[]):
    async with lf (item.parsedUrl.path) as l:
        await l.start ()
        await asyncio.wait_for (itemsLoaded (l, [item] + deps), timeout=30)

@pytest.mark.asyncio
async def test_empty (loader):
    await literalItem (loader, testItemMap['/empty'])

@pytest.mark.asyncio
async def test_headers_duplicate (loader):
    """
    Some headers, like Set-Cookie can be present multiple times. Chrome
    separates these with a newline.
    """
    async with loader ('/headers/duplicate') as l:
        await l.start ()
        async for it in l:
            if it.parsedUrl.path == '/headers/duplicate':
                assert not it.failed
                dup = list (filter (lambda x: x[0] == 'Duplicate', it.responseHeaders))
                assert len(dup) == 2
                assert list(sorted(map(itemgetter(1), dup))) == ['1', '2']
                break

@pytest.mark.asyncio
async def test_headers_req (loader):
    """
    Custom request headers. JavaScript’s Headers() does not support duplicate
    headers, so we can’t generate those.
    """
    async with loader ('/headers/fetch/html') as l:
        await l.start ()
        async for it in l:
            if it.parsedUrl.path == '/headers/fetch/req':
                assert not it.failed
                dup = list (filter (lambda x: x[0] == 'custom', it.requestHeaders))
                assert len(dup) == 1
                assert list(sorted(map(itemgetter(1), dup))) == ['1']
                break

@pytest.mark.asyncio
async def test_redirect (loader):
    await literalItem (loader, testItemMap['/redirect/301/empty'], [testItemMap['/empty']])
    # chained redirects
    await literalItem (loader, testItemMap['/redirect/301/redirect/301/empty'], [testItemMap['/redirect/301/empty'], testItemMap['/empty']])

@pytest.mark.asyncio
async def test_encoding (loader):
    """ Text responses are transformed to UTF-8. Make sure this works
    correctly. """
    for item in {testItemMap['/encoding/utf8'], testItemMap['/encoding/latin1'], testItemMap['/encoding/iso88591']}:
        await literalItem (loader, item)

@pytest.mark.asyncio
async def test_binary (loader):
    """ Browser should ignore content it cannot display (i.e. octet-stream) """
    await literalItem (loader, testItemMap['/binary'])

@pytest.mark.asyncio
async def test_image (loader):
    """ Images should be displayed inline """
    await literalItem (loader, testItemMap['/image'])

@pytest.mark.asyncio
async def test_attachment (loader):
    """ And downloads won’t work in headless mode, even if it’s just a text file """
    await literalItem (loader, testItemMap['/attachment'])

@pytest.mark.asyncio
async def test_html (loader):
    await literalItem (loader, testItemMap['/html'], [testItemMap['/image'], testItemMap['/nonexistent']])
    # make sure alerts are dismissed correctly (image won’t load otherwise)
    await literalItem (loader, testItemMap['/html/alert'], [testItemMap['/image']])

@pytest.mark.asyncio
async def test_post (loader):
    """ XHR POST request with binary data"""
    await literalItem (loader, testItemMap['/html/fetchPost'],
            [testItemMap['/html/fetchPost/binary'],
            testItemMap['/html/fetchPost/binary/large'],
            testItemMap['/html/fetchPost/form'],
            testItemMap['/html/fetchPost/form/large']])

@pytest.mark.asyncio
async def test_crash (loader):
    async with loader ('/html') as l:
        await l.start ()
        with pytest.raises (Crashed):
            await l.tab.Page.crash ()

@pytest.mark.asyncio
async def test_invalidurl (loader):
    url = 'http://nonexistent.example/'
    async with loader (url) as l:
        await l.start ()
        async for it in l:
            assert it.failed
            break

