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
from operator import itemgetter
from io import BytesIO
import pytest
from warcio.archiveiterator import ArchiveIterator
from warcio.warcwriter import WARCWriter
from warcio.statusandheaders import StatusAndHeaders

from .tools import mergeWarc

@pytest.fixture
def writer():
    return WARCWriter (NamedTemporaryFile(), gzip=True)

def recordsEqual(golden, underTest):
    for a, b in zip (golden, underTest):
        # record ids are not predictable, so we cannot compare them
        a.rec_headers.remove_header('WARC-Record-Id')
        a.rec_headers.remove_header('WARC-Block-Digest')
        b.rec_headers.remove_header('WARC-Record-Id')
        b.rec_headers.remove_header('WARC-Block-Digest')
        aheader = sorted(a.rec_headers.headers, key=itemgetter(0))
        bheader = sorted(b.rec_headers.headers, key=itemgetter(0))
        assert aheader == bheader

def test_unmodified(writer):
    """
    Single request/response pair, no revisits
    """

    records = []
    httpHeaders = StatusAndHeaders('GET / HTTP/1.1', {}, is_http_request=True)
    warcHeaders = {}
    record = writer.create_warc_record ('http://example.com/', 'request', payload=BytesIO(b'foobar'),
            warc_headers_dict=warcHeaders, http_headers=httpHeaders)
    records.append (record)

    httpHeaders = StatusAndHeaders('200 OK', {}, protocol='HTTP/1.1')
    record = writer.create_warc_record ('http://example.com/', 'response', payload=BytesIO(b'data'),
            warc_headers_dict=warcHeaders, http_headers=httpHeaders)
    records.append (record)

    for r in records:
        writer.write_record (r)

    output = NamedTemporaryFile()
    mergeWarc ([writer.out.name], output)

    output.seek(0)
    recordsEqual (records, ArchiveIterator (output))

def test_different_payload(writer):
    """
    Duplicate URL, but different payload
    """

    records = []
    for i in range (2):
        httpHeaders = StatusAndHeaders('GET / HTTP/1.1', {}, is_http_request=True)
        warcHeaders = {}
        record = writer.create_warc_record ('http://example.com/', 'request', payload=BytesIO(b'foobar'),
                warc_headers_dict=warcHeaders, http_headers=httpHeaders)
        records.append (record)

        httpHeaders = StatusAndHeaders('200 OK', {}, protocol='HTTP/1.1')
        record = writer.create_warc_record ('http://example.com/', 'response',
                payload=BytesIO('data{}'.format(i).encode ('utf8')),
                warc_headers_dict=warcHeaders, http_headers=httpHeaders)
        records.append (record)

    for r in records:
        writer.write_record (r)

    output = NamedTemporaryFile()
    mergeWarc ([writer.out.name], output)

    output.seek(0)
    recordsEqual (records, ArchiveIterator (output))

def makeRevisit(writer, ref, dup):
    """ Make revisit record for reference """
    dupHeaders = dup.rec_headers
    refHeaders = ref.rec_headers
    httpHeaders = StatusAndHeaders('200 OK', {}, protocol='HTTP/1.1')
    record = writer.create_revisit_record (dupHeaders.get_header('WARC-Target-URI'),
            digest=refHeaders.get_header('WARC-Payload-Digest'),
            refers_to_uri=refHeaders.get_header('WARC-Target-URI'),
            refers_to_date=refHeaders.get_header('WARC-Date'))
    record.rec_headers.add_header ('WARC-Refers-To', refHeaders.get_header('WARC-Record-ID'))
    record.rec_headers.add_header ('WARC-Truncated', 'length')
    record.rec_headers.add_header ('Content-Length', '0')
    # XXX: added by warcio, but this seems wrong
    record.rec_headers.add_header ('Content-Type', 'application/http; msgtype=response')
    return record

def test_resp_revisit_same_url(writer):
    """
    Duplicate record for the same URL, creates a revisit
    """

    records = []
    for i in range (2):
        httpHeaders = StatusAndHeaders('GET / HTTP/1.1', {}, is_http_request=True)
        warcHeaders = {}
        record = writer.create_warc_record ('http://example.com/', 'request', payload=BytesIO(b'foobar'),
                warc_headers_dict=warcHeaders, http_headers=httpHeaders)
        records.append (record)

        httpHeaders = StatusAndHeaders('200 OK', {}, protocol='HTTP/1.1')
        record = writer.create_warc_record ('http://example.com/', 'response', payload=BytesIO(b'data'),
                warc_headers_dict=warcHeaders, http_headers=httpHeaders)
        records.append (record)

    for r in records:
        writer.write_record (r)

    dup = records.pop ()
    ref = records[1]
    records.append (makeRevisit (writer, ref, dup))

    output = NamedTemporaryFile()
    mergeWarc ([writer.out.name], output)

    output.seek(0)
    recordsEqual (records, ArchiveIterator (output))

def test_resp_revisit_other_url(writer):
    """
    Duplicate record for different URL, creates a revisit
    """

    records = []

    httpHeaders = StatusAndHeaders('GET / HTTP/1.1', {}, is_http_request=True)
    warcHeaders = {}
    record = writer.create_warc_record ('http://example.com/', 'request', payload=BytesIO(b'foobar'),
            warc_headers_dict=warcHeaders, http_headers=httpHeaders)
    records.append (record)

    httpHeaders = StatusAndHeaders('200 OK', {}, protocol='HTTP/1.1')
    record = writer.create_warc_record ('http://example.com/', 'response', payload=BytesIO(b'data'),
            warc_headers_dict=warcHeaders, http_headers=httpHeaders)
    records.append (record)

    httpHeaders = StatusAndHeaders('GET / HTTP/1.1', {}, is_http_request=True)
    warcHeaders = {}
    record = writer.create_warc_record ('http://example.com/one', 'request', payload=BytesIO(b'foobar'),
            warc_headers_dict=warcHeaders, http_headers=httpHeaders)
    records.append (record)

    httpHeaders = StatusAndHeaders('200 OK', {}, protocol='HTTP/1.1')
    record = writer.create_warc_record ('http://example.com/one', 'response', payload=BytesIO(b'data'),
            warc_headers_dict=warcHeaders, http_headers=httpHeaders)
    records.append (record)

    for r in records:
        writer.write_record (r)

    dup = records.pop ()
    ref = records[1]
    records.append (makeRevisit (writer, ref, dup))

    output = NamedTemporaryFile()
    mergeWarc ([writer.out.name], output)

    output.seek(0)
    recordsEqual (records, ArchiveIterator (output))

