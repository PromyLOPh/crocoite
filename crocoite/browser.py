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
import pychrome

class Item:
    """
    Simple wrapper containing Chrome request and response
    """

    def __init__ (self, tab):
        self.tab = tab
        self.chromeRequest = None
        self.chromeResponse = None
        self.chromeFinished = None

    def __repr__ (self):
        return '<Item {}>'.format (self.request['url'])

    @property
    def request (self):
        return self.chromeRequest['request']

    @property
    def response (self):
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
            body = self.tab.Network.getResponseBody (requestId=self.id, _timeout=60)
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
                return b64decode (self.tab.Network.getRequestPostData (requestId=self.id, _timeout=60)['postData']), True
            except (pychrome.exceptions.CallMethodException, pychrome.exceptions.TimeoutException):
                raise ValueError ('Cannot fetch request body')
        return None, False

    def setRequest (self, req):
        self.chromeRequest = req

    def setResponse (self, resp):
        self.chromeResponse = resp

    def setFinished (self, finished):
        self.chromeFinished = finished

class SiteLoader:
    """
    Load site in Chrome and monitor network requests

    XXX: track popup windows/new tabs and close them
    """

    allowedSchemes = {'http', 'https'}

    def __init__ (self, browser, url, logger=logging.getLogger(__name__)):
        self.requests = {}
        self.browser = browser
        self.url = url
        self.logger = logger

        self.tab = browser.new_tab()

    def __enter__ (self):
        tab = self.tab
        # setup callbacks
        tab.Network.requestWillBeSent = self._requestWillBeSent
        tab.Network.responseReceived = self._responseReceived
        tab.Network.loadingFinished = self._loadingFinished
        tab.Network.loadingFailed = self._loadingFailed
        tab.Log.entryAdded = self._entryAdded
        #tab.Page.loadEventFired = loadEventFired
        tab.Page.javascriptDialogOpening = self._javascriptDialogOpening

        # start the tab
        tab.start()

        # enable events
        tab.Log.enable ()
        tab.Network.enable()
        tab.Page.enable ()
        tab.Network.clearBrowserCache ()
        if tab.Network.canClearBrowserCookies ()['result']:
            tab.Network.clearBrowserCookies ()

        return self

    def __len__ (self):
        return len (self.requests)

    def start (self):
        self.tab.Page.navigate(url=self.url)

    def wait (self, timeout=1):
        self.tab.wait (timeout)

    def waitIdle (self, idleTimeout=1, maxTimeout=60):
        step = 0
        for i in range (0, maxTimeout):
            self.wait (1)
            if len (self) == 0:
                step += 1
                if step > idleTimeout:
                    break
            else:
                step = 0

    def stop (self):
        """
        Stop loading site

        XXX: stop executing scripts
        """

        tab = self.tab

        tab.Page.stopLoading ()
        tab.Network.disable ()
        tab.Page.disable ()
        tab.Log.disable ()
        # XXX: we can’t drain the event queue directly, so insert (yet another) wait
        tab.wait (1)
        tab.Network.requestWillBeSent = None
        tab.Network.responseReceived = None
        tab.Network.loadingFinished = None
        tab.Network.loadingFailed = None
        tab.Page.loadEventFired = None
        tab.Page.javascriptDialogOpening = None
        tab.Log.entryAdded = None

    def __exit__ (self, exc_type, exc_value, traceback):
        self.tab.stop ()
        self.browser.close_tab(self.tab)
        return False

    # overrideable callbacks
    def loadingFinished (self, item, redirect=False):
        pass

    def loadingFailed (self, item):
        pass

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
                self.loadingFinished (item, redirect=True)
                self.logger.info ('redirected request {} has url {}'.format (reqId, req['url']))
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
            self.loadingFinished (item)

    def _loadingFailed (self, **kwargs):
        reqId = kwargs['requestId']
        self.logger.warning ('failed {} {}'.format (reqId, kwargs['errorText'], kwargs.get ('blockedReason')))
        item = self.requests.pop (reqId, None)
        self.loadingFailed (item)

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

class AccountingSiteLoader (SiteLoader):
    """
    SiteLoader that keeps basic statistics about retrieved pages.
    """

    def __init__ (self, browser, url, logger=logging.getLogger(__name__)):
        super ().__init__ (browser, url, logger)

        self.stats = {'requests': 0, 'finished': 0, 'failed': 0, 'bytesRcv': 0}

    def loadingFinished (self, item, redirect=False):
        super ().loadingFinished (item, redirect)

        self.stats['finished'] += 1
        self.stats['bytesRcv'] += item.encodedDataLength

    def loadingFailed (self, item):
        super ().loadingFailed (item)

        self.stats['failed'] += 1

    def _requestWillBeSent (self, **kwargs):
        super ()._requestWillBeSent (**kwargs)

        self.stats['requests'] += 1

import subprocess
from tempfile import mkdtemp
from contextlib import contextmanager
import socket, shutil

@contextmanager
def ChromeService (binary='google-chrome-stable', host='localhost', port=9222, windowSize=(1920, 1080)):
    """
    Start Chrome with socket activation (i.e. pass listening socket). Polling
    is not required with this method, since reads will block until Chrome is
    ready.
    """
    while True:
        s = socket.socket ()
        s.setsockopt (socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind ((host, port))
            break
        except OSError:
            # try different port
            if port < 65000:
                port += 1
            else:
                raise
    s.listen (10)
    userDataDir = mkdtemp ()
    args = [binary,
            '--window-size={},{}'.format (*windowSize),
            '--user-data-dir={}'.format (userDataDir), # use temporory user dir
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
            '--remote-debugging-socket-fd={}'.format (s.fileno ()),
            '--homepage=about:blank',
            'about:blank']
    # start new session, so ^C does not affect subprocess
    p = subprocess.Popen (args, pass_fds=[s.fileno()], start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
    s.close ()

    # must be wrapped in try-finally, otherwise code in __exit__/finally is not
    # executed
    try:
        yield 'http://{}:{}'.format (host, port)
    finally:
        p.terminate ()
        p.wait ()
        shutil.rmtree (userDataDir)

@contextmanager
def NullService (url):
    yield url

### tests ###

import unittest, time
from http.server import BaseHTTPRequestHandler

class TestHTTPRequestHandler (BaseHTTPRequestHandler):
    encodingTestString = {
        'latin1': 'äöü',
        'utf-8': 'äöü',
        'ISO-8859-1': 'äöü',
        }
    binaryTestData = b'\x00\x01\x02'
    # 1×1 pixel PNG
    imageTestData = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x00\x00\x00\x00:~\x9bU\x00\x00\x00\nIDAT\x08\x1dc\xf8\x0f\x00\x01\x01\x01\x006_g\x80\x00\x00\x00\x00IEND\xaeB`\x82'
    htmlTestData = '<html><body><img src="/image"><img src="/nonexistent"></body></html>'
    alertData = '<html><body><script>window.addEventListener("beforeunload", function (e) { e.returnValue = "bye?"; return e.returnValue; }); alert("stopping here"); if (confirm("are you sure?") || prompt ("42?")) { window.location = "/nonexistent"; }</script><img src="/image"></body></html>'

    def do_GET(self):
        path = self.path
        if path.startswith ('/redirect/301'):
            self.send_response(301)
            self.send_header ('Location', path[13:])
            self.end_headers()
        elif path == '/empty':
            self.send_response (200)
            self.end_headers ()
        elif path.startswith ('/encoding'):
            # send text data with different encodings
            _, _, encoding = path.split ('/', 3)
            self.send_response (200)
            self.send_header ('Content-Type', 'text/plain; charset={}'.format (encoding))
            self.end_headers ()
            self.wfile.write (self.encodingTestString[encoding].encode (encoding))
        elif path == '/binary':
            # send binary data
            self.send_response (200)
            self.send_header ('Content-Type', 'application/octet-stream')
            self.send_header ('Content-Length', len (self.binaryTestData))
            self.end_headers ()
            self.wfile.write (self.binaryTestData)
        elif path == '/image':
            # send binary data
            self.send_response (200)
            self.send_header ('Content-Type', 'image/png')
            self.end_headers ()
            self.wfile.write (self.imageTestData)
        elif path == '/attachment':
            self.send_response (200)
            self.send_header ('Content-Type', 'text/plain; charset=utf-8')
            self.send_header ('Content-Disposition', 'attachment; filename="attachment.txt"')
            self.end_headers ()
            self.wfile.write (self.encodingTestString['utf-8'].encode ('utf-8'))
        elif path == '/html':
            self.send_response (200)
            self.send_header ('Content-Type', 'text/html; charset=utf-8')
            self.end_headers ()
            self.wfile.write (self.htmlTestData.encode ('utf-8'))
        elif path == '/alert':
            self.send_response (200)
            self.send_header ('Content-Type', 'text/html; charset=utf-8')
            self.end_headers ()
            self.wfile.write (self.alertData.encode ('utf-8'))
        else:
            self.send_response (404)
            self.end_headers ()

    def log_message (self, format, *args):
        pass

def startServer ():
    import http.server
    PORT = 8000
    httpd = http.server.HTTPServer (("localhost", PORT), TestHTTPRequestHandler)
    httpd.serve_forever()

class TestSiteLoaderAdapter (SiteLoader):
    def __init__ (self, browser, url):
        SiteLoader.__init__ (self, browser, url)
        self.finished = []

    def loadingFinished (self, item, redirect=False):
        self.finished.append (item)

class TestSiteLoader (unittest.TestCase):
    def setUp (self):
        from multiprocessing import Process
        self.server = Process (target=startServer)
        self.server.start ()
        self.baseurl = 'http://localhost:8000/'
        self.service = ChromeService ()
        browserUrl = self.service.__enter__ ()
        self.browser = pychrome.Browser(url=browserUrl)

    def buildAdapter (self, path):
        return TestSiteLoaderAdapter (self.browser, '{}{}'.format (self.baseurl, path))

    def assertUrls (self, l, expect):
        urls = set (map (lambda x: x.parsedUrl.path, l.finished))
        expect = set (expect)
        self.assertEqual (urls, expect)
        
    def test_wait (self):
        waittime = 2
        with self.buildAdapter ('empty') as l:
            l.start ()
            before = time.time ()
            l.wait (waittime)
            after = time.time ()
            self.assertTrue ((after-before) >= waittime)

    def test_empty (self):
        with self.buildAdapter ('empty') as l:
            l.start ()
            l.waitIdle ()
            self.assertEqual (len (l.finished), 1)

    def test_redirect301 (self):
        with self.buildAdapter ('redirect/301/empty') as l:
            l.start ()
            l.waitIdle ()
            self.assertEqual (len (l.finished), 2)
            self.assertUrls (l, ['/redirect/301/empty', '/empty'])
            for item in l.finished:
                if item.parsedUrl.path == '/empty':
                    self.assertEqual (item.response['status'], 200)
                    self.assertEqual (item.body[0], b'')
                elif item.parsedUrl.path == '/redirect/301/empty':
                    self.assertEqual (item.response['status'], 301)
                else:
                    self.fail ('unknown url')

    def test_redirect301multi (self):
        with self.buildAdapter ('redirect/301/redirect/301/empty') as l:
            l.start ()
            l.waitIdle ()
            self.assertEqual (len (l.finished), 3)
            self.assertUrls (l, ['/redirect/301/redirect/301/empty', '/redirect/301/empty', '/empty'])
            for item in l.finished:
                if item.parsedUrl.path == '/empty':
                    self.assertEqual (item.response['status'], 200)
                    self.assertEqual (item.body[0], b'')
                elif item.parsedUrl.path in {'/redirect/301/empty', \
                        '/redirect/301/redirect/301/empty'}:
                    self.assertEqual (item.response['status'], 301)
                else:
                    self.fail ('unknown url')

    def test_encoding (self):
        """ Text responses are transformed to UTF-8. Make sure this works
        correctly. """
        for encoding, expected in TestHTTPRequestHandler.encodingTestString.items ():
            with self.buildAdapter ('encoding/{}'.format (encoding)) as l:
                l.start ()
                l.waitIdle ()
                self.assertEqual (len (l.finished), 1)
                self.assertUrls (l, ['/encoding/{}'.format (encoding)])
                self.assertEqual (l.finished[0].body[0], expected.encode ('utf8'))

    def test_binary (self):
        """ Browser should ignore content it cannot display (i.e. octet-stream) """
        with self.buildAdapter ('binary') as l:
            l.start ()
            l.waitIdle ()
            self.assertEqual (len (l.finished), 0)

    def test_image (self):
        """ Images should be displayed inline """
        with self.buildAdapter ('image') as l:
            l.start ()
            l.waitIdle ()
            self.assertEqual (len (l.finished), 1)
            self.assertUrls (l, ['/image'])
            self.assertEqual (l.finished[0].body[0], TestHTTPRequestHandler.imageTestData)

    def test_attachment (self):
        """ And downloads won’t work in headless mode """
        with self.buildAdapter ('attachment') as l:
            l.start ()
            l.waitIdle ()
            self.assertEqual (len (l.finished), 0)

    def test_html (self):
        with self.buildAdapter ('html') as l:
            l.start ()
            l.waitIdle ()
            self.assertEqual (len (l.finished), 3)
            self.assertUrls (l, ['/html', '/image', '/nonexistent'])
            for item in l.finished:
                if item.parsedUrl.path == '/html':
                    self.assertEqual (item.response['status'], 200)
                    self.assertEqual (item.body[0], TestHTTPRequestHandler.htmlTestData.encode ('utf-8'))
                elif item.parsedUrl.path == '/image':
                    self.assertEqual (item.response['status'], 200)
                    self.assertEqual (item.body[0], TestHTTPRequestHandler.imageTestData)
                elif item.parsedUrl.path == '/nonexistent':
                    self.assertEqual (item.response['status'], 404)
                else:
                    self.fail ('unknown url')

    def test_alert (self):
        with self.buildAdapter ('alert') as l:
            l.start ()
            l.waitIdle ()
            self.assertUrls (l, ['/alert', '/image'])
            for item in l.finished:
                if item.parsedUrl.path == '/alert':
                    self.assertEqual (item.response['status'], 200)
                    self.assertEqual (item.body[0], TestHTTPRequestHandler.alertData.encode ('utf-8'))
                elif item.parsedUrl.path == '/image':
                    self.assertEqual (item.response['status'], 200)
                    self.assertEqual (item.body[0], TestHTTPRequestHandler.imageTestData)
                else:
                    self.fail ('unknown url')

    def tearDown (self):
        self.service.__exit__ (None, None, None)
        self.server.terminate ()
        self.server.join ()

if __name__ == '__main__':
    import sys
    if sys.argv[1] == 'server':
        startServer ()

