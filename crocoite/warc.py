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

from warcio.timeutils import datetime_to_iso_date
from warcio.warcwriter import WARCWriter
from warcio.statusandheaders import StatusAndHeaders

from .util import packageUrl, StrJsonEncoder
from .controller import EventHandler, ControllerStart
from .behavior import Script, DomSnapshotEvent, ScreenshotEvent
from .browser import Item

class WarcHandler (EventHandler):
    __slots__ = ('logger', 'writer', 'documentRecords', 'log',
            'maxLogSize', 'logEncoding', 'warcinfoRecordId')

    def __init__ (self, fd,
            logger):
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

        d = {}
        if self.warcinfoRecordId:
            d['WARC-Warcinfo-ID'] = self.warcinfoRecordId
        d.update (warc_headers_dict)
        warc_headers_dict = d

        record = self.writer.create_warc_record (str (url), kind, payload=payload,
                warc_headers_dict=warc_headers_dict, http_headers=http_headers)
        self.writer.write_record (record)

        return record

    def _writeRequest (self, item):
        logger = self.logger.bind (reqId=item.id)

        req = item.request
        url = item.url

        path = url.relative().with_fragment(None)
        httpHeaders = StatusAndHeaders(f'{req["method"]} {path} HTTP/1.1',
                item.requestHeaders, protocol='HTTP/1.1', is_http_request=True)
        initiator = item.initiator
        warcHeaders = {
                'X-Chrome-Initiator': json.dumps (initiator),
                'X-Chrome-Request-ID': item.id,
                'WARC-Date': datetime_to_iso_date (datetime.utcfromtimestamp (item.chromeRequest['wallTime'])),
                }

        if item.requestBody is not None:
            payload, payloadBase64Encoded = item.requestBody
        else:
            # oops, don’t know what went wrong here
            logger.error ('requestBody missing', uuid='ee9adc58-e723-4595-9feb-312a67ead6a0')
            warcHeaders['WARC-Truncated'] = 'unspecified'
            payload = None

        if payload:
            payload = BytesIO (payload)
            warcHeaders['X-Chrome-Base64Body'] = str (payloadBase64Encoded)
        record = self.writeRecord (url, 'request',
                payload=payload, http_headers=httpHeaders,
                warc_headers_dict=warcHeaders)
        return record.rec_headers['WARC-Record-ID']

    def _writeResponse (self, item, concurrentTo):
        # fetch the body
        reqId = item.id
        rawBody = None
        base64Encoded = False
        bodyTruncated = None
        if item.isRedirect or item.body is None:
            # redirects reuse the same request, thus we cannot safely retrieve
            # the body (i.e getResponseBody may return the new location’s
            # body). No body available means we failed to retrieve it.
            bodyTruncated = 'unspecified'
        else:
            rawBody, base64Encoded = item.body

        # now the response
        resp = item.response
        warcHeaders = {
                'WARC-Concurrent-To': concurrentTo,
                'WARC-IP-Address': resp.get ('remoteIPAddress', ''),
                'X-Chrome-Protocol': resp.get ('protocol', ''),
                'X-Chrome-FromDiskCache': str (resp.get ('fromDiskCache')),
                'X-Chrome-ConnectionReused': str (resp.get ('connectionReused')),
                'X-Chrome-Request-ID': item.id,
                'WARC-Date': datetime_to_iso_date (datetime.utcfromtimestamp (
                        item.chromeRequest['wallTime']+
                        (item.chromeResponse['timestamp']-item.chromeRequest['timestamp']))),
                }
        if bodyTruncated:
            warcHeaders['WARC-Truncated'] = bodyTruncated
        else:
            warcHeaders['X-Chrome-Base64Body'] = str (base64Encoded)

        httpHeaders = StatusAndHeaders(f'{resp["status"]} {item.statusText}',
                item.responseHeaders,
                protocol='HTTP/1.1')

        # Content is saved decompressed and decoded, remove these headers
        blacklistedHeaders = {'transfer-encoding', 'content-encoding'}
        for h in blacklistedHeaders:
            httpHeaders.remove_header (h)

        # chrome sends nothing but utf8 encoded text. Fortunately HTTP
        # headers take precedence over the document’s <meta>, thus we can
        # easily override those.
        contentType = resp.get ('mimeType')
        if contentType:
            if not base64Encoded:
                contentType += '; charset=utf-8'
            httpHeaders.replace_header ('content-type', contentType)

        if rawBody is not None:
            httpHeaders.replace_header ('content-length', str (len (rawBody)))
            bodyIo = BytesIO (rawBody)
        else:
            bodyIo = BytesIO ()

        record = self.writeRecord (item.url, 'response',
                warc_headers_dict=warcHeaders, payload=bodyIo,
                http_headers=httpHeaders)

        if item.resourceType == 'Document':
            self.documentRecords[item.url] = record.rec_headers.get_header ('WARC-Record-ID')

    def _writeScript (self, item):
        writer = self.writer
        encoding = 'utf-8'
        self.writeRecord (packageUrl (f'script/{item.path}'), 'metadata',
                payload=BytesIO (str (item).encode (encoding)),
                warc_headers_dict={'Content-Type':
                f'application/javascript; charset={encoding}'})

    def _writeItem (self, item):
        if item.failed:
            # should have been handled by the logger already
            return

        concurrentTo = self._writeRequest (item)
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

        warcHeaders = {'X-DOM-Snapshot': str (True),
                'X-Chrome-Viewport': item.viewport,
                'Content-Type': 'text/html; charset=utf-8',
                }

        self._addRefersTo (warcHeaders, item.url)

        self.writeRecord (item.url, 'conversion',
                payload=BytesIO (item.document),
                warc_headers_dict=warcHeaders)

    def _writeScreenshot (self, item):
        writer = self.writer
        warcHeaders = {'Content-Type': 'image/png',
                'X-Crocoite-Screenshot-Y-Offset': str (item.yoff)}
        self._addRefersTo (warcHeaders, item.url)
        self.writeRecord (item.url, 'conversion',
                payload=BytesIO (item.data), warc_headers_dict=warcHeaders)

    def _writeControllerStart (self, item):
        payload = BytesIO (json.dumps (item.payload, indent=2, cls=StrJsonEncoder).encode ('utf-8'))

        writer = self.writer
        warcinfo = self.writeRecord (packageUrl ('warcinfo'), 'warcinfo',
                warc_headers_dict={'Content-Type': 'text/plain; encoding=utf-8'},
                payload=payload)
        self.warcinfoRecordId = warcinfo.rec_headers['WARC-Record-ID']

    def _flushLogEntries (self):
        writer = self.writer
        self.log.seek (0)
        # XXX: we should use the type continuation here
        self.writeRecord (packageUrl ('log'), 'resource', payload=self.log,
                warc_headers_dict={'Content-Type': f'text/plain; encoding={self.logEncoding}'})
        self.log = BytesIO ()

    def _writeLog (self, item):
        """ Handle log entries, called by .logger.WarcHandlerConsumer only """
        self.log.write (item.encode (self.logEncoding))
        self.log.write (b'\n')
        # instead of locking, check we’re running in the main thread
        if self.log.tell () > self.maxLogSize and \
                threading.current_thread () is threading.main_thread ():
            self._flushLogEntries ()

    route = {Script: _writeScript,
            Item: _writeItem,
            DomSnapshotEvent: _writeDomSnapshot,
            ScreenshotEvent: _writeScreenshot,
            ControllerStart: _writeControllerStart,
            }

    def push (self, item):
        processed = False
        for k, v in self.route.items ():
            if isinstance (item, k):
                v (self, item)
                processed = True
                break

        if not processed:
            self.logger.debug (f'unknown event {item!r}')

