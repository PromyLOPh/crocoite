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
Chrome browser interactions.
"""

import logging
from urllib.parse import urlsplit
from base64 import b64decode
from http.server import BaseHTTPRequestHandler
from collections import deque
from threading import Event

import pychrome

class Item:
    """
    Simple wrapper containing Chrome request and response
    """

    __slots__ = ('tab', 'chromeRequest', 'chromeResponse', 'chromeFinished',
            'isRedirect', 'failed')

    def __init__ (self, tab):
        self.tab = tab
        self.chromeRequest = None
        self.chromeResponse = None
        self.chromeFinished = None
        self.isRedirect = False
        self.failed = False

    def __repr__ (self):
        return '<Item {}>'.format (self.request['url'])

    @property
    def request (self):
        return self.chromeRequest['request']

    @property
    def response (self):
        assert not self.failed, "you must not access response if failed is set"
        return self.chromeResponse['response']

    @property
    def initiator (self):
        return self.chromeRequest['initiator']

    @property
    def id (self):
        return self.chromeRequest['requestId']

    @property
    def encodedDataLength (self):
        return self.chromeFinished['encodedDataLength']

    @property
    def url (self):
        return self.response['url']

    @property
    def parsedUrl (self):
        return urlsplit (self.url)

    @property
    def body (self):
        """ Return response body or None """
        try:
            body = self.tab.Network.getResponseBody (requestId=self.id, _timeout=10)
            rawBody = body['body']
            base64Encoded = body['base64Encoded']
            if base64Encoded:
                rawBody = b64decode (rawBody)
            else:
                rawBody = rawBody.encode ('utf8')
            return rawBody, base64Encoded
        except (pychrome.exceptions.CallMethodException, pychrome.exceptions.TimeoutException):
            raise ValueError ('Cannot fetch response body')

    @property
    def requestBody (self):
        """ Get request/POST body """
        req = self.request
        postData = req.get ('postData')
        if postData:
            return postData.encode ('utf8'), False
        elif req.get ('hasPostData', False):
            try:
                return b64decode (self.tab.Network.getRequestPostData (requestId=self.id, _timeout=10)['postData']), True
            except (pychrome.exceptions.CallMethodException, pychrome.exceptions.TimeoutException):
                raise ValueError ('Cannot fetch request body')
        return None, False

    @property
    def requestHeaders (self):
        # the response object may contain refined headers, which were
        # *actually* sent over the wire
        return self._unfoldHeaders (self.response.get ('requestHeaders', self.request['headers']))

    @property
    def responseHeaders (self):
        return self._unfoldHeaders (self.response['headers'])

    @property
    def statusText (self):
        text = self.response.get ('statusText')
        if text:
            return text
        text = BaseHTTPRequestHandler.responses.get (self.response['status'])
        if text:
            return text[0]
        return 'No status text available'

    @staticmethod
    def _unfoldHeaders (headers):
        """
        A host may send multiple headers using the same key, which Chrome folds
        into the same item. Separate those.
        """
        items = []
        for k in headers.keys ():
            for v in headers[k].split ('\n'):
                items.append ((k, v))
        return items

    def setRequest (self, req):
        self.chromeRequest = req

    def setResponse (self, resp):
        self.chromeResponse = resp

    def setFinished (self, finished):
        self.chromeFinished = finished

class BrowserCrashed (Exception):
    pass

class SiteLoader:
    """
    Load site in Chrome and monitor network requests

    Chrome’s raw devtools events are preprocessed here (asynchronously, in a
    different thread, spawned by pychrome) and put into a deque. There
    are two reasons for this: First of all, it makes consumer exception
    handling alot easier (no need to propagate them to the main thread). And
    secondly, browser crashes must be handled before everything else, as they
    result in a loss of communication with the browser itself (i.e. we can’t
    fetch a resource’s body any more).

    XXX: track popup windows/new tabs and close them
    """

    __slots__ = ('requests', 'browser', 'url', 'logger', 'queue', 'notify', 'tab')
    allowedSchemes = {'http', 'https'}

    def __init__ (self, browser, url, logger=logging.getLogger(__name__)):
        self.requests = {}
        self.browser = pychrome.Browser (url=browser)
        self.url = url
        self.logger = logger
        self.queue = deque ()
        self.notify = Event ()

    def __enter__ (self):
        tab = self.tab = self.browser.new_tab()
        # setup callbacks
        tab.Network.requestWillBeSent = self._requestWillBeSent
        tab.Network.responseReceived = self._responseReceived
        tab.Network.loadingFinished = self._loadingFinished
        tab.Network.loadingFailed = self._loadingFailed
        tab.Log.entryAdded = self._entryAdded
        tab.Page.javascriptDialogOpening = self._javascriptDialogOpening
        tab.Inspector.targetCrashed = self._targetCrashed

        # start the tab
        tab.start()

        # enable events
        tab.Log.enable ()
        tab.Network.enable()
        tab.Page.enable ()
        tab.Inspector.enable ()
        tab.Network.clearBrowserCache ()
        if tab.Network.canClearBrowserCookies ()['result']:
            tab.Network.clearBrowserCookies ()

        return self

    def __exit__ (self, exc_type, exc_value, traceback):
        self.tab.Page.stopLoading ()
        self.tab.stop ()
        self.browser.close_tab(self.tab)
        return False

    def __len__ (self):
        return len (self.requests)

    def __iter__ (self):
        return iter (self.queue)

    def start (self):
        self.tab.Page.navigate(url=self.url)

    # use event to signal presence of new items. This way the controller
    # can wait for them without polling.
    def _append (self, item):
        self.queue.append (item)
        self.notify.set ()

    def _appendleft (self, item):
        self.queue.appendleft (item)
        self.notify.set ()

    # internal chrome callbacks
    def _requestWillBeSent (self, **kwargs):
        reqId = kwargs['requestId']
        req = kwargs['request']

        url = urlsplit (req['url'])
        if url.scheme not in self.allowedSchemes:
            return

        item = self.requests.get (reqId)
        if item:
            # redirects never “finish” loading, but yield another requestWillBeSent with this key set
            redirectResp = kwargs.get ('redirectResponse')
            if redirectResp:
                # create fake responses
                resp = {'requestId': reqId, 'response': redirectResp, 'timestamp': kwargs['timestamp']}
                item.setResponse (resp)
                resp = {'requestId': reqId, 'encodedDataLength': 0, 'timestamp': kwargs['timestamp']}
                item.setFinished (resp)
                item.isRedirect = True
                self.logger.info ('redirected request {} has url {}'.format (reqId, req['url']))
                self._append (item)
            else:
                self.logger.warning ('request {} already exists, overwriting.'.format (reqId))

        item = Item (self.tab)
        item.setRequest (kwargs)
        self.requests[reqId] = item

    def _responseReceived (self, **kwargs):
        reqId = kwargs['requestId']
        item = self.requests.get (reqId)
        if item is None:
            return

        resp = kwargs['response']
        url = urlsplit (resp['url'])
        if url.scheme in self.allowedSchemes:
            self.logger.info ('response {} {}'.format (reqId, resp['url']))
            item.setResponse (kwargs)
        else:
            self.logger.warning ('response: ignoring scheme {}'.format (url.scheme))

    def _loadingFinished (self, **kwargs):
        """
        Item was fully loaded. For some items the request body is not available
        when responseReceived is fired, thus move everything here.
        """
        reqId = kwargs['requestId']
        item = self.requests.pop (reqId, None)
        if item is None:
            # we never recorded this request (blacklisted scheme, for example)
            return
        req = item.request
        resp = item.response
        assert req['url'] == resp['url'], 'req and resp urls are not the same {} vs {}'.format (req['url'], resp['url'])
        url = urlsplit (resp['url'])
        if url.scheme in self.allowedSchemes:
            self.logger.info ('finished {} {}'.format (reqId, req['url']))
            item.setFinished (kwargs)
            self._append (item)

    def _loadingFailed (self, **kwargs):
        reqId = kwargs['requestId']
        self.logger.warning ('failed {} {}'.format (reqId, kwargs['errorText'], kwargs.get ('blockedReason')))
        item = self.requests.pop (reqId, None)
        item.failed = True
        self._append (item)

    def _entryAdded (self, **kwargs):
        """ Log entry added """
        entry = kwargs['entry']
        level = {'verbose': logging.DEBUG, 'info': logging.INFO,
                'warning': logging.WARNING,
                'error': logging.ERROR}[entry['level']]
        self.logger.log (level, 'console: {}: {}'.format (entry['source'], entry['text']), extra={'raw': entry})

    def _javascriptDialogOpening (self, **kwargs):
        t = kwargs.get ('type')
        if t in {'alert', 'confirm', 'prompt'}:
            self.logger.info ('javascript opened a dialog: {}, {}, canceling'.format (t, kwargs.get ('message')))
            self.tab.Page.handleJavaScriptDialog (accept=False)
        elif t == 'beforeunload':
            # we must accept this one, otherwise the page will not unload/close
            self.logger.info ('javascript opened a dialog: {}, {}, procceeding'.format (t, kwargs.get ('message')))
            self.tab.Page.handleJavaScriptDialog (accept=True)
        else:
            self.logger.warning ('unknown javascript dialog type {}'.format (t))

    def _targetCrashed (self, **kwargs):
        self.logger.error ('browser crashed')
        # priority message
        self._appendleft (BrowserCrashed ())

import subprocess, os, time
from tempfile import mkdtemp
import socket, shutil

class ChromeService:
    """
    Start Chrome with socket activation (i.e. pass listening socket). Polling
    is not required with this method, since reads will block until Chrome is
    ready.
    """

    __slots__ = ('binary', 'windowSize', 'p', 'userDataDir')

    def __init__ (self, binary='google-chrome-stable', windowSize=(1920, 1080)):
        self.binary = binary
        self.windowSize = windowSize
        self.p = None

    def __enter__ (self):
        assert self.p is None
        self.userDataDir = mkdtemp ()
        args = [self.binary,
                '--window-size={},{}'.format (*self.windowSize),
                '--user-data-dir={}'.format (self.userDataDir), # use temporory user dir
                '--no-default-browser-check',
                '--no-first-run', # don’t show first run screen
                '--disable-breakpad', # no error reports
                '--disable-extensions',
                '--disable-infobars',
                '--disable-notifications', # no libnotify
                '--headless',
                '--disable-gpu',
                '--hide-scrollbars', # hide scrollbars on screenshots
                '--mute-audio', # don’t play any audio
                '--remote-debugging-port=0', # pick a port. XXX: we may want to use --remote-debugging-pipe instead
                '--homepage=about:blank',
                'about:blank']
        # start new session, so ^C does not affect subprocess
        self.p = subprocess.Popen (args, start_new_session=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
        port = None
        # chrome writes its current active devtools port to a file. due to the
        # sleep() this is rather ugly, but should work with all versions of the
        # browser.
        for i in range (100):
            try:
                with open (os.path.join (self.userDataDir, 'DevToolsActivePort'), 'r') as fd:
                    port = int (fd.readline ().strip ())
                    break
            except FileNotFoundError:
                time.sleep (0.2)
        if port is None:
            raise Exception ('Chrome died on us.')

        return 'http://localhost:{}'.format (port)

    def __exit__ (self, *exc):
        self.p.terminate ()
        self.p.wait ()
        shutil.rmtree (self.userDataDir)
        self.p = None

class NullService:
    __slots__ = ('url')

    def __init__ (self, url):
        self.url = url

    def __enter__ (self):
        return self.url

    def __exit__ (self, *exc):
        pass

### tests ###

import unittest, time
from operator import itemgetter

class TestItem (Item):
    """ This should be as close to Item as possible """

    __slots__ = ('bodySend', '_body')
    base = 'http://localhost:8000/'

    def __init__ (self, path, status, headers, bodyReceive, bodySend=None):
        super ().__init__ (tab=None)
        self.chromeResponse = {'response': {'headers': headers, 'status': status, 'url': self.base + path}}
        self._body = bodyReceive, False
        self.bodySend = bodyReceive if not bodySend else bodySend

    @property
    def body (self):
        return self._body

testItems = [
    TestItem ('binary', 200, {'Content-Type': 'application/octet-stream'}, b'\x00\x01\x02'),
    TestItem ('attachment', 200, 
            {'Content-Type': 'text/plain; charset=utf-8',
            'Content-Disposition': 'attachment; filename="attachment.txt"',
            },
            'This is a simple text file with umlauts. ÄÖU.'.encode ('utf8')),
    TestItem ('encoding/utf8', 200, {'Content-Type': 'text/plain; charset=utf-8'},
            'This is a test, äöü μνψκ ¥¥¥¿ýý¡'.encode ('utf8')),
    TestItem ('encoding/iso88591', 200, {'Content-Type': 'text/plain; charset=ISO-8859-1'},
            'This is a test, äöü.'.encode ('utf8'),
            'This is a test, äöü.'.encode ('ISO-8859-1')),
    TestItem ('encoding/latin1', 200, {'Content-Type': 'text/plain; charset=latin1'},
            'This is a test, äöü.'.encode ('utf8'),
            'This is a test, äöü.'.encode ('latin1')),
    TestItem ('image', 200, {'Content-Type': 'image/png'},
            # 1×1 png image
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x00\x00\x00\x00:~\x9bU\x00\x00\x00\nIDAT\x08\x1dc\xf8\x0f\x00\x01\x01\x01\x006_g\x80\x00\x00\x00\x00IEND\xaeB`\x82'),
    TestItem ('empty', 200, {}, b''),
    TestItem ('redirect/301/empty', 301, {'Location': '/empty'}, b''),
    TestItem ('redirect/301/redirect/301/empty', 301, {'Location': '/redirect/301/empty'}, b''),
    TestItem ('nonexistent', 404, {}, b''),
    TestItem ('html', 200, {'Content-Type': 'html'},
            '<html><body><img src="/image"><img src="/nonexistent"></body></html>'.encode ('utf8')),
    TestItem ('html/alert', 200, {'Content-Type': 'html'},
            '<html><body><script>window.addEventListener("beforeunload", function (e) { e.returnValue = "bye?"; return e.returnValue; }); alert("stopping here"); if (confirm("are you sure?") || prompt ("42?")) { window.location = "/nonexistent"; }</script><img src="/image"></body></html>'.encode ('utf8')),
    ]
testItemMap = dict ([(item.parsedUrl.path, item) for item in testItems])

class TestHTTPRequestHandler (BaseHTTPRequestHandler):
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
            
    def log_message (self, format, *args):
        pass

def startServer ():
    import http.server
    PORT = 8000
    httpd = http.server.HTTPServer (("localhost", PORT), TestHTTPRequestHandler)
    httpd.serve_forever()

class TestSiteLoader (unittest.TestCase):
    __slots__ = ('server', 'baseurl', 'service', 'browser')

    def setUp (self):
        from multiprocessing import Process
        self.server = Process (target=startServer)
        self.server.start ()
        self.baseurl = 'http://localhost:8000'
        self.service = ChromeService ()
        self.browser = self.service.__enter__ ()

    def buildAdapter (self, path):
        self.assertTrue (path.startswith ('/'))
        return SiteLoader (self.browser, '{}{}'.format (self.baseurl, path))

    def assertItems (self, l, items):
        items = dict ([(i.parsedUrl.path, i) for i in items])
        timeout = 5
        while True:
            if not l.notify.wait (timeout) and len (items) > 0:
                self.fail ('timeout')
            if len (l.queue) > 0:
                item = l.queue.popleft ()
                if isinstance (item, Exception):
                    raise item
                self.assertIsNot (item.chromeResponse, None, msg='url={}'.format (item.request['url']))
                golden = items.pop (item.parsedUrl.path)
                if not golden:
                    self.fail ('url {} not supposed to be fetched'.format (item.url))
                self.assertEqual (item.body[0], golden.body[0], msg='body for url={}'.format (item.request['url']))
                self.assertEqual (item.response['status'], golden.response['status'])
                for k, v in golden.responseHeaders:
                    actual = list (map (itemgetter (1), filter (lambda x: x[0] == k, item.responseHeaders)))
                    self.assertIn (v, actual)

            # check queue at least once
            if not items:
                break

    def assertLiteralItem (self, item, deps=[]):
        with self.buildAdapter (item.parsedUrl.path) as l:
            l.start ()
            self.assertItems (l, [item] + deps)

    def test_empty (self):
        self.assertLiteralItem (testItemMap['/empty'])

    def test_redirect (self):
        self.assertLiteralItem (testItemMap['/redirect/301/empty'], [testItemMap['/empty']])
        # chained redirects
        self.assertLiteralItem (testItemMap['/redirect/301/redirect/301/empty'], [testItemMap['/redirect/301/empty'], testItemMap['/empty']])

    def test_encoding (self):
        """ Text responses are transformed to UTF-8. Make sure this works
        correctly. """
        for item in {testItemMap['/encoding/utf8'], testItemMap['/encoding/latin1'], testItemMap['/encoding/iso88591']}:
            self.assertLiteralItem (item)

    def test_binary (self):
        """ Browser should ignore content it cannot display (i.e. octet-stream) """
        with self.buildAdapter ('/binary') as l:
            l.start ()
            self.assertItems (l, [])

    def test_image (self):
        """ Images should be displayed inline """
        self.assertLiteralItem (testItemMap['/image'])

    def test_attachment (self):
        """ And downloads won’t work in headless mode, even if it’s just a text file """
        with self.buildAdapter ('/attachment') as l:
            l.start ()
            self.assertItems (l, [])

    def test_html (self):
        self.assertLiteralItem (testItemMap['/html'], [testItemMap['/image'], testItemMap['/nonexistent']])
        # make sure alerts are dismissed correctly (image won’t load otherwise)
        self.assertLiteralItem (testItemMap['/html/alert'], [testItemMap['/image']])

    def tearDown (self):
        self.service.__exit__ (None, None, None)
        self.server.terminate ()
        self.server.join ()

if __name__ == '__main__':
    import sys
    if sys.argv[1] == 'server':
        startServer ()

