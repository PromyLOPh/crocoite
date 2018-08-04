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

import pytest
from operator import itemgetter
from http.server import BaseHTTPRequestHandler
from pychrome.exceptions import TimeoutException

from .browser import Item, SiteLoader, ChromeService, NullService, BrowserCrashed
from .logger import Logger, Consumer

class TItem (Item):
    """ This should be as close to Item as possible """

    __slots__ = ('bodySend', '_body', '_requestBody')
    base = 'http://localhost:8000/'

    def __init__ (self, path, status, headers, bodyReceive, bodySend=None, requestBody=None):
        super ().__init__ (tab=None)
        self.chromeResponse = {'response': {'headers': headers, 'status': status, 'url': self.base + path}}
        self._body = bodyReceive, False
        self.bodySend = bodyReceive if not bodySend else bodySend
        self._requestBody = requestBody, False

    @property
    def body (self):
        return self._body

    @property
    def requestBody (self):
        return self._requestBody

testItems = [
    TItem ('binary', 200, {'Content-Type': 'application/octet-stream'}, b'\x00\x01\x02'),
    TItem ('attachment', 200, 
            {'Content-Type': 'text/plain; charset=utf-8',
            'Content-Disposition': 'attachment; filename="attachment.txt"',
            },
            'This is a simple text file with umlauts. ÄÖU.'.encode ('utf8')),
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
    TItem ('empty', 200, {}, b''),
    TItem ('redirect/301/empty', 301, {'Location': '/empty'}, b''),
    TItem ('redirect/301/redirect/301/empty', 301, {'Location': '/redirect/301/empty'}, b''),
    TItem ('nonexistent', 404, {}, b''),
    TItem ('html', 200, {'Content-Type': 'html'},
            '<html><body><img src="/image"><img src="/nonexistent"></body></html>'.encode ('utf8')),
    TItem ('html/alert', 200, {'Content-Type': 'html'},
            '<html><body><script>window.addEventListener("beforeunload", function (e) { e.returnValue = "bye?"; return e.returnValue; }); alert("stopping here"); if (confirm("are you sure?") || prompt ("42?")) { window.location = "/nonexistent"; }</script><img src="/image"></body></html>'.encode ('utf8')),
    TItem ('html/fetchPost', 200, {'Content-Type': 'html'},
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

class RequestHandler (BaseHTTPRequestHandler):
    def do_GET(self):
        item = testItemMap.get (self.path)
        if item:
            self.send_response (item.response['status'])
            for k, v in item.response['headers'].items ():
                self.send_header (k, v)
            body = item.bodySend
            self.send_header ('Content-Length', len (body))
            self.end_headers()
            self.wfile.write (body)
        return

    do_POST = do_GET

    def log_message (self, format, *args):
        pass

@pytest.fixture
def http ():
    def run ():
        import http.server
        PORT = 8000
        httpd = http.server.HTTPServer (("localhost", PORT), RequestHandler)
        print ('starting http server')
        httpd.serve_forever()

    from multiprocessing import Process
    p = Process (target=run)
    p.start ()
    yield p
    p.terminate ()
    p.join ()

class AssertConsumer (Consumer):
    def __call__ (self, **kwargs):
        assert 'uuid' in kwargs
        assert 'msg' in kwargs
        assert 'context' in kwargs

@pytest.fixture
def logger ():
    return Logger (consumer=[AssertConsumer ()])

@pytest.fixture
def loader (http, logger):
    def f (path):
        if path.startswith ('/'):
            path = 'http://localhost:8000{}'.format (path)
        return SiteLoader (browser, path, logger)
    print ('loader setup')
    with ChromeService () as browser:
        yield f
    print ('loader teardown')

def itemsLoaded (l, items):
    items = dict ([(i.parsedUrl.path, i) for i in items])
    timeout = 5
    while True:
        if not l.notify.wait (timeout) and len (items) > 0:
            assert False, 'timeout'
        if len (l.queue) > 0:
            item = l.queue.popleft ()
            if isinstance (item, Exception):
                raise item
            assert not item.failed
            assert item.chromeResponse is not None
            golden = items.pop (item.parsedUrl.path)
            if not golden:
                assert False, 'url {} not supposed to be fetched'.format (item.url)
            assert item.body[0] == golden.body[0]
            assert item.requestBody[0] == golden.requestBody[0]
            assert item.response['status'] == golden.response['status']
            assert item.statusText == BaseHTTPRequestHandler.responses.get (item.response['status'])[0]
            for k, v in golden.responseHeaders:
                actual = list (map (itemgetter (1), filter (lambda x: x[0] == k, item.responseHeaders)))
                assert v in actual

        # check queue at least once
        if not items:
            break

def literalItem (lf, item, deps=[]):
    with lf (item.parsedUrl.path) as l:
        l.start ()
        itemsLoaded (l, [item] + deps)

def test_empty (loader):
    literalItem (loader, testItemMap['/empty'])

def test_redirect (loader):
    literalItem (loader, testItemMap['/redirect/301/empty'], [testItemMap['/empty']])
    # chained redirects
    literalItem (loader, testItemMap['/redirect/301/redirect/301/empty'], [testItemMap['/redirect/301/empty'], testItemMap['/empty']])

def test_encoding (loader):
    """ Text responses are transformed to UTF-8. Make sure this works
    correctly. """
    for item in {testItemMap['/encoding/utf8'], testItemMap['/encoding/latin1'], testItemMap['/encoding/iso88591']}:
        literalItem (loader, item)

def test_binary (loader):
    """ Browser should ignore content it cannot display (i.e. octet-stream) """
    with loader ('/binary') as l:
        l.start ()
        itemsLoaded (l, [])

def test_image (loader):
    """ Images should be displayed inline """
    literalItem (loader, testItemMap['/image'])

def test_attachment (loader):
    """ And downloads won’t work in headless mode, even if it’s just a text file """
    with loader ('/attachment') as l:
        l.start ()
        itemsLoaded (l, [])

def test_html (loader):
    literalItem (loader, testItemMap['/html'], [testItemMap['/image'], testItemMap['/nonexistent']])
    # make sure alerts are dismissed correctly (image won’t load otherwise)
    literalItem (loader, testItemMap['/html/alert'], [testItemMap['/image']])

def test_post (loader):
    """ XHR POST request with binary data"""
    literalItem (loader, testItemMap['/html/fetchPost'],
            [testItemMap['/html/fetchPost/binary'],
            testItemMap['/html/fetchPost/binary/large'],
            testItemMap['/html/fetchPost/form'],
            testItemMap['/html/fetchPost/form/large']])

def test_crash (loader):
    with loader ('/html') as l:
        l.start ()
        try:
            l.tab.Page.crash (_timeout=1)
        except TimeoutException:
            pass
        q = l.queue
        assert isinstance (q.popleft (), BrowserCrashed)

def test_invalidurl (loader):
    url = 'http://nonexistent.example/'
    with loader (url) as l:
        l.start ()

        q = l.queue
        if not l.notify.wait (10):
            assert False, 'timeout'

        it = q.popleft ()
        assert it.failed

def test_nullservice ():
    """ Null service returns the url as is """

    url = 'http://localhost:12345'
    with NullService (url) as u:
        assert u == url

