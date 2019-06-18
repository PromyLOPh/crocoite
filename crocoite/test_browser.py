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

import asyncio, socket
from operator import itemgetter
from http.server import BaseHTTPRequestHandler
from datetime import datetime

from yarl import URL
from aiohttp import web
from multidict import CIMultiDict

from hypothesis import given
import hypothesis.strategies as st
from hypothesis.provisional import domains
import pytest

from .browser import RequestResponsePair, SiteLoader, Request, \
        UnicodeBody, ReferenceTimestamp, Base64Body, UnicodeBody, Request, \
        Response, NavigateError, PageIdle, FrameNavigated
from .logger import Logger, Consumer
from .devtools import Crashed, Process

# if you want to know what’s going on:
#import logging
#logging.basicConfig(level=logging.DEBUG)

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
async def loader (logger):
    async with Process () as browser, SiteLoader (browser, logger) as l:
        yield l

@pytest.mark.asyncio
async def test_crash (loader):
    with pytest.raises (Crashed):
        await loader.tab.Page.crash ()

@pytest.mark.asyncio
async def test_invalidurl (loader):
    host = 'nonexistent.example'

    # make sure the url does *not* resolve (some DNS intercepting ISP’s mess
    # with this)
    loop = asyncio.get_event_loop ()
    try:
        resolved = await loop.getaddrinfo (host, None)
    except socket.gaierror:
        url = URL.build (scheme='http', host=host)
        with pytest.raises (NavigateError):
            await loader.navigate (url)
    else:
        pytest.skip (f'host {host} resolved to {resolved}')

timestamp = st.one_of (
                st.integers(min_value=0, max_value=2**32-1),
                st.floats (min_value=0, max_value=2**32-1),
                )

@given(timestamp, timestamp, timestamp)
def test_referencetimestamp (relativeA, absoluteA, relativeB):
    ts = ReferenceTimestamp (relativeA, absoluteA)
    absoluteA = datetime.utcfromtimestamp (absoluteA)
    absoluteB = ts (relativeB)
    assert (absoluteA < absoluteB and relativeA < relativeB) or \
            (absoluteA >= absoluteB and relativeA >= relativeB)
    assert abs ((absoluteB - absoluteA).total_seconds () - (relativeB - relativeA)) < 10e-6

def urls ():
    """ Build http/https URL """
    scheme = st.sampled_from (['http', 'https'])
    # Path must start with a slash
    pathSt = st.builds (lambda x: '/' + x, st.text ())
    args = st.fixed_dictionaries ({
            'scheme': scheme,
            'host': domains (),
            'port': st.one_of (st.none (), st.integers (min_value=1, max_value=2**16-1)),
            'path': pathSt,
            'query_string': st.text (),
            'fragment': st.text (),
            })
    return st.builds (lambda x: URL.build (**x), args)

def urlsStr ():
    return st.builds (lambda x: str (x), urls ())

asciiText = st.text (st.characters (min_codepoint=32, max_codepoint=126))

def chromeHeaders ():
    # token as defined by https://tools.ietf.org/html/rfc7230#section-3.2.6
    token = st.sampled_from('abcdefghijklmnopqrstuvwxyz0123456789!#$%&\'*+-.^_`|~')
    # XXX: the value should be asciiText without leading/trailing spaces
    return st.dictionaries (token, token)

def fixedDicts (fixed, dynamic):
    return st.builds (lambda x, y: x.update (y), st.fixed_dictionaries (fixed), st.lists (dynamic))

def chromeRequestWillBeSent (reqid, url):
    methodSt = st.sampled_from (['GET', 'POST', 'PUT', 'DELETE'])
    return st.fixed_dictionaries ({
            'requestId': reqid,
            'initiator': st.just ('Test'),
            'wallTime': timestamp,
            'timestamp': timestamp,
            'request': st.fixed_dictionaries ({
                'url': url,
                'method': methodSt,
                'headers': chromeHeaders (),
                # XXX: postData, hasPostData
                })
            })

def chromeResponseReceived (reqid, url):
    mimeTypeSt = st.one_of (st.none (), st.just ('text/html'))
    remoteIpAddressSt = st.one_of (st.none (), st.just ('127.0.0.1'))
    protocolSt = st.one_of (st.none (), st.just ('h2'))
    statusCodeSt = st.integers (min_value=100, max_value=999)
    typeSt = st.sampled_from (['Document', 'Stylesheet', 'Image', 'Media',
            'Font', 'Script', 'TextTrack', 'XHR', 'Fetch', 'EventSource',
            'WebSocket', 'Manifest', 'SignedExchange', 'Ping',
            'CSPViolationReport', 'Other'])
    return st.fixed_dictionaries ({
            'requestId': reqid,
            'timestamp': timestamp,
            'type': typeSt,
            'response': st.fixed_dictionaries ({
                'url': url,
                'requestHeaders': chromeHeaders (), # XXX: make this optional
                'headers': chromeHeaders (),
                'status': statusCodeSt,
                'statusText': asciiText,
                'mimeType': mimeTypeSt,
                'remoteIPAddress': remoteIpAddressSt,
                'protocol': protocolSt,
                })
            })

def chromeReqResp ():
    # XXX: will this gnerated the same url for all testcases?
    reqid = st.shared (st.text (), 'reqresp')
    url = st.shared (urlsStr (), 'reqresp')
    return st.tuples (chromeRequestWillBeSent (reqid, url),
            chromeResponseReceived (reqid, url))

def requestResponsePair ():
    def f (creq, cresp, hasPostData, reqBody, respBody):
        i = RequestResponsePair ()
        i.fromRequestWillBeSent (creq)
        i.request.hasPostData = hasPostData
        if hasPostData:
            i.request.body = reqBody

        if cresp is not None:
            i.fromResponseReceived (cresp)
            if respBody is not None:
                i.response.body = respBody
        return i

    bodySt = st.one_of (
            st.none (),
            st.builds (UnicodeBody, st.text ()),
            st.builds (Base64Body.fromBytes, st.binary ())
            )
    return st.builds (lambda reqresp, hasPostData, reqBody, respBody:
            f (reqresp[0], reqresp[1], hasPostData, reqBody, respBody),
            chromeReqResp (), st.booleans (), bodySt, bodySt)

@given(chromeReqResp ())
def test_requestResponsePair (creqresp):
    creq, cresp = creqresp

    item = RequestResponsePair ()

    assert item.id is None
    assert item.url is None
    assert item.request is None
    assert item.response is None

    item.fromRequestWillBeSent (creq)

    assert item.id == creq['requestId']
    url = URL (creq['request']['url'])
    assert item.url == url
    assert item.request is not None
    assert item.request.timestamp == datetime.utcfromtimestamp (creq['wallTime'])
    assert set (item.request.headers.keys ()) == set (creq['request']['headers'].keys ())
    assert item.response is None

    item.fromResponseReceived (cresp)

    # url will not be overwritten
    assert item.id == creq['requestId'] == cresp['requestId']
    assert item.url == url
    assert item.request is not None
    assert set (item.request.headers.keys ()) == set (cresp['response']['requestHeaders'].keys ())
    assert item.response is not None
    assert set (item.response.headers.keys ()) == set (cresp['response']['headers'].keys ())
    assert (item.response.timestamp - item.request.timestamp).total_seconds () - \
            (cresp['timestamp'] - creq['timestamp']) < 10e-6

@given(chromeReqResp ())
def test_requestResponsePair_eq (creqresp):
    creq, cresp = creqresp

    item = RequestResponsePair ()
    item2 = RequestResponsePair ()
    assert item == item
    assert item == item2

    item.fromRequestWillBeSent (creq)
    assert item != item2
    item2.fromRequestWillBeSent (creq)
    assert item == item
    assert item == item2

    item.fromResponseReceived (cresp)
    assert item != item2
    item2.fromResponseReceived (cresp)
    assert item == item
    assert item == item2

    # XXX: test for inequality with different parameters

### Google Chrome integration tests ###

serverUrl = URL.build (scheme='http', host='localhost', port=8080)
items = [
    RequestResponsePair (
        url=serverUrl.with_path ('/encoding/utf-8'),
        request=Request (method='GET'),
        response=Response (status=200, headers=CIMultiDict ([('Content-Type', 'text/html; charset=utf-8')]),
            body=UnicodeBody ('äöü'), mimeType='text/html')
        ),
    RequestResponsePair (
        url=serverUrl.with_path ('/encoding/latin1'),
        request=Request (method='GET'),
        response=Response (status=200, headers=CIMultiDict ([('Content-Type', 'text/html; charset=latin1')]),
            body=UnicodeBody ('äöü'), mimeType='text/html')
        ),
    RequestResponsePair (
        url=serverUrl.with_path ('/encoding/utf-16'),
        request=Request (method='GET'),
        response=Response (status=200, headers=CIMultiDict ([('Content-Type', 'text/html; charset=utf-16')]),
            body=UnicodeBody ('äöü'), mimeType='text/html')
        ),
    RequestResponsePair (
        url=serverUrl.with_path ('/encoding/ISO-8859-1'),
        request=Request (method='GET'),
        response=Response (status=200, headers=CIMultiDict ([('Content-Type', 'text/html; charset=ISO-8859-1')]),
            body=UnicodeBody ('äöü'), mimeType='text/html')
        ),
    RequestResponsePair (
        url=serverUrl.with_path ('/status/200'),
        request=Request (method='GET'),
        response=Response (status=200, headers=CIMultiDict ([('Content-Type', 'text/plain')]),
            body=b'',
            mimeType='text/plain'),
        ),
    # redirects never have a response body
    RequestResponsePair (
        url=serverUrl.with_path ('/status/301'),
        request=Request (method='GET'),
        response=Response (status=301,
            headers=CIMultiDict ([('Content-Type', 'text/plain'),
                ('Location', str (serverUrl.with_path ('/status/301/redirected')))]),
            body=None,
            mimeType='text/plain'),
        ),
    RequestResponsePair (
        url=serverUrl.with_path ('/image/png'),
        request=Request (method='GET'),
        response=Response (status=200, headers=CIMultiDict ([('Content-Type', 'image/png')]),
            body=Base64Body.fromBytes (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x00\x00\x00\x00:~\x9bU\x00\x00\x00\nIDAT\x08\x1dc\xf8\x0f\x00\x01\x01\x01\x006_g\x80\x00\x00\x00\x00IEND\xaeB`\x82'),
            mimeType='image/png'),
        ),
    RequestResponsePair (
        url=serverUrl.with_path ('/script/alert'),
        request=Request (method='GET'),
        response=Response (status=200, headers=CIMultiDict ([('Content-Type', 'text/html; charset=utf-8')]),
            body=UnicodeBody ('''<html><body><script>
window.addEventListener("beforeunload", function (e) {
    e.returnValue = "bye?";
    return e.returnValue;
});
alert("stopping here");
if (confirm("are you sure?") || prompt ("42?")) {
    window.location = "/nonexistent";
}
</script></body></html>'''), mimeType='text/html')
        ),
    ]

@pytest.mark.asyncio
# would be nice if we could use hypothesis here somehow
@pytest.mark.parametrize("golden", items)
async def test_integration_item (loader, golden):
    async def f (req):
        body = golden.response.body
        contentType = golden.response.headers.get ('content-type', '') if golden.response.headers is not None else ''
        charsetOff = contentType.find ('charset=')
        if isinstance (body, UnicodeBody) and charsetOff != -1:
            encoding = contentType[charsetOff+len ('charset='):]
            body = golden.response.body.decode ('utf-8').encode (encoding)
        return web.Response (body=body, status=golden.response.status,
                headers=golden.response.headers)

    app = web.Application ()
    app.router.add_route (golden.request.method, golden.url.path, f)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, serverUrl.host, serverUrl.port)
    try:
        await site.start()
    except Exception as e:
        pytest.skip (e)

    haveReqResp = False
    haveNavigated = False
    try:
        await loader.navigate (golden.url)

        it = loader.__aiter__ ()
        while True:
            try:
                item = await asyncio.wait_for (it.__anext__ (), timeout=1)
            except asyncio.TimeoutError:
                break
            # XXX: can only check the first req/resp right now (due to redirect)
            if isinstance (item, RequestResponsePair) and not haveReqResp:
                # we do not know this in advance
                item.request.initiator = None
                item.request.headers = None
                item.remoteIpAddress = None
                item.protocol = None
                item.resourceType = None

                if item.response:
                    assert item.response.statusText is not None
                    item.response.statusText = None

                    del item.response.headers['server']
                    del item.response.headers['content-length']
                    del item.response.headers['date']
                assert item == golden
                haveReqResp = True
            elif isinstance (item, FrameNavigated):
                # XXX: can’t check this, because of the redirect
                #assert item.url == golden.url
                haveNavigated = True
    finally:
        assert haveReqResp
        assert haveNavigated
        await runner.cleanup ()

def test_page_idle ():
    for v in (True, False):
        idle = PageIdle (v)
        assert bool (idle) == v


