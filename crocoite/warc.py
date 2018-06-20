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

import logging
import json
from io import BytesIO
from warcio.statusandheaders import StatusAndHeaders
from urllib.parse import urlsplit
from logging.handlers import BufferingHandler
from datetime import datetime
from threading import Thread
from queue import Queue

from warcio.timeutils import datetime_to_iso_date
from warcio.warcwriter import WARCWriter

from .util import packageUrl
from .controller import defaultSettings, EventHandler, ControllerStart
from .behavior import Script, DomSnapshotEvent, ScreenshotEvent
from .browser import Item

class WarcHandler (EventHandler):
    __slots__ = ('logger', 'writer', 'maxBodySize')

    def __init__ (self, fd,
            logger=logging.getLogger(__name__),
            maxBodySize=defaultSettings.maxBodySize):
        self.logger = logger
        self.writer = WARCWriter (fd, gzip=True)
        self.maxBodySize = maxBodySize

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

    def _getBody (self, item):
        reqId = item.id

        rawBody = b''
        base64Encoded = False
        if item.isRedirect:
            # redirects reuse the same request, thus we cannot safely retrieve
            # the body (i.e getResponseBody may return the new location’s
            # body). This is fine.
            pass
        elif item.encodedDataLength > self.maxBodySize:
            # check body size first, since we’re loading everything into memory
            raise ValueError ('body for {} too large {} vs {}'.format (reqId,
                    item.encodedDataLength, self.maxBodySize))
        else:
            rawBody, base64Encoded = item.body
        return rawBody, base64Encoded

    def _writeResponse (self, item, concurrentTo, rawBody, base64Encoded):
        writer = self.writer
        reqId = item.id
        resp = item.response

        # now the response
        warcHeaders = {
                'WARC-Concurrent-To': concurrentTo,
                'WARC-IP-Address': resp.get ('remoteIPAddress', ''),
                'X-Chrome-Protocol': resp.get ('protocol', ''),
                'X-Chrome-FromDiskCache': str (resp.get ('fromDiskCache')),
                'X-Chrome-ConnectionReused': str (resp.get ('connectionReused')),
                'X-Chrome-Base64Body': str (base64Encoded),
                'WARC-Date': datetime_to_iso_date (datetime.utcfromtimestamp (
                        item.chromeRequest['wallTime']+
                        (item.chromeResponse['timestamp']-item.chromeRequest['timestamp']))),
                }

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

        httpHeaders.replace_header ('content-length', '{:d}'.format (len (rawBody)))

        record = writer.create_warc_record(resp['url'], 'response',
                warc_headers_dict=warcHeaders, payload=BytesIO (rawBody),
                http_headers=httpHeaders)
        writer.write_record(record)

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
        try:
            # write neither request nor response if we cannot retrieve the body
            rawBody, base64Encoded = self._getBody (item)
            concurrentTo = self._writeRequest (item)
            self._writeResponse (item, concurrentTo, rawBody, base64Encoded)
        except ValueError as e:
            self.logger.error (e.args[0])

    def _writeDomSnapshot (self, item):
        writer = self.writer
        httpHeaders = StatusAndHeaders('200 OK', {}, protocol='HTTP/1.1')
        record = writer.create_warc_record (item.url, 'response',
                payload=BytesIO (item.document),
                http_headers=httpHeaders,
                warc_headers_dict={'X-DOM-Snapshot': str (True),
                        'X-Chrome-Viewport': item.viewport})
        writer.write_record (record)

    def _writeScreenshot (self, item):
        writer = self.writer
        url = packageUrl ('screenshot-{}-{}.png'.format (0, item.yoff))
        record = writer.create_warc_record (url, 'resource',
                payload=BytesIO (item.data), warc_headers_dict={'Content-Type': 'image/png'})
        writer.write_record (record)

    def _writeControllerStart (self, item):
        writer = self.writer
        warcinfo = writer.create_warcinfo_record (filename=None, info=item.payload)
        writer.write_record (warcinfo)

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

