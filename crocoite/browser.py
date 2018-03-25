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
    def body (self):
        """ Return response body or None """
        try:
            body = self.tab.Network.getResponseBody (requestId=self.id)
            rawBody = body['body']
            base64Encoded = body['base64Encoded']
            if base64Encoded:
                rawBody = b64decode (rawBody)
            else:
                rawBody = rawBody.encode ('utf8')
            return rawBody
        except pychrome.exceptions.CallMethodException:
            return None

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


