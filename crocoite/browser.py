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

import asyncio
from urllib.parse import urlsplit
from base64 import b64decode
from http.server import BaseHTTPRequestHandler

from .logger import Level
from .devtools import Browser, TabException

class Item:
    """
    Simple wrapper containing Chrome request and response
    """

    __slots__ = ('chromeRequest', 'chromeResponse', 'chromeFinished',
            'isRedirect', 'failed', 'body', 'requestBody')

    def __init__ (self):
        self.chromeRequest = {}
        self.chromeResponse = {}
        self.chromeFinished = {}
        self.isRedirect = False
        self.failed = False
        self.body = None
        self.requestBody = None

    def __repr__ (self):
        return '<Item {}>'.format (self.url)

    @property
    def request (self):
        return self.chromeRequest.get ('request', {})

    @property
    def response (self):
        return self.chromeResponse.get ('response', {})

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
        return self.response.get ('url', self.request.get ('url'))

    @property
    def parsedUrl (self):
        return urlsplit (self.url)

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

    async def prefetchRequestBody (self, tab):
        # request body
        req = self.request
        postData = req.get ('postData')
        if postData:
            self.requestBody = postData.encode ('utf8'), False
        elif req.get ('hasPostData', False):
            try:
                postData = await tab.Network.getRequestPostData (requestId=self.id)
                postData = postData['postData']
                self.requestBody = b64decode (postData), True
            except TabException:
                self.requestBody = None
        else:
            self.requestBody = None, False

    async def prefetchResponseBody (self, tab):
        # get response body
        try:
            body = await tab.Network.getResponseBody (requestId=self.id)
            rawBody = body['body']
            base64Encoded = body['base64Encoded']
            if base64Encoded:
                rawBody = b64decode (rawBody)
            else:
                rawBody = rawBody.encode ('utf8')
            self.body = rawBody, base64Encoded
        except TabException:
            self.body = None

class VarChangeEvent:
    """ Notify when variable is changed """

    __slots__ = ('_value', 'event')

    def __init__ (self, value):
        self._value = value
        self.event = asyncio.Event()

    def set (self, value):
        if value != self._value:
            self._value = value
            # unblock waiting threads
            self.event.set ()
            self.event.clear ()

    def get (self):
        return self._value

    async def wait (self):
        await self.event.wait ()
        return self._value

class SiteLoader:
    """
    Load site in Chrome and monitor network requests

    XXX: track popup windows/new tabs and close them
    """

    __slots__ = ('requests', 'browser', 'url', 'logger', 'tab', '_iterRunning', 'idle', '_framesLoading')
    allowedSchemes = {'http', 'https'}

    def __init__ (self, browser, url, logger):
        self.requests = {}
        self.browser = Browser (url=browser)
        self.url = url
        self.logger = logger.bind (context=type (self).__name__, url=url)
        self._iterRunning = []

        self.idle = VarChangeEvent (True)
        self._framesLoading = set ()

    async def __aenter__ (self):
        tab = self.tab = await self.browser.__aenter__ ()

        # enable events
        await asyncio.gather (*[
                tab.Log.enable (),
                tab.Network.enable(),
                tab.Page.enable (),
                tab.Inspector.enable (),
                tab.Network.clearBrowserCache (),
                tab.Network.clearBrowserCookies (),
                ])
        return self

    async def __aexit__ (self, exc_type, exc_value, traceback):
        for task in self._iterRunning:
            # ignore any results from stuff we did not end up using anyway
            if not task.done ():
                task.cancel ()
        self._iterRunning = []
        await self.browser.__aexit__ (exc_type, exc_value, traceback)
        self.tab = None
        return False

    def __len__ (self):
        return len (self.requests)

    async def __aiter__ (self):
        """ Retrieve network items """
        tab = self.tab
        assert tab is not None
        handler = {
                tab.Network.requestWillBeSent: self._requestWillBeSent,
                tab.Network.responseReceived: self._responseReceived,
                tab.Network.loadingFinished: self._loadingFinished,
                tab.Network.loadingFailed: self._loadingFailed,
                tab.Log.entryAdded: self._entryAdded,
                tab.Page.javascriptDialogOpening: self._javascriptDialogOpening,
                tab.Page.frameStartedLoading: self._frameStartedLoading,
                tab.Page.frameStoppedLoading: self._frameStoppedLoading,
                }

        # The implementation is a little advanced. Why? The goal here is to
        # process events from the tab as quickly as possible (i.e.
        # asynchronously). We need to make sure that JavaScript dialogs are
        # handled immediately for instance. Otherwise they stall every
        # other request. Also, we don’t want to use an unbounded queue,
        # since the items yielded can get quite big (response body). Thus
        # we need to block (yield) for every item completed, but not
        # handled by the consumer (caller).
        running = self._iterRunning
        running.append (asyncio.ensure_future (self.tab.get ()))
        while True:
            done, pending = await asyncio.wait (running, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                result = t.result ()
                if result is None:
                    pass
                elif isinstance (result, Item):
                    yield result
                else:
                    method, data = result
                    f = handler.get (method, None)
                    if f is not None:
                        task = asyncio.ensure_future (f (**data))
                        pending.add (task)
                    pending.add (asyncio.ensure_future (self.tab.get ()))

            running = pending
            self._iterRunning = running

    async def start (self):
        await self.tab.Page.navigate(url=self.url)

    # internal chrome callbacks
    async def _requestWillBeSent (self, **kwargs):
        reqId = kwargs['requestId']
        req = kwargs['request']
        logger = self.logger.bind (reqId=reqId, reqUrl=req['url'])

        url = urlsplit (req['url'])
        if url.scheme not in self.allowedSchemes:
            return

        ret = None
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
                logger.info ('redirect', uuid='85eaec41-e2a9-49c2-9445-6f19690278b8', target=req['url'])
                await item.prefetchRequestBody (self.tab)
                # cannot fetch request body due to race condition (item id reused)
                ret = item
            else:
                logger.warning ('request exists', uuid='2c989142-ba00-4791-bb03-c2a14e91a56b')

        item = Item ()
        item.setRequest (kwargs)
        self.requests[reqId] = item
        logger.debug ('request', uuid='55c17564-1bd0-4499-8724-fa7aad65478f')

        return ret

    async def _responseReceived (self, **kwargs):
        reqId = kwargs['requestId']
        item = self.requests.get (reqId)
        if item is None:
            return

        resp = kwargs['response']
        logger = self.logger.bind (reqId=reqId, respUrl=resp['url'])
        url = urlsplit (resp['url'])
        if url.scheme in self.allowedSchemes:
            logger.debug ('response', uuid='84461c4e-e8ef-4cbd-8e8e-e10a901c8bd0')
            item.setResponse (kwargs)
        else:
            logger.warning ('scheme forbidden', uuid='2ea6e5d7-dd3b-4881-b9de-156c1751c666')

    async def _loadingFinished (self, **kwargs):
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
        logger = self.logger.bind (reqId=reqId, reqUrl=req['url'])
        resp = item.response
        if req['url'] != resp['url']:
            logger.error ('url mismatch', uuid='7385f45f-0b06-4cbc-81f9-67bcd72ee7d0', respUrl=resp['url'])
        url = urlsplit (resp['url'])
        if url.scheme in self.allowedSchemes:
            logger.info ('finished', uuid='5a8b4bad-f86a-4fe6-a53e-8da4130d6a02')
            item.setFinished (kwargs)
            await asyncio.gather (item.prefetchRequestBody (self.tab), item.prefetchResponseBody (self.tab))
            return item

    async def _loadingFailed (self, **kwargs):
        reqId = kwargs['requestId']
        self.logger.warning ('loading failed',
                uuid='68410f13-6eea-453e-924e-c1af4601748b',
                errorText=kwargs['errorText'],
                blockedReason=kwargs.get ('blockedReason'))
        item = self.requests.pop (reqId, None)
        if item is not None:
            item.failed = True
            return item

    async def _entryAdded (self, **kwargs):
        """ Log entry added """
        entry = kwargs['entry']
        level = {'verbose': Level.DEBUG, 'info': Level.INFO,
                'warning': Level.WARNING,
                'error': Level.ERROR}.get (entry.pop ('level'), Level.INFO)
        entry['uuid'] = 'e62ffb5a-0521-459c-a3d9-1124551934d2'
        self.logger (level, 'console', **entry)

    async def _javascriptDialogOpening (self, **kwargs):
        t = kwargs.get ('type')
        if t in {'alert', 'confirm', 'prompt'}:
            self.logger.info ('js dialog',
                    uuid='d6f07ce2-648e-493b-a1df-f353bed27c84',
                    action='cancel', type=t, message=kwargs.get ('message'))
            await self.tab.Page.handleJavaScriptDialog (accept=False)
        elif t == 'beforeunload':
            # we must accept this one, otherwise the page will not unload/close
            self.logger.info ('js dialog',
                    uuid='96399b99-9834-4c8f-bd93-cb9fa2225abd',
                    action='proceed', type=t, message=kwargs.get ('message'))
            await self.tab.Page.handleJavaScriptDialog (accept=True)
        else: # pragma: no cover
            self.logger.warning ('js dialog unknown',
                    uuid='3ef7292e-8595-4e89-b834-0cc6bc40ee38', **kwargs)

    async def _frameStartedLoading (self, **kwargs):
        self._framesLoading.add (kwargs['frameId'])
        self.idle.set (False)

    async def _frameStoppedLoading (self, **kwargs):
        self._framesLoading.remove (kwargs['frameId'])
        if not self._framesLoading:
            self.idle.set (True)

