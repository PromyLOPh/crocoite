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

import os, sys, json
import pychrome
from urllib.parse import urlsplit
from warcio.warcwriter import WARCWriter
from warcio.statusandheaders import StatusAndHeaders
from base64 import b64decode
import logging
from logging.handlers import BufferingHandler
from http.server import BaseHTTPRequestHandler
from io import BytesIO
import argparse
import tempfile, random

from html5lib.treewalkers.base import TreeWalker
from html5lib.filters.base import Filter
from html5lib.serializer import HTMLSerializer
from html5lib import constants

logger = logging.getLogger(__name__)

# 10 MB, encoded! (i.e. actual data can be larger due to compression)
maxBodySize = 10*1024*1024

def packageData (path):
    """
    Locate package data, see setup.py’s data_files
    """
    return os.path.join (os.path.dirname (__file__), 'data', path)

def packageUrl (path):
    """
    Create URL for package data stored into WARC
    """
    return 'urn:' + __package__ + ':' + path

def randomString (length=None, chars='abcdefghijklmnopqrstuvwxyz'):
    if length is None:
        length = random.randint (16, 32)
    return ''.join (map (lambda x: random.choice (chars), range (length)))

class WARCLogHandler (BufferingHandler):
    """
    Buffered log handler, flushing to warcio
    """

    contentType = 'text/plain; charset=utf-8'

    def __init__ (self, capacity, warcfile):
        BufferingHandler.__init__ (self, capacity)
        self.warcfile = warcfile

    def flush (self):
        self.acquire ()
        try:
            buf = ''
            for record in self.buffer:
                buf += self.format (record)
                buf += '\n'
            # XXX: record type?
            record = self.warcfile.create_warc_record (
                    packageUrl ('log'), 'metadata',
                    payload=BytesIO (buf.encode ('utf8')),
                    warc_headers_dict={'Content-Type': self.contentType})
            self.warcfile.write_record(record)
            self.buffer = []
        finally:
            self.release ()

class ChromeTreeWalker (TreeWalker):
    """
    Recursive html5lib TreeWalker for Google Chrome method DOM.getDocument
    """

    def recurse (self, node):
        name = node['nodeName']
        if name.startswith ('#'):
            if name == '#text':
                yield from self.text (node['nodeValue'])
            elif name == '#comment':
                yield self.comment (node['nodeValue'])
            elif name == '#document':
                for child in node.get ('children', []):
                    yield from self.recurse (child)
        else:
            default_namespace = constants.namespaces["html"]
            attributes = node.get ('attributes', [])
            convertedAttr = {}
            for i in range (0, len (attributes), 2):
                convertedAttr[(default_namespace, attributes[i])] = attributes[i+1]
            yield self.startTag (default_namespace, name, convertedAttr)
            for child in node.get ('children', []):
                yield from self.recurse (child)
            yield self.endTag ('', name)

    def __iter__ (self):
        from pprint import pprint
        assert self.tree['nodeName'] == '#document'
        return self.recurse (self.tree)

class StripTagFilter (Filter):
    """
    Remove arbitrary tags
    """

    def __init__ (self, source, tags):
        Filter.__init__ (self, source)
        self.tags = set (map (str.lower, tags))

    def __iter__(self):
        delete = 0
        for token in Filter.__iter__(self):
            tokenType = token['type']
            if tokenType == 'StartTag':
                if delete > 0 or token['name'].lower () in self.tags:
                    delete += 1
            if delete == 0:
                yield token
            if tokenType == 'EndTag' and delete > 0:
                delete -= 1

def main ():
    def getStatusText (response):
        text = response.get ('statusText')
        if text:
            return text
        text = BaseHTTPRequestHandler.responses.get (response['status'])
        if text:
            return text[0]
        return 'No status text available'

    def requestWillBeSent (**kwargs):
        req = kwargs.get ('request')
        reqId = kwargs['requestId']
        url = urlsplit (req['url'])
        if url.scheme in ('http', 'https'):
            logger.debug ('sending {} {}'.format (reqId, req['url']))
            if reqId in requests:
                redirectResp = kwargs.get ('redirectResponse')
                if redirectResp:
                    requests[reqId]['response'] = redirectResp
                    # XXX: can we retrieve the original response body right now?
                    itemToWarc (reqId, requests[reqId], ignoreBody=True)
                else:
                    logger.warn ('request {} already exists, overwriting.'.format (reqId))
            requests[reqId] = {}
            requests[reqId]['request'] = req
            requests[reqId]['initiator'] = kwargs['initiator']
        else:
            logger.warn ('request: ignoring scheme {}'.format (url.scheme))

    def responseReceived (**kwargs):
        resp = kwargs['response']
        reqId = kwargs['requestId']
        url = urlsplit (resp['url'])
        if url.scheme in ('http', 'https') and reqId in requests:
            logger.debug ('response {} {}'.format (reqId, resp['url']))
            requests[reqId]['response'] = resp
        else:
            logger.warn ('response: ignoring scheme {}'.format (url.scheme))

    def loadEventFired (**kwargs):
        """
        Equivalent to DOM ready JavaScript event
        """
        root = tab.DOM.getDocument ()
        rootId = root['root']['nodeId']
        links = tab.DOM.querySelectorAll (nodeId=rootId, selector='a')
        #for i in links['nodeIds']:
        #    print ('link', tab.DOM.getAttributes (nodeId=i))

    def loadingFinished (**kwargs):
        """
        Item was fully loaded. For some items the request body is not available
        when responseReceived is fired, thus move everything here.
        """
        reqId = kwargs['requestId']
        # we never recorded this request
        if reqId not in requests:
            return
        item = requests[reqId]
        req = item['request']
        resp = item['response']
        url = urlsplit (resp['url'])
        if url.scheme in ('http', 'https'):
            logger.debug ('finished {} {}'.format (reqId, req['url']))
            itemToWarc (reqId, item, kwargs['encodedDataLength'])
            del requests[reqId]

    def itemToWarc (reqId, item, encodedDataLength=0, ignoreBody=False):
        req = item['request']
        resp = item['response']
        url = urlsplit (resp['url'])

        # overwrite request headers with those actually sent
        newReqHeaders = resp.get ('requestHeaders')
        if newReqHeaders:
            req['headers'] = newReqHeaders

        postData = req.get ('postData')
        if postData:
            postData = BytesIO (postData.encode ('utf8'))
        path = url.path
        if url.query:
            path += '?' + url.query
        httpHeaders = StatusAndHeaders('{} {} HTTP/1.1'.format (req['method'], path),
                req['headers'], protocol='HTTP/1.1', is_http_request=True)
        initiator = item['initiator']
        warcHeaders = {
                'X-Chrome-Initiator': json.dumps (initiator),
                }
        record = writer.create_warc_record(req['url'], 'request',
                payload=postData, http_headers=httpHeaders,
                warc_headers_dict=warcHeaders)
        writer.write_record(record)
        concurrentTo = record.rec_headers['WARC-Record-ID']

        # check body size first, since we’re loading everything into memory
        if encodedDataLength < maxBodySize:
            try:
                if ignoreBody:
                    rawBody = b''
                    base64Encoded = True
                else:
                    body = tab.Network.getResponseBody (requestId=reqId)
                    rawBody = body['body']
                    base64Encoded = body['base64Encoded']
                    if base64Encoded:
                        rawBody = b64decode (rawBody)
                    else:
                        rawBody = rawBody.encode ('utf8')

                httpHeaders = StatusAndHeaders('{} {}'.format (resp['status'],
                        getStatusText (resp)), resp['headers'], protocol='HTTP/1.1')

                # Content is saved decompressed and decoded, remove these headers
                blacklistedHeaders = {'transfer-encoding', 'content-encoding'}
                for h in blacklistedHeaders:
                    httpHeaders.remove_header (h)

                # chrome sends nothing but utf8 encoded text. Fortunately HTTP
                # headers take precedence over the document’s <meta>, thus we can
                # easily override those.
                contentType = resp['mimeType']
                if not base64Encoded:
                    contentType += '; charset=utf-8'
                httpHeaders.replace_header ('content-type', contentType)

                httpHeaders.replace_header ('content-length', '{:d}'.format (len (rawBody)))

                warcHeaders = {
                        'WARC-Concurrent-To': concurrentTo,
                        'WARC-IP-Address': resp.get ('remoteIPAddress', ''),
                        'X-Chrome-Protocol': resp.get ('protocol', ''),
                        'X-Chrome-FromDiskCache': str (resp.get ('fromDiskCache')),
                        'X-Chrome-ConnectionReused': str (resp.get ('connectionReused')),
                        'X-Chrome-Base64Body': str (base64Encoded),
                        }
                record = writer.create_warc_record(resp['url'], 'response',
                        warc_headers_dict=warcHeaders, payload=BytesIO (rawBody),
                        http_headers=httpHeaders)
                writer.write_record(record)
            except pychrome.exceptions.CallMethodException:
                logger.error ('no data for {} {} {}'.format (resp['url'],
                        resp['status'], reqId))
        else:
            logger.warn ('body for {} is too large, {} bytes'.format (resp['url'], kwargs['encodedDataLength']))

    def loadingFailed (**kwargs):
        reqId = kwargs['requestId']
        logger.debug ('failed {} {}'.format (reqId, kwargs['errorText'], kwargs.get ('blockedReason')))
        if reqId in requests:
            del requests[reqId]

    def getFormattedViewportMetrics (tab):
        layoutMetrics = tab.Page.getLayoutMetrics ()
        # XXX: I’m not entirely sure which one we should use here
        return '{}x{}'.format (layoutMetrics['layoutViewport']['clientWidth'],
                    layoutMetrics['layoutViewport']['clientHeight'])

    def writeDOMSnapshot (tab, writer):
        """
        Get a DOM snapshot of tab and write it to WARC.

        We could use DOMSnapshot.getSnapshot here, but the API is not stable
        yet. Also computed styles are not really necessary here.

        XXX: Currently writes a response, when it should use “resource”. pywb
        can’t handle that though.
        """
        viewport = getFormattedViewportMetrics (tab)
        dom = tab.DOM.getDocument (depth=-1)
        # remove script, to make the page static and noscript, because at the
        # time we took the snapshot scripts were enabled
        stream = StripTagFilter (ChromeTreeWalker (dom['root']), ['script', 'noscript'])
        serializer = HTMLSerializer ()
        httpHeaders = StatusAndHeaders('200 OK', {}, protocol='HTTP/1.1')
        record = writer.create_warc_record (dom['root']['documentURL'], 'response',
                payload=BytesIO (serializer.render (stream, 'utf8')),
                http_headers=httpHeaders,
                warc_headers_dict={'X-DOM-Snapshot': str (True),
                        'X-Chrome-Viewport': viewport})
        writer.write_record (record)

    def emulateScreenMetrics (tab):
        """
        Emulate different screen sizes, causing the site to fetch assets (img
        srcset and css, for example) for different screen resolutions.
        """
        sizes = [
                {'width': 1920, 'height': 1080, 'deviceScaleFactor': 1.5, 'mobile': False},
                {'width': 1920, 'height': 1080, 'deviceScaleFactor': 2, 'mobile': False},
                # very dense display
                {'width': 1920, 'height': 1080, 'deviceScaleFactor': 4, 'mobile': False},
                # just a few samples:
                # 1st gen iPhone (portrait mode)
                {'width': 320, 'height': 480, 'deviceScaleFactor': 1.8, 'mobile': True},
                # 6th gen iPhone (portrait mode)
                {'width': 750, 'height': 1334, 'deviceScaleFactor': 326/90, 'mobile': True},
                ]
        for s in sizes:
            tab.Emulation.setDeviceMetricsOverride (**s)
            tab.wait (1)
        # wait until assets finished loading
        while len (requests) != 0:
            tab.wait (1)

    logging.basicConfig (level=logging.DEBUG)

    parser = argparse.ArgumentParser(description='Save website to WARC using Google Chrome.')
    parser.add_argument('--browser', default='http://127.0.0.1:9222', help='DevTools URL')
    parser.add_argument('--timeout', default=10, type=int, help='Maximum time for archival')
    parser.add_argument('--idle-timeout', default=2, type=int, help='Maximum idle seconds (i.e. no requests)', dest='idleTimeout')
    parser.add_argument('--onload', action='append', help='')
    parser.add_argument('--log-buffer', default=1000, type=int, dest='logBuffer')
    parser.add_argument('--keep-tab', action='store_true', default=False, dest='keepTab', help='Keep tab open')
    parser.add_argument('url', help='Website URL')
    parser.add_argument('output', help='WARC filename')

    args = parser.parse_args ()

    stopVarname = '__' + __package__ + '_stop__'
    # avoid sites messing with our scripts by using a random stop variable name
    newStopVarname = randomString ()
    onload = ['var {} = false;\n'.format (newStopVarname)]
    for path in args.onload:
        if not os.path.exists (path):
            # search for defaults scripts in package data directory
            path = packageData (path)
        with open (path, 'r') as fd:
            onload.append (fd.read ().replace (stopVarname, newStopVarname))
    onload = '\n'.join (onload)
    stopVarname = newStopVarname

    # temporary store for requests
    requests = {}

    # create a browser instance
    browser = pychrome.Browser(url=args.browser)

    # create a tab
    tab = browser.new_tab()

    # setup callbacks
    tab.Network.requestWillBeSent = requestWillBeSent
    tab.Network.responseReceived = responseReceived
    tab.Network.loadingFinished = loadingFinished
    tab.Network.loadingFailed = loadingFailed
    tab.Page.loadEventFired = loadEventFired

    # start the tab
    tab.start()

    fd = open (args.output, 'wb')
    writer = WARCWriter (fd, gzip=True)
    version = tab.Browser.getVersion ()
    payload = {
            'software': __package__,
            'browser': version['product'],
            'useragent': version['userAgent'],
            'viewport': getFormattedViewportMetrics (tab),
            }
    warcinfo = writer.create_warcinfo_record (filename=None, info=payload)
    writer.write_record (warcinfo)

    warcLogger = WARCLogHandler (args.logBuffer, writer)
    logger.addHandler (warcLogger)

    # save onload script
    record = writer.create_warc_record (packageUrl ('onload'), 'metadata',
            payload=BytesIO (onload.encode ('utf8')),
            warc_headers_dict={'Content-Type': 'application/javascript; charset=utf-8'})
    writer.write_record (record)

    # enable events
    tab.Network.enable()
    tab.Page.enable ()
    tab.Network.clearBrowserCache ()
    if tab.Network.canClearBrowserCookies ()['result']:
        tab.Network.clearBrowserCookies ()
    # inject our custom javascript to the page before loading
    tab.Page.addScriptToEvaluateOnNewDocument (source=onload)

    tab.Page.navigate(url=args.url)

    idleTimeout = 0
    for i in range (0, args.timeout):
        tab.wait (1)
        if len (requests) == 0:
            idleTimeout += 1
            if idleTimeout > args.idleTimeout:
                break
        else:
            idleTimeout = 0

    # get ready for snapshot: stop loading and scripts, disable events
    tab.Runtime.evaluate (expression='{} = true; window.scrollTo (0, 0);'.format (stopVarname), returnByValue=True)
    # if we stopped due to timeout, wait for remaining assets
    while len (requests) != 0:
        tab.wait (1)

    emulateScreenMetrics (tab)

    tab.Page.stopLoading ()
    tab.Network.disable ()
    tab.Page.disable ()
    tab.Network.requestWillBeSent = None
    tab.Network.responseReceived = None
    tab.Network.loadingFinished = None
    tab.Network.loadingFailed = None
    tab.Page.loadEventFired = None

    writeDOMSnapshot (tab, writer)

    tab.stop()
    if not args.keepTab:
        browser.close_tab(tab)

    logger.removeHandler (warcLogger)
    warcLogger.flush ()
    fd.close ()

    return True

