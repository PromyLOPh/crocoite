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
from multidict import CIMultiDict
from hypothesis import given, reproduce_failure
import hypothesis.strategies as st
import pytest

from .warc import WarcHandler
from .logger import Logger, WarcHandlerConsumer
from .controller import ControllerStart
from .behavior import Script, ScreenshotEvent, DomSnapshotEvent
from .browser import RequestResponsePair, Base64Body, UnicodeBody
from .test_browser import requestResponsePair, urls

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
            assert headers['warc-type'] == 'metadata'
            assert 'warc-target-uri' not in headers
            assert headers['x-crocoite-type'] == 'log'
            assert headers['content-type'] == f'application/json; encoding={handler.logEncoding}'

            while True:
                l = it.raw_stream.readline ()
                if not l:
                    break
                data = json.loads (l.strip ())
                assert data == golden.pop (0)

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
            requestResponsePair (),
            )

@pytest.mark.asyncio
@given (st.lists (event ()))
async def test_push (golden):
    def checkWarcinfoId (headers):
        if lastWarcinfoRecordid is not None:
            assert headers['WARC-Warcinfo-ID'] == lastWarcinfoRecordid

    lastWarcinfoRecordid = None

    # null logger
    logger = Logger ()
    with open('/tmp/test.warc.gz', 'w+b') as fd:
        with WarcHandler (fd, logger) as handler:
            for g in golden:
                await handler.push (g)

        fd.seek (0)
        it = iter (ArchiveIterator (fd))
        for g in golden:
            if isinstance (g, ControllerStart):
                rec = next (it)

                headers = rec.rec_headers
                assert headers['warc-type'] == 'warcinfo'
                assert 'warc-target-uri' not in headers
                assert 'x-crocoite-type' not in headers

                data = json.load (rec.raw_stream)
                assert data == g.payload

                lastWarcinfoRecordid = headers['warc-record-id']
                assert lastWarcinfoRecordid
            elif isinstance (g, Script):
                rec = next (it)

                headers = rec.rec_headers
                assert headers['warc-type'] == 'resource'
                assert headers['content-type'] == 'application/javascript; charset=utf-8'
                assert headers['x-crocoite-type'] == 'script'
                checkWarcinfoId (headers)
                if g.path:
                    assert URL (headers['warc-target-uri']) == URL ('file://' + g.abspath)
                else:
                    assert 'warc-target-uri' not in headers

                data = rec.raw_stream.read ().decode ('utf-8')
                assert data == g.data
            elif isinstance (g, ScreenshotEvent):
                # XXX: check refers-to header
                rec = next (it)

                headers = rec.rec_headers
                assert headers['warc-type'] == 'conversion'
                assert headers['x-crocoite-type'] == 'screenshot'
                checkWarcinfoId (headers)
                assert URL (headers['warc-target-uri']) == g.url, (headers['warc-target-uri'], g.url)
                assert headers['warc-refers-to'] is None
                assert int (headers['X-Crocoite-Screenshot-Y-Offset']) == g.yoff

                assert rec.raw_stream.read () == g.data
            elif isinstance (g, DomSnapshotEvent):
                rec = next (it)

                headers = rec.rec_headers
                assert headers['warc-type'] == 'conversion'
                assert headers['x-crocoite-type'] == 'dom-snapshot'
                checkWarcinfoId (headers)
                assert URL (headers['warc-target-uri']) == g.url
                assert headers['warc-refers-to'] is None

                assert rec.raw_stream.read () == g.document
            elif isinstance (g, RequestResponsePair):
                rec = next (it)

                # request
                headers = rec.rec_headers
                assert headers['warc-type'] == 'request'
                assert 'x-crocoite-type' not in headers
                checkWarcinfoId (headers)
                assert URL (headers['warc-target-uri']) == g.url
                assert headers['x-chrome-request-id'] == g.id
                
                assert CIMultiDict (rec.http_headers.headers) == g.request.headers
                if g.request.hasPostData:
                    if g.request.body is not None:
                        assert rec.raw_stream.read () == g.request.body
                    else:
                        # body fetch failed
                        assert headers['warc-truncated'] == 'unspecified'
                        assert not rec.raw_stream.read ()
                else:
                    assert not rec.raw_stream.read ()

                # response
                if g.response:
                    rec = next (it)
                    headers = rec.rec_headers
                    httpheaders = rec.http_headers
                    assert headers['warc-type'] == 'response'
                    checkWarcinfoId (headers)
                    assert URL (headers['warc-target-uri']) == g.url
                    assert headers['x-chrome-request-id'] == g.id
                    assert 'x-crocoite-type' not in headers

                    # these are checked separately
                    filteredHeaders = CIMultiDict (httpheaders.headers)
                    for b in {'content-type', 'content-length'}:
                        if b in g.response.headers:
                            g.response.headers.popall (b)
                        if b in filteredHeaders:
                            filteredHeaders.popall (b)
                    assert filteredHeaders == g.response.headers

                    expectedContentType = g.response.mimeType
                    if expectedContentType is not None:
                        assert httpheaders['content-type'].startswith (expectedContentType)

                    if g.response.body is not None:
                        assert rec.raw_stream.read () == g.response.body
                        assert httpheaders['content-length'] == str (len (g.response.body))
                        # body is never truncated if it exists
                        assert headers['warc-truncated'] is None

                        # unencoded strings are converted to utf8
                        if isinstance (g.response.body, UnicodeBody) and httpheaders['content-type'] is not None:
                            assert httpheaders['content-type'].endswith ('; charset=utf-8')
                    else:
                        # body fetch failed
                        assert headers['warc-truncated'] == 'unspecified'
                        assert not rec.raw_stream.read ()
                        # content-length header should be kept intact
            else:
                assert False, f"invalid golden type {type(g)}" # pragma: no cover

        # no further records
        with pytest.raises (StopIteration):
            next (it)

