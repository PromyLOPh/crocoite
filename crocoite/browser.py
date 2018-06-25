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
from collections import deque
from threading import Event
from http.server import BaseHTTPRequestHandler

import pychrome

class Item:
    """
    Simple wrapper containing Chrome request and response
    """

    __slots__ = ('tab', 'chromeRequest', 'chromeResponse', 'chromeFinished',
            'isRedirect', 'failed')

    def __init__ (self, tab):
        self.tab = tab
        self.chromeRequest = {}
        self.chromeResponse = {}
        self.chromeFinished = {}
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

    @property
    def resourceType (self):
        return self.chromeResponse.get ('type', self.chromeRequest.get ('type', None))

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
import shutil

class ChromeService:
    """ Start Google Chrome listening on a random port """

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

