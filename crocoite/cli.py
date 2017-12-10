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
Standalone and Celery command line interface
"""

import os, random, logging, argparse
from io import BytesIO
from datetime import datetime
from base64 import b64decode
import pychrome
from urllib.parse import urlsplit
from warcio.warcwriter import WARCWriter
from warcio.statusandheaders import StatusAndHeaders
from html5lib.serializer import HTMLSerializer

from celery import Celery
from celery.utils.log import get_task_logger

from . import html, packageData, packageUrl
from .warc import WarcLoader
from .html import StripAttributeFilter, StripTagFilter, ChromeTreeWalker
from .browser import ChromeService, NullService

def getFormattedViewportMetrics (tab):
    layoutMetrics = tab.Page.getLayoutMetrics ()
    # XXX: I’m not entirely sure which one we should use here
    return '{}x{}'.format (layoutMetrics['layoutViewport']['clientWidth'],
                layoutMetrics['layoutViewport']['clientHeight'])

def writeScript (path, source, writer):
    record = writer.create_warc_record (packageUrl (path), 'metadata',
            payload=BytesIO (source.encode ('utf8')),
            warc_headers_dict={'Content-Type': 'application/javascript; charset=utf-8'})
    writer.write_record (record)

def randomString (length=None, chars='abcdefghijklmnopqrstuvwxyz'):
    if length is None:
        length = random.randint (16, 32)
    return ''.join (map (lambda x: random.choice (chars), range (length)))

def writeDOMSnapshot (tab, writer):
    """
    Get a DOM snapshot of tab and write it to WARC.

    We could use DOMSnapshot.getSnapshot here, but the API is not stable
    yet. Also computed styles are not really necessary here.

    XXX: Currently writes a response, when it should use “resource”. pywb
    can’t handle that though.
    """
    viewport = getFormattedViewportMetrics (tab)
    dom = tab.DOM.getDocument (depth=-1, pierce=True)
    haveUrls = set ()
    for doc in ChromeTreeWalker (dom['root']).split ():
        rawUrl = doc['documentURL']
        if rawUrl in haveUrls:
            # ignore duplicate URLs. they are usually caused by
            # javascript-injected iframes (advertising) with no(?) src
            logger.warning ('have DOM snapshot for URL {}, ignoring'.format (rawUrl))
            continue
        url = urlsplit (rawUrl)
        if url.scheme in ('http', 'https'):
            logger.debug ('saving DOM snapshot for url {}, base {}'.format (doc['documentURL'], doc['baseURL']))
            haveUrls.add (rawUrl)
            walker = ChromeTreeWalker (doc)
            # remove script, to make the page static and noscript, because at the
            # time we took the snapshot scripts were enabled
            disallowedTags = ['script', 'noscript']
            disallowedAttributes = html.eventAttributes
            stream = StripAttributeFilter (StripTagFilter (walker, disallowedTags), disallowedAttributes)
            serializer = HTMLSerializer ()
            httpHeaders = StatusAndHeaders('200 OK', {}, protocol='HTTP/1.1')
            record = writer.create_warc_record (doc['documentURL'], 'response',
                    payload=BytesIO (serializer.render (stream, 'utf-8')),
                    http_headers=httpHeaders,
                    warc_headers_dict={'X-DOM-Snapshot': str (True),
                            'X-Chrome-Viewport': viewport})
            writer.write_record (record)

def emulateScreenMetrics (l):
    """
    Emulate different screen sizes, causing the site to fetch assets (img
    srcset and css, for example) for different screen resolutions.
    """
    cssPpi = 96
    sizes = [
            {'width': 1920, 'height': 1080, 'deviceScaleFactor': 1.5, 'mobile': False},
            {'width': 1920, 'height': 1080, 'deviceScaleFactor': 2, 'mobile': False},
            # very dense display
            {'width': 1920, 'height': 1080, 'deviceScaleFactor': 4, 'mobile': False},
            # just a few samples:
            # 1st gen iPhone (portrait mode)
            {'width': 320, 'height': 480, 'deviceScaleFactor': 163/cssPpi, 'mobile': True},
            # 6th gen iPhone (portrait mode)
            {'width': 750, 'height': 1334, 'deviceScaleFactor': 326/cssPpi, 'mobile': True},
            # and reset
            {'width': 1920, 'height': 1080, 'deviceScaleFactor': 1, 'mobile': False},
            ]
    for s in sizes:
        l.tab.Emulation.setDeviceMetricsOverride (**s)
        l.wait (1)
    # XXX: this seems to be broken, it does not clear the override
    #tab.Emulation.clearDeviceMetricsOverride ()
    # wait until assets finished loading
    l.waitIdle (2, 10)

def loadScripts (paths, scripts=[]):
    for p in paths:
        if not os.path.exists (p):
            # search for defaults scripts in package data directory
            p = packageData (p)
        with open (p, 'r') as fd:
            scripts.append (fd.read ())
    return '\n'.join (scripts)

def writeScreenshot (tab, writer):
    """
    Create screenshot from tab and write it to WARC
    """
    viewport = getFormattedViewportMetrics (tab)
    data = b64decode (tab.Page.captureScreenshot (format='png')['data'])
    record = writer.create_warc_record (packageUrl ('screenshot.png'), 'resource',
            payload=BytesIO (data), warc_headers_dict={'Content-Type': 'image/png',
            'X-Chrome-Viewport': viewport})
    writer.write_record (record)

# XXX: rabbitmq is hardcoded
app = Celery ('crocoite.distributed')
app.config_from_object('celeryconfig')
logger = get_task_logger('crocoite.distributed.archive')

# defaults can be changed below using argparse; track started state, because tasks are usually long-running
@app.task(bind=True, track_started=True)
def archive (self, url, output, onload, onsnapshot, browser,
        logBuffer, maxBodySize, idleTimeout, timeout, domSnapshot, screenshot):
    """
    Archive a single URL

    Supports these config keys (celeryconfig):

    warc_filename = '{domain}-{date}-{id}.warc.gz'
    temp_dir = '/tmp/'
    finished_dir = '/tmp/finished'
    """

    self.update_state (state='PROGRESS', meta={'step': 'start'})

    stopVarname = '__' + __package__ + '_stop__'
    # avoid sites messing with our scripts by using a random stop variable name
    newStopVarname = randomString ()
    onload = loadScripts (onload, ['var {} = false;\n'.format (stopVarname)]).replace (stopVarname, newStopVarname)
    stopVarname = newStopVarname

    service = ChromeService ()
    if browser:
        service = NullService (browser)

    with service as browser:
        browser = pychrome.Browser(url=browser)

        if not output:
            parsedUrl = urlsplit (url)
            outFile = app.conf.warc_filename.format (
                            id=self.request.id,
                            domain=parsedUrl.hostname.replace ('/', '-'),
                            date=datetime.utcnow ().isoformat (),
                            )
            outPath = os.path.join (app.conf.temp_dir, outFile)
            fd = open (outPath, 'wb')
        else:
            fd = open (output, 'wb')
        writer = WARCWriter (fd, gzip=True)

        with WarcLoader (browser, url, writer, logBuffer=logBuffer,
                maxBodySize=maxBodySize) as l:
            version = l.tab.Browser.getVersion ()
            payload = {
                    'software': __package__,
                    'browser': version['product'],
                    'useragent': version['userAgent'],
                    'viewport': getFormattedViewportMetrics (l.tab),
                    }
            warcinfo = writer.create_warcinfo_record (filename=None, info=payload)
            writer.write_record (warcinfo)
            # save onload script as well
            writeScript ('onload', onload, writer)

            # inject our custom javascript to the page before loading
            l.tab.Page.addScriptToEvaluateOnNewDocument (source=onload)
            l.start ()

            self.update_state (state='PROGRESS', meta={'step': 'fetch'})
            l.waitIdle (idleTimeout, timeout)

            # get ready for snapshot: stop loading and scripts, disable events
            l.tab.Runtime.evaluate (expression='{} = true; window.scrollTo (0, 0);'.format (stopVarname), returnByValue=True)
            # if we stopped due to timeout, wait for remaining assets
            l.waitIdle (2, 10)

            self.update_state (state='PROGRESS', meta={'step': 'emulateScreenMetrics'})
            emulateScreenMetrics (l)

            l.stop ()

            if domSnapshot:
                self.update_state (state='PROGRESS', meta={'step': 'domSnapshot'})
                script = loadScripts (onsnapshot)
                writeScript ('onsnapshot', script, writer)
                l.tab.Runtime.evaluate (expression=script, returnByValue=True)
                writeDOMSnapshot (l.tab, writer)

            if screenshot:
                self.update_state (state='PROGRESS', meta={'step': 'screenshot'})
                writeScreenshot (l.tab, writer)
    if not output:
        outPath = os.path.join (app.conf.finished_dir, outFile)
        os.rename (fd.name, outPath)
    return True

def stateCallback (data):
    result = data['result']
    if data['status'] == 'PROGRESS':
        print (data['task_id'], result['step'])

def main ():
    parser = argparse.ArgumentParser(description='Save website to WARC using Google Chrome.')
    parser.add_argument('--browser', help='DevTools URL', metavar='URL')
    parser.add_argument('--distributed', help='Use celery worker', action='store_true')
    parser.add_argument('--timeout', default=10, type=int, help='Maximum time for archival', metavar='SEC')
    parser.add_argument('--idle-timeout', default=2, type=int, help='Maximum idle seconds (i.e. no requests)', dest='idleTimeout', metavar='SEC')
    parser.add_argument('--log-buffer', default=1000, type=int, dest='logBuffer', metavar='LINES')
    parser.add_argument('--max-body-size', default=10*1024*1024, type=int, dest='maxBodySize', help='Max body size', metavar='BYTES')
    #parser.add_argument('--keep-tab', action='store_true', default=False, dest='keepTab', help='Keep tab open')
    parser.add_argument('--onload', default=[], action='append', help='Inject JavaScript file before loading page', metavar='FILE')
    parser.add_argument('--onsnapshot', default=[], action='append', help='Run JavaScript files before creating DOM snapshot', metavar='FILE')
    parser.add_argument('--no-screenshot', default=True, action='store_false', help='Do not create a screenshot of the website', dest='screenshot')
    parser.add_argument('--no-dom-snapshot', default=True, action='store_false', help='Do not create a DOM snapshot of the website', dest='domSnapshot')
    parser.add_argument('url', help='Website URL')
    parser.add_argument('output', help='WARC filename')

    args = parser.parse_args ()
    distributed = args.distributed
    passArgs = vars (args)
    del passArgs['distributed']

    if distributed:
        result = archive.delay (**passArgs)
        result.get (on_message=stateCallback)
    else:
        archive (**passArgs)

    return True

