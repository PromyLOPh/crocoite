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
Classes writing data to WARC files
"""

import json, threading
from io import BytesIO
from datetime import datetime
from http.server import BaseHTTPRequestHandler

from warcio.timeutils import datetime_to_iso_date
from warcio.warcwriter import WARCWriter
from warcio.statusandheaders import StatusAndHeaders
from yarl import URL

from .util import StrJsonEncoder
from .controller import EventHandler, ControllerStart
from .behavior import Script, DomSnapshotEvent, ScreenshotEvent
from .browser import RequestResponsePair, UnicodeBody

# the official mimetype for json, according to https://tools.ietf.org/html/rfc8259
jsonMime = 'application/json'
# mime for javascript, according to https://tools.ietf.org/html/rfc4329#section-7.2
jsMime = 'application/javascript'

class WarcHandler (EventHandler):
    __slots__ = ('logger', 'writer', 'documentRecords', 'log',
            'maxLogSize', 'logEncoding', 'warcinfoRecordId')

    def __init__ (self, fd, logger):
        self.logger = logger
        self.writer = WARCWriter (fd, gzip=True)

        self.logEncoding = 'utf-8'
        self.log = BytesIO ()
        # max log buffer size (bytes)
        self.maxLogSize = 500*1024

        # maps document urls to WARC record ids, required for DomSnapshotEvent
        # and ScreenshotEvent
        self.documentRecords = {}
        # record id of warcinfo record
        self.warcinfoRecordId = None

    def __enter__ (self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._flushLogEntries ()

    def writeRecord (self, url, kind, payload, warc_headers_dict=None, http_headers=None):
        """
        Thin wrapper around writer.create_warc_record and writer.write_record.

        Adds default WARC headers.
        """
        assert url is None or isinstance (url, URL)

        d = {}
        if self.warcinfoRecordId:
            d['WARC-Warcinfo-ID'] = self.warcinfoRecordId
        d.update (warc_headers_dict)
        warc_headers_dict = d

        record = self.writer.create_warc_record (str (url) if url else '',
                kind,
                payload=payload,
                warc_headers_dict=warc_headers_dict,
                http_headers=http_headers)
        self.writer.write_record (record)

        return record

    def _writeRequest (self, item):
        logger = self.logger.bind (reqId=item.id)

        req = item.request
        url = item.url

        path = url.relative().with_fragment(None)
        httpHeaders = StatusAndHeaders(f'{req.method} {path} HTTP/1.1',
                req.headers, protocol='HTTP/1.1', is_http_request=True)
        warcHeaders = {
                # required to correlate request with log entries
                'X-Chrome-Request-ID': item.id,
                'WARC-Date': datetime_to_iso_date (req.timestamp),
                }

        body = item.request.body
        if item.request.hasPostData and body is None:
            # oops, don’t know what went wrong here
            logger.error ('requestBody missing',
                    uuid='ee9adc58-e723-4595-9feb-312a67ead6a0')
            warcHeaders['WARC-Truncated'] = 'unspecified'
        else:
            body = BytesIO (body)
        record = self.writeRecord (url, 'request',
                payload=body, http_headers=httpHeaders,
                warc_headers_dict=warcHeaders)
        return record.rec_headers['WARC-Record-ID']

    def _writeResponse (self, item, concurrentTo):
        # fetch the body
        reqId = item.id

        # now the response
        resp = item.response
        warcHeaders = {
                'WARC-Concurrent-To': concurrentTo,
                # required to correlate request with log entries
                'X-Chrome-Request-ID': item.id,
                'WARC-Date': datetime_to_iso_date (resp.timestamp),
                }
        # conditional WARC headers
        if item.remoteIpAddress:
            warcHeaders['WARC-IP-Address'] = item.remoteIpAddress

        # HTTP headers
        statusText = resp.statusText or \
                BaseHTTPRequestHandler.responses.get (
                resp.status, ('No status text available', ))[0]
        httpHeaders = StatusAndHeaders(f'{resp.status} {statusText}',
                resp.headers, protocol='HTTP/1.1')

        # Content is saved decompressed and decoded, remove these headers
        blacklistedHeaders = {'transfer-encoding', 'content-encoding'}
        for h in blacklistedHeaders:
            httpHeaders.remove_header (h)

        # chrome sends nothing but utf8 encoded text. Fortunately HTTP
        # headers take precedence over the document’s <meta>, thus we can
        # easily override those.
        contentType = resp.mimeType
        if contentType:
            if isinstance (resp.body, UnicodeBody):
                contentType += '; charset=utf-8'
            httpHeaders.replace_header ('Content-Type', contentType)

        # response body
        body = resp.body
        if body is None:
            warcHeaders['WARC-Truncated'] = 'unspecified'
        else:
            httpHeaders.replace_header ('Content-Length', str (len (body)))
            body = BytesIO (body)

        record = self.writeRecord (item.url, 'response',
                warc_headers_dict=warcHeaders, payload=body,
                http_headers=httpHeaders)

        if item.resourceType == 'Document':
            self.documentRecords[item.url] = record.rec_headers.get_header ('WARC-Record-ID')

    def _writeScript (self, item):
        writer = self.writer
        encoding = 'utf-8'
        # XXX: yes, we’re leaking information about the user here, but this is
        # the one and only source URL of the scripts.
        uri = URL(f'file://{item.abspath}') if item.path else None
        self.writeRecord (uri, 'resource',
                payload=BytesIO (str (item).encode (encoding)),
                warc_headers_dict={
                    'Content-Type': f'{jsMime}; charset={encoding}',
                    'X-Crocoite-Type': 'script',
                    })

    def _writeItem (self, item):
        assert item.request
        concurrentTo = self._writeRequest (item)
        # items that failed loading don’t have a response
        if item.response:
            self._writeResponse (item, concurrentTo)

    def _addRefersTo (self, headers, url):
        refersTo = self.documentRecords.get (url)
        if refersTo:
            headers['WARC-Refers-To'] = refersTo
        else:
            self.logger.error (f'No document record found for {url}')
        return headers

    def _writeDomSnapshot (self, item):
        writer = self.writer

        warcHeaders = {
                'X-Crocoite-Type': 'dom-snapshot',
                'X-Chrome-Viewport': item.viewport,
                'Content-Type': 'text/html; charset=utf-8',
                }

        self._addRefersTo (warcHeaders, item.url)

        self.writeRecord (item.url, 'conversion',
                payload=BytesIO (item.document),
                warc_headers_dict=warcHeaders)

    def _writeScreenshot (self, item):
        writer = self.writer
        warcHeaders = {
                'Content-Type': 'image/png',
                'X-Crocoite-Screenshot-Y-Offset': str (item.yoff),
                'X-Crocoite-Type': 'screenshot',
                }
        self._addRefersTo (warcHeaders, item.url)
        self.writeRecord (item.url, 'conversion',
                payload=BytesIO (item.data), warc_headers_dict=warcHeaders)

    def _writeControllerStart (self, item, encoding='utf-8'):
        payload = BytesIO (json.dumps (item.payload, indent=2, cls=StrJsonEncoder).encode (encoding))

        writer = self.writer
        warcinfo = self.writeRecord (None, 'warcinfo',
                warc_headers_dict={'Content-Type': f'{jsonMime}; encoding={encoding}'},
                payload=payload)
        self.warcinfoRecordId = warcinfo.rec_headers['WARC-Record-ID']

    def _flushLogEntries (self):
        if self.log.tell () > 0:
            writer = self.writer
            self.log.seek (0)
            warcHeaders = {
                    'Content-Type': f'application/json; encoding={self.logEncoding}',
                    'X-Crocoite-Type': 'log',
                    }
            self.writeRecord (None, 'metadata', payload=self.log,
                    warc_headers_dict=warcHeaders)
            self.log = BytesIO ()

    def _writeLog (self, item):
        """ Handle log entries, called by .logger.WarcHandlerConsumer only """
        self.log.write (item.encode (self.logEncoding))
        self.log.write (b'\n')
        if self.log.tell () > self.maxLogSize:
            self._flushLogEntries ()

    route = {Script: _writeScript,
            RequestResponsePair: _writeItem,
            DomSnapshotEvent: _writeDomSnapshot,
            ScreenshotEvent: _writeScreenshot,
            ControllerStart: _writeControllerStart,
            }

    async def push (self, item):
        for k, v in self.route.items ():
            if isinstance (item, k):
                v (self, item)
                break

