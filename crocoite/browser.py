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
from base64 import b64decode, b64encode
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler

from yarl import URL
from multidict import CIMultiDict

from .logger import Level
from .devtools import Browser, TabException

# These two classes’ only purpose is so we can later tell whether a body was
# base64-encoded or a unicode string
class Base64Body (bytes):
    def __new__ (cls, value):
        return bytes.__new__ (cls, b64decode (value))

    @classmethod
    def fromBytes (cls, b):
        """ For testing """
        return cls (b64encode (b))

class UnicodeBody (bytes):
    def __new__ (cls, value):
        if type (value) is not str:
            raise TypeError ('expecting unicode string')

        return bytes.__new__ (cls, value.encode ('utf-8'))

class Request:
    __slots__ = ('headers', 'body', 'initiator', 'hasPostData', 'method', 'timestamp')

    def __init__ (self, method=None, headers=None, body=None):
        self.headers = headers
        self.body = body
        self.hasPostData = False
        self.initiator = None
        # HTTP method
        self.method = method
        self.timestamp = None

    def __repr__ (self):
        return f'Request({self.method!r}, {self.headers!r}, {self.body!r})'

    def __eq__ (self, b):
        if b is None:
            return False

        if not isinstance (b, Request):
            raise TypeError ('Can only compare equality with Request.')

        # do not compare hasPostData (only required to fetch body) and
        # timestamp (depends on time)
        return self.headers == b.headers and \
                self.body == b.body and \
                self.initiator == b.initiator and \
                self.method == b.method

class Response:
    __slots__ = ('status', 'statusText', 'headers', 'body', 'bytesReceived',
            'timestamp', 'mimeType')

    def __init__ (self, status=None, statusText=None, headers=None, body=None, mimeType=None):
        self.status = status
        self.statusText = statusText
        self.headers = headers
        self.body = body
        # bytes received over the network (not body size!)
        self.bytesReceived = 0
        self.timestamp = None
        self.mimeType = mimeType

    def __repr__ (self):
        return f'Response({self.status!r}, {self.statusText!r}, {self.headers!r}, {self.body!r}, {self.mimeType!r})'

    def __eq__ (self, b):
        if b is None:
            return False

        if not isinstance (b, Response):
            raise TypeError ('Can only compare equality with Response.')

        # do not compare bytesReceived (depends on network), timestamp
        # (depends on time) and statusText (does not matter)
        return self.status == b.status and \
                self.statusText == b.statusText and \
                self.headers == b.headers and \
                self.body == b.body and \
                self.mimeType == b.mimeType

class ReferenceTimestamp:
    """ Map relative timestamp to absolute timestamp """

    def __init__ (self, relative, absolute):
        self.relative = timedelta (seconds=relative)
        self.absolute = datetime.utcfromtimestamp (absolute)

    def __call__ (self, relative):
        if not isinstance (relative, timedelta):
            relative = timedelta (seconds=relative)
        return self.absolute + (relative-self.relative)

class RequestResponsePair:
    __slots__ = ('request', 'response', 'id', 'url', 'remoteIpAddress',
            'protocol', 'resourceType', '_time')

    def __init__ (self, id=None, url=None, request=None, response=None):
        self.request = request
        self.response = response
        self.id = id
        self.url = url
        self.remoteIpAddress = None
        self.protocol = None
        self.resourceType = None
        self._time = None

    def __repr__ (self):
        return f'RequestResponsePair({self.id!r}, {self.url!r}, {self.request!r}, {self.response!r})'

    def __eq__ (self, b):
        if not isinstance (b, RequestResponsePair):
            raise TypeError (f'Can only compare with {self.__class__.__name__}')

        # do not compare id and _time. These depend on external factors and do
        # not influence the request/response *content*
        return self.request == b.request and \
                self.response == b.response and \
                self.url == b.url and \
                self.remoteIpAddress == b.remoteIpAddress and \
                self.protocol == b.protocol and \
                self.resourceType == b.resourceType

    def fromRequestWillBeSent (self, req):
        """ Set request data from Chrome Network.requestWillBeSent event """
        r = req['request']

        self.id = req['requestId']
        self.url = URL (r['url'])
        self.resourceType = req.get ('type')
        self._time = ReferenceTimestamp (req['timestamp'], req['wallTime'])

        assert self.request is None, req
        self.request = Request ()
        self.request.initiator = req['initiator']
        self.request.headers = CIMultiDict (self._unfoldHeaders (r['headers']))
        self.request.hasPostData = r.get ('hasPostData', False)
        self.request.method = r['method']
        self.request.timestamp = self._time (req['timestamp'])
        if self.request.hasPostData:
            postData = r.get ('postData')
            if postData is not None:
                self.request.body = UnicodeBody (postData)

    def fromResponse (self, r, timestamp=None, resourceType=None):
        """
        Set response data from Chrome’s Response object.
        
        Request must exist. Updates if response was set before. Sometimes
        fromResponseReceived is triggered twice by Chrome. No idea why.
        """
        assert self.request is not None, (self.request, r)

        if not timestamp:
            timestamp = self.request.timestamp

        self.remoteIpAddress = r.get ('remoteIPAddress')
        self.protocol = r.get ('protocol')
        if resourceType:
            self.resourceType = resourceType

        # a response may contain updated request headers (i.e. those actually
        # sent over the wire)
        if 'requestHeaders' in r:
            self.request.headers = CIMultiDict (self._unfoldHeaders (r['requestHeaders']))

        self.response = Response ()
        self.response.headers = CIMultiDict (self._unfoldHeaders (r['headers']))
        self.response.status = r['status']
        self.response.statusText = r['statusText']
        self.response.timestamp = timestamp
        self.response.mimeType = r['mimeType']

    def fromResponseReceived (self, resp):
        """ Set response data from Chrome Network.responseReceived """
        return self.fromResponse (resp['response'],
                self._time (resp['timestamp']), resp['type'])

    def fromLoadingFinished (self, data):
        self.response.bytesReceived = data['encodedDataLength']

    def fromLoadingFailed (self, data):
        self.response = None

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

    async def prefetchRequestBody (self, tab):
        if self.request.hasPostData and self.request.body is None:
            try:
                postData = await tab.Network.getRequestPostData (requestId=self.id)
                self.request.body = UnicodeBody (postData['postData'])
            except TabException:
                self.request.body = None

    async def prefetchResponseBody (self, tab):
        """ Fetch response body """
        try:
            body = await tab.Network.getResponseBody (requestId=self.id)
            if body['base64Encoded']:
                self.response.body = Base64Body (body['body'])
            else:
                self.response.body = UnicodeBody (body['body'])
        except TabException:
            self.response.body = None

class NavigateError (IOError):
    pass

class PageIdle:
    """ Page idle event """

    __slots__ = ('idle', )

    def __init__ (self, idle):
        self.idle = idle

    def __bool__ (self):
        return self.idle

class FrameNavigated:
    __slots__ = ('id', 'url', 'mimeType')

    def __init__ (self, id, url, mimeType):
        self.id = id
        self.url = URL (url)
        self.mimeType = mimeType

class SiteLoader:
    """
    Load site in Chrome and monitor network requests

    XXX: track popup windows/new tabs and close them
    """

    __slots__ = ('requests', 'browser', 'logger', 'tab', '_iterRunning',
            '_framesLoading', '_rootFrame')
    allowedSchemes = {'http', 'https'}

    def __init__ (self, browser, logger):
        self.requests = {}
        self.browser = Browser (url=browser)
        self.logger = logger.bind (context=type (self).__name__)
        self._iterRunning = []

        self._framesLoading = set ()
        self._rootFrame = None

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
                tab.Page.frameNavigated: self._frameNavigated,
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
        tabGetTask = asyncio.ensure_future (self.tab.get ())
        running.append (tabGetTask)
        while True:
            done, pending = await asyncio.wait (running, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                result = t.result ()
                if result is None:
                    pass
                elif t == tabGetTask:
                    method, data = result
                    f = handler.get (method, None)
                    if f is not None:
                        task = asyncio.ensure_future (f (**data))
                        pending.add (task)
                    tabGetTask = asyncio.ensure_future (self.tab.get ())
                    pending.add (tabGetTask)
                else:
                    yield result

            running = pending
            self._iterRunning = running

    async def navigate (self, url):
        ret = await self.tab.Page.navigate(url=url)
        self.logger.debug ('navigate',
                uuid='9d47ded2-951f-4e09-86ee-fd4151e20666', result=ret)
        if 'errorText' in ret:
            raise NavigateError (ret['errorText'])
        self._rootFrame = ret['frameId']

    # internal chrome callbacks
    async def _requestWillBeSent (self, **kwargs):
        self.logger.debug ('requestWillBeSent',
                uuid='b828d75a-650d-42d2-8c66-14f4547512da', args=kwargs)

        reqId = kwargs['requestId']
        req = kwargs['request']
        url = URL (req['url'])
        logger = self.logger.bind (reqId=reqId, reqUrl=url)

        if url.scheme not in self.allowedSchemes:
            return

        ret = None
        item = self.requests.get (reqId)
        if item:
            # redirects never “finish” loading, but yield another requestWillBeSent with this key set
            redirectResp = kwargs.get ('redirectResponse')
            if redirectResp:
                if item.url != url:
                    # this happens for unknown reasons. the docs simply state
                    # it can differ in case of a redirect. Fix it and move on.
                    logger.warning ('redirect url differs',
                            uuid='558a7df7-2258-4fe4-b16d-22b6019cc163',
                            expected=item.url)
                    redirectResp['url'] = str (item.url)
                item.fromResponse (redirectResp)
                logger.info ('redirect', uuid='85eaec41-e2a9-49c2-9445-6f19690278b8', target=url)
                # XXX: queue this? no need to wait for it
                await item.prefetchRequestBody (self.tab)
                # cannot fetch response body due to race condition (item id reused)
                ret = item
            else:
                logger.warning ('request exists', uuid='2c989142-ba00-4791-bb03-c2a14e91a56b')

        item = RequestResponsePair ()
        item.fromRequestWillBeSent (kwargs)
        self.requests[reqId] = item

        return ret

    async def _responseReceived (self, **kwargs):
        self.logger.debug ('responseReceived',
                uuid='ecd67e69-401a-41cb-b4ec-eeb1f1ec6abb', args=kwargs)

        reqId = kwargs['requestId']
        item = self.requests.get (reqId)
        if item is None:
            return

        resp = kwargs['response']
        url = URL (resp['url'])
        logger = self.logger.bind (reqId=reqId, respUrl=url)
        if item.url != url:
            logger.error ('url mismatch', uuid='7385f45f-0b06-4cbc-81f9-67bcd72ee7d0', respUrl=url)
        if url.scheme in self.allowedSchemes:
            item.fromResponseReceived (kwargs)
        else:
            logger.warning ('scheme forbidden', uuid='2ea6e5d7-dd3b-4881-b9de-156c1751c666')

    async def _loadingFinished (self, **kwargs):
        """
        Item was fully loaded. For some items the request body is not available
        when responseReceived is fired, thus move everything here.
        """
        self.logger.debug ('loadingFinished',
                uuid='35479405-a5b5-4395-8c33-d3601d1796b9', args=kwargs)

        reqId = kwargs['requestId']
        item = self.requests.pop (reqId, None)
        if item is None:
            # we never recorded this request (blacklisted scheme, for example)
            return
        req = item.request
        if item.url.scheme in self.allowedSchemes:
            item.fromLoadingFinished (kwargs)
            # XXX queue both
            await asyncio.gather (item.prefetchRequestBody (self.tab), item.prefetchResponseBody (self.tab))
            return item

    async def _loadingFailed (self, **kwargs):
        self.logger.info ('loadingFailed',
                uuid='4a944e85-5fae-4aa6-9e7c-e578b29392e4', args=kwargs)

        reqId = kwargs['requestId']
        logger = self.logger.bind (reqId=reqId)
        item = self.requests.pop (reqId, None)
        if item is not None:
            item.fromLoadingFailed (kwargs)
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
        self.logger.debug ('frameStartedLoading',
                uuid='bbeb39c0-3304-4221-918e-f26bd443c566', args=kwargs)

        self._framesLoading.add (kwargs['frameId'])
        return PageIdle (False)

    async def _frameStoppedLoading (self, **kwargs):
        self.logger.debug ('frameStoppedLoading',
                uuid='fcbe8110-511c-4cbb-ac2b-f61a5782c5a0', args=kwargs)

        self._framesLoading.remove (kwargs['frameId'])
        if not self._framesLoading:
            return PageIdle (True)

    async def _frameNavigated (self, **kwargs):
        self.logger.debug ('frameNavigated',
                uuid='0e876f7d-7129-4612-8632-686f42ac6e1f', args=kwargs)
        frame = kwargs['frame']
        if self._rootFrame == frame['id']:
            assert frame.get ('parentId', None) is None, "root frame must not have a parent"
            return FrameNavigated (frame['id'], frame['url'], frame['mimeType'])

