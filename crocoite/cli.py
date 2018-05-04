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
Command line interface
"""

import logging, argparse, json, sys

from . import behavior
from .controller import RecursiveController, defaultSettings, \
        ControllerSettings, DepthLimit, PrefixLimit
from .browser import NullService, ChromeService

def main ():
    parser = argparse.ArgumentParser(description='Save website to WARC using Google Chrome.')
    parser.add_argument('--browser', help='DevTools URL', metavar='URL')
    parser.add_argument('--recursive', help='Follow links recursively')
    parser.add_argument('--concurrency', '-j', type=int, default=1)
    parser.add_argument('--timeout', default=10, type=int, help='Maximum time for archival', metavar='SEC')
    parser.add_argument('--idle-timeout', default=2, type=int, help='Maximum idle seconds (i.e. no requests)', dest='idleTimeout', metavar='SEC')
    parser.add_argument('--log-buffer', default=defaultSettings.logBuffer, type=int, dest='logBuffer', metavar='LINES')
    parser.add_argument('--max-body-size', default=defaultSettings.maxBodySize, type=int, dest='maxBodySize', help='Max body size', metavar='BYTES')
    parser.add_argument('--behavior', help='Comma-separated list of enabled behavior scripts',
            dest='enabledBehaviorNames',
            default=list (behavior.availableNames),
            choices=list (behavior.availableNames))
    group = parser.add_mutually_exclusive_group (required=True)
    group.add_argument('--output', help='WARC filename', metavar='FILE')
    group.add_argument('--distributed', help='Use celery worker', action='store_true')
    parser.add_argument('url', help='Website URL')

    args = parser.parse_args ()

    if args.distributed:
        if args.browser:
            parser.error ('--browser is not supported for distributed jobs')
        from . import task
        settings = dict (maxBodySize=args.maxBodySize,
                logBuffer=args.logBuffer, idleTimeout=args.idleTimeout,
                timeout=args.timeout)
        result = task.controller.delay (url=args.url, settings=settings,
                enabledBehaviorNames=args.enabledBehaviorNames,
                recursive=args.recursive, concurrency=args.concurrency)
        r = result.get ()
    else:
        logging.basicConfig (level=logging.INFO)

        if args.recursive is None:
            recursionPolicy = DepthLimit (0)
        elif args.recursive.isdigit ():
            recursionPolicy = DepthLimit (int (args.recursive))
        elif args.recursive == 'prefix':
            recursionPolicy = PrefixLimit (args.url)
        else:
            parser.error ('Invalid argument for --recursive')
        service = ChromeService ()
        if args.browser:
            service = NullService (args.browser)
        settings = ControllerSettings (maxBodySize=args.maxBodySize,
                logBuffer=args.logBuffer, idleTimeout=args.idleTimeout,
                timeout=args.timeout)
        with open (args.output, 'wb') as fd:
            controller = RecursiveController (args.url, fd, settings=settings,
                    recursionPolicy=recursionPolicy, service=service)
            r = controller.run ()
    json.dump (r, sys.stdout)

    return True

