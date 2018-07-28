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
from warcio.statusandheaders import StatusAndHeaders
from urllib.parse import urlsplit
from datetime import datetime

from warcio.timeutils import datetime_to_iso_date
from warcio.warcwriter import WARCWriter

from .util import packageUrl
from .controller import defaultSettings, EventHandler, ControllerStart
from .behavior import Script, DomSnapshotEvent, ScreenshotEvent
from .browser import Item

class WarcHandler (EventHandler):
    __slots__ = ('logger', 'writer', 'maxBodySize', 'documentRecords', 'log',
            'maxLogSize', 'logEncoding')

    def __init__ (self, fd,
            logger,
            maxBodySize=defaultSettings.maxBodySize):
        self.logger = logger
        self.writer = WARCWriter (fd, gzip=True)
        self.maxBodySize = maxBodySize

        self.logEncoding = 'utf-8'
        self.log = BytesIO ()
        # max log buffer size (bytes)
        self.maxLogSize = 500*1024

        # maps document urls to WARC record ids, required for DomSnapshotEvent
        # and ScreenshotEvent
        self.documentRecords = {}

    def __enter__ (self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._flushLogEntries ()

    def _writeRequest (self, item):
        writer = self.writer

        req = item.request
        resp = item.response
        url = urlsplit (resp['url'])

        path = url.path
        if url.query:
            path += '?' + url.query
        httpHeaders = StatusAndHeaders('{} {} HTTP/1.1'.format (req['method'], path),
                item.requestHeaders, protocol='HTTP/1.1', is_http_request=True)
        initiator = item.initiator
        warcHeaders = {
                'X-Chrome-Initiator': json.dumps (initiator),
                'WARC-Date': datetime_to_iso_date (datetime.utcfromtimestamp (item.chromeRequest['wallTime'])),
                }
        payload, payloadBase64Encoded = item.requestBody
        if payload:
            payload = BytesIO (payload)
            warcHeaders['X-Chrome-Base64Body'] = str (payloadBase64Encoded)
        record = writer.create_warc_record(req['url'], 'request',
                payload=payload, http_headers=httpHeaders,
                warc_headers_dict=warcHeaders)
        writer.write_record(record)

        return record.rec_headers['WARC-Record-ID']

    def _writeResponse (self, item, concurrentTo):
        # fetch the body
        reqId = item.id
        rawBody = None
        base64Encoded = False
        bodyTruncated = None
        if item.isRedirect:
            # redirects reuse the same request, thus we cannot safely retrieve
            # the body (i.e getResponseBody may return the new location’s
            # body).
            bodyTruncated = 'unspecified'
        elif item.encodedDataLength > self.maxBodySize:
            bodyTruncated = 'length'
            # check body size first, since we’re loading everything into memory
            self.logger.error ('body for {} too large {} vs {}'.format (reqId,
                    item.encodedDataLength, self.maxBodySize))
        else:
            try:
                rawBody, base64Encoded = item.body
            except ValueError:
                # oops, don’t know what went wrong here
                bodyTruncated = 'unspecified'

        # now the response
        resp = item.response
        warcHeaders = {
                'WARC-Concurrent-To': concurrentTo,
                'WARC-IP-Address': resp.get ('remoteIPAddress', ''),
                'X-Chrome-Protocol': resp.get ('protocol', ''),
                'X-Chrome-FromDiskCache': str (resp.get ('fromDiskCache')),
                'X-Chrome-ConnectionReused': str (resp.get ('connectionReused')),
                'WARC-Date': datetime_to_iso_date (datetime.utcfromtimestamp (
                        item.chromeRequest['wallTime']+
                        (item.chromeResponse['timestamp']-item.chromeRequest['timestamp']))),
                }
        if bodyTruncated:
            warcHeaders['WARC-Truncated'] = bodyTruncated
        else:
            warcHeaders['X-Chrome-Base64Body'] = str (base64Encoded)

        httpHeaders = StatusAndHeaders('{} {}'.format (resp['status'],
                item.statusText), item.responseHeaders,
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
            httpHeaders.replace_header ('content-length', '{:d}'.format (len (rawBody)))
            bodyIo = BytesIO (rawBody)
        else:
            bodyIo = BytesIO ()

        writer = self.writer
        record = writer.create_warc_record(resp['url'], 'response',
                warc_headers_dict=warcHeaders, payload=bodyIo,
                http_headers=httpHeaders)
        writer.write_record(record)

        if item.resourceType == 'Document':
            self.documentRecords[item.url] = record.rec_headers.get_header ('WARC-Record-ID')

    def _writeScript (self, item):
        writer = self.writer
        encoding = 'utf-8'
        record = writer.create_warc_record (packageUrl ('script/{}'.format (item.path)), 'metadata',
                payload=BytesIO (str (item).encode (encoding)),
                warc_headers_dict={'Content-Type': 'application/javascript; charset={}'.format (encoding)})
        writer.write_record (record)

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
            self.logger.error ('No document record found for {}'.format (url))
        return headers

    def _writeDomSnapshot (self, item):
        writer = self.writer

        warcHeaders = {'X-DOM-Snapshot': str (True),
                'X-Chrome-Viewport': item.viewport,
                'Content-Type': 'text/html; charset=utf-8',
                }

        self._addRefersTo (warcHeaders, item.url)

        record = writer.create_warc_record (item.url, 'conversion',
                payload=BytesIO (item.document),
                warc_headers_dict=warcHeaders)
        writer.write_record (record)

    def _writeScreenshot (self, item):
        writer = self.writer
        warcHeaders = {'Content-Type': 'image/png',
                'X-Crocoite-Screenshot-Y-Offset': str (item.yoff)}
        self._addRefersTo (warcHeaders, item.url)
        record = writer.create_warc_record (item.url, 'conversion',
                payload=BytesIO (item.data), warc_headers_dict=warcHeaders)
        writer.write_record (record)

    def _writeControllerStart (self, item):
        writer = self.writer
        warcinfo = writer.create_warcinfo_record (filename=None, info=item.payload)
        writer.write_record (warcinfo)

    def _flushLogEntries (self):
        writer = self.writer
        self.log.seek (0)
        # XXX: we should use the type continuation here
        record = writer.create_warc_record (packageUrl ('log'), 'resource', payload=self.log,
                warc_headers_dict={'Content-Type': 'text/plain; encoding={}'.format (self.logEncoding)})
        writer.write_record (record)
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
            self.logger.debug ('unknown event {}'.format (repr (item)))

