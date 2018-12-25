# Copyright (c) 2018 crocoite contributors
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

from tempfile import NamedTemporaryFile
import json, urllib
from operator import itemgetter

from warcio.archiveiterator import ArchiveIterator
from yarl import URL
from hypothesis import given, reproduce_failure
import hypothesis.strategies as st
import pytest

from .warc import WarcHandler
from .logger import Logger, WarcHandlerConsumer
from .controller import ControllerStart
from .behavior import Script, ScreenshotEvent, DomSnapshotEvent
from .browser import Item

def test_log ():
    logger = Logger ()

    with NamedTemporaryFile() as fd:
        with WarcHandler (fd, logger) as handler:
            warclogger = WarcHandlerConsumer (handler)
            logger.connect (warclogger)
            golden = []

            assert handler.log.tell () == 0
            golden.append (logger.info (foo=1, bar='baz', encoding='äöü⇔ΓΨ'))
            assert handler.log.tell () != 0

            handler.maxLogSize = 0
            golden.append (logger.info (bar=1, baz='baz'))
            # should flush the log
            assert handler.log.tell () == 0

        fd.seek (0)
        for it in ArchiveIterator (fd):
            headers = it.rec_headers
            assert headers['warc-type'] == 'resource'
            assert headers['warc-target-uri'].endswith (':log')
            assert headers['content-type'] == f'text/plain; encoding={handler.logEncoding}'

            while True:
                l = it.raw_stream.readline ()
                if not l:
                    break
                data = json.loads (l.strip ())
                assert data == golden.pop (0)

def hostname ():
    # XXX: find a better way to generate hostnames
    return st.text (alphabet=st.sampled_from('abcdefghijklmnopqrstuvwxyz0123456789-'), min_size=1, max_size=253)

def urls ():
    """ Build http/https URL """
    scheme = st.one_of (st.just ('http'), st.just ('https'))
    # Path must start with a slash
    pathSt = st.builds (lambda x: '/' + x, st.text ())
    args = st.fixed_dictionaries ({
            'scheme': scheme,
            'host': hostname (),
            'port': st.one_of (st.none (), st.integers (min_value=1, max_value=2**16-1)),
            'path': pathSt,
            'query_string': st.text (),
            'fragment': st.text (),
            })
    return st.builds (lambda x: URL.build (**x), args)

def item ():
    def f (url, requestBody, body, mimeType):
        i = Item ()
        # XXX: we really need some level of abstraction. Testing is a nightmare.
        i.setRequest ({'requestId': 'myid', 'initiator': 'Test', 'wallTime': 0, 'timestamp': 1, 'request': {'url': str (url), 'method': 'GET', 'headers': {'None': 'None'}}})
        i.setResponse ({'requestId': 'myid', 'timestamp': 2, 'type': 'Document', 'response': {'url': str (url), 'requestHeaders': {'foo': 'bar', 'Set-Cookie': 'line1\nline2'}, 'headers': {'Response': 'Headers', 'Content-Length': '12345'}, 'status': 200}})
        if mimeType is not None:
            i.chromeResponse['response']['mimeType'] = 'text/html'
        i.requestBody = requestBody
        i.body = body
        return i

    def failedItem (url):
        i = Item ()
        i.setRequest ({'requestId': 'myid', 'initiator': 'Test', 'wallTime': 0, 'timestamp': 1, 'request': {'url': str (url), 'method': 'GET', 'headers': {'None': 'None'}}})
        i.failed = True
        return i

    bodySt = st.one_of (st.none (), st.tuples (st.one_of (st.none (), st.binary ()), st.booleans ()))
    mimeTypeSt = st.one_of (st.none (), st.just ('text/html'))
    return st.one_of (
            st.builds (failedItem, urls ()),
            st.builds (f, urls (), bodySt, bodySt, mimeTypeSt),
            )

def jsonObject ():
    """ JSON-encodable objects """
    return st.dictionaries (st.text (), st.one_of (st.integers (), st.text ()))

def viewport ():
    return st.builds (lambda x, y: f'{x}x{y}', st.integers (), st.integers ())

def event ():
    return st.one_of (
            st.builds (ControllerStart, jsonObject ()),
            st.builds (Script.fromStr, st.text (), st.one_of(st.none (), st.text ())),
            st.builds (ScreenshotEvent, urls (), st.integers (), st.binary ()),
            st.builds (DomSnapshotEvent, urls (), st.builds (lambda x: x.encode ('utf-8'), st.text ()), viewport()),
            item (),
            )

@given (st.lists (event ()))
def test_push (golden):
    def checkWarcinfoId (headers):
        if lastWarcinfoRecordid is not None:
            assert headers['WARC-Warcinfo-ID'] == lastWarcinfoRecordid

    lastWarcinfoRecordid = None

    # null logger
    logger = Logger ()
    with NamedTemporaryFile() as fd:
        with WarcHandler (fd, logger) as handler:
            for g in golden:
                handler.push (g)

        fd.seek (0)
        it = iter (ArchiveIterator (fd))
        for g in golden:
            if isinstance (g, ControllerStart):
                rec = next (it)

                headers = rec.rec_headers
                assert headers['warc-type'] == 'warcinfo'
                assert headers['warc-target-uri'].endswith (':warcinfo')

                data = json.load (rec.raw_stream)
                assert data == g.payload

                lastWarcinfoRecordid = headers['warc-record-id']
                assert lastWarcinfoRecordid
            elif isinstance (g, Script):
                rec = next (it)

                headers = rec.rec_headers
                assert headers['warc-type'] == 'metadata'
                checkWarcinfoId (headers)
                path = g.path or '-'
                goldenpath = f':script/{urllib.parse.quote (path)}'
                assert headers['warc-target-uri'].endswith (goldenpath), (g.path, path, goldenpath)

                data = rec.raw_stream.read ().decode ('utf-8')
                assert data == g.data
            elif isinstance (g, ScreenshotEvent):
                # XXX: check refers-to header
                rec = next (it)

                headers = rec.rec_headers
                assert headers['warc-type'] == 'conversion'
                checkWarcinfoId (headers)
                assert URL (headers['warc-target-uri']) == g.url, (headers['warc-target-uri'], g.url)
                assert headers['warc-refers-to'] is None
                assert int (headers['X-Crocoite-Screenshot-Y-Offset']) == g.yoff

                assert rec.raw_stream.read () == g.data
            elif isinstance (g, DomSnapshotEvent):
                rec = next (it)

                headers = rec.rec_headers
                assert headers['warc-type'] == 'conversion'
                checkWarcinfoId (headers)
                assert URL (headers['warc-target-uri']) == g.url
                assert headers['warc-refers-to'] is None
                assert headers['X-DOM-Snapshot'] == 'True'

                assert rec.raw_stream.read () == g.document
            elif isinstance (g, Item):
                if g.failed:
                    continue

                rec = next (it)

                # request
                headers = rec.rec_headers
                assert headers['warc-type'] == 'request'
                checkWarcinfoId (headers)
                assert URL (headers['warc-target-uri']) == g.url
                assert headers['x-chrome-request-id'] == g.id
                
                assert sorted (rec.http_headers.headers, key=itemgetter (0)) == sorted (g.requestHeaders, key=itemgetter (0))
                if g.requestBody:
                    if g.requestBody[0] is None:
                        assert not rec.raw_stream.read ()
                    else:
                        assert rec.raw_stream.read () == g.requestBody[0], g.requestBody
                        assert str (headers['x-chrome-base64body'] or False) == str (g.requestBody[1]), (headers['x-chrome-base64body'], g.requestBody)
                else:
                    # body fetch failed
                    assert headers['warc-truncated'] == 'unspecified'

                # response
                rec = next (it)
                headers = rec.rec_headers
                httpheaders = rec.http_headers
                assert headers['warc-type'] == 'response'
                checkWarcinfoId (headers)
                assert URL (headers['warc-target-uri']) == g.url
                assert headers['x-chrome-request-id'] == g.id

                # these are checked separately
                blacklistedHeaders = {'content-type', 'content-length'}
                sortedHeaders = lambda l: sorted (filter (lambda x: x[0].lower() not in blacklistedHeaders, l), key=itemgetter (0))
                assert sortedHeaders (httpheaders.headers) == sortedHeaders (g.responseHeaders)

                expectedContentType = g.response.get ('mimeType')
                if expectedContentType is not None:
                    assert httpheaders['content-type'].startswith (expectedContentType)

                if g.body:
                    if g.body[0] is None:
                        assert not rec.raw_stream.read ()
                        #assert httpheaders['content-length'] == '0'
                    else:
                        assert rec.raw_stream.read () == g.body[0]
                        assert str (headers['x-chrome-base64body'] or False) == str (g.body[1])
                        assert httpheaders['content-length'] == str (len (g.body[0]))

                    # body is never truncated if it exists
                    assert headers['warc-truncated'] is None

                    # unencoded strings are converted to utf8
                    if not g.body[1] and httpheaders['content-type'] is not None:
                        assert httpheaders['content-type'].endswith ('; charset=utf-8')
                else:
                    # body fetch failed
                    assert headers['warc-truncated'] == 'unspecified'
                    # content-length header should be kept intact
            else:
                assert False, f"invalid golden type {type(g)}" # pragma: no cover

        # no further records
        with pytest.raises (StopIteration):
            next (it)

