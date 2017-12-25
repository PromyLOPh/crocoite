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

import os, logging, argparse
from io import BytesIO
from datetime import datetime
import pychrome
from urllib.parse import urlsplit

from celery import Celery
from celery.utils.log import get_task_logger

from . import behavior, defaults
from .warc import WarcLoader, SerializingWARCWriter
from .browser import ChromeService, NullService
from .util import packageUrl, getFormattedViewportMetrics

app = Celery ('crocoite.distributed')
app.config_from_object('celeryconfig')
logger = get_task_logger('crocoite.distributed.archive')

# defaults can be changed below using argparse; track started state, because tasks are usually long-running
@app.task(bind=True, track_started=True)
def archive (self, url, output, browser, logBuffer, maxBodySize, idleTimeout,
        timeout, enabledBehaviorNames):
    """
    Archive a single URL

    Supports these config keys (celeryconfig):

    warc_filename = '{domain}-{date}-{id}.warc.gz'
    temp_dir = '/tmp/'
    finished_dir = '/tmp/finished'
    """

    ret = {'stats': None}

    self.update_state (state='PROGRESS', meta={'step': 'start'})

    service = ChromeService ()
    if browser:
        service = NullService (browser)

    allBehavior = list (filter (lambda x: x.name in enabledBehaviorNames, behavior.available))

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
        writer = SerializingWARCWriter (fd, gzip=True)

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

            # not all behavior scripts are allowed for every URL, filter them
            enabledBehavior = list (filter (lambda x: url in x,
                    map (lambda x: x (l), allBehavior)))

            self.update_state (state='PROGRESS', meta={'step': 'onload'})
            for b in enabledBehavior:
                logger.debug ('starting onload behavior {}'.format (b.name))
                b.onload ()
            l.start ()

            self.update_state (state='PROGRESS', meta={'step': 'fetch'})
            l.waitIdle (idleTimeout, timeout)

            self.update_state (state='PROGRESS', meta={'step': 'onstop'})
            for b in enabledBehavior:
                logger.debug ('starting onstop behavior {}'.format (b.name))
                b.onstop ()

            # if we stopped due to timeout, wait for remaining assets
            l.waitIdle (2, 60)
            l.stop ()

            self.update_state (state='PROGRESS', meta={'step': 'onfinish'})
            for b in enabledBehavior:
                logger.debug ('starting onfinish behavior {}'.format (b.name))
                b.onfinish ()

            ret['stats'] = l.stats
        writer.flush ()
    if not output:
        outPath = os.path.join (app.conf.finished_dir, outFile)
        os.rename (fd.name, outPath)
    return ret

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
    parser.add_argument('--log-buffer', default=defaults.logBuffer, type=int, dest='logBuffer', metavar='LINES')
    parser.add_argument('--max-body-size', default=defaults.maxBodySize, type=int, dest='maxBodySize', help='Max body size', metavar='BYTES')
    #parser.add_argument('--keep-tab', action='store_true', default=False, dest='keepTab', help='Keep tab open')
    parser.add_argument('--behavior', help='Comma-separated list of enabled behavior scripts',
            dest='enabledBehaviorNames',
            default=list (behavior.availableNames),
            choices=list (behavior.availableNames))
    parser.add_argument('url', help='Website URL')
    parser.add_argument('output', help='WARC filename')

    args = parser.parse_args ()

    # prepare args for function
    distributed = args.distributed
    passArgs = vars (args)
    del passArgs['distributed']

    if distributed:
        result = archive.delay (**passArgs)
        r = result.get (on_message=stateCallback)
    else:
        # XXX: local evaluation does not init celery logging?
        logging.basicConfig (level=logging.INFO)
        r = archive (**passArgs)
    print (r['stats'])

    return True

