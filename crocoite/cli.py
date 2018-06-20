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
        ControllerSettings, DepthLimit, PrefixLimit, StatsHandler
from .browser import NullService, ChromeService
from .warc import WarcHandler

def parseRecursive (recursive, url):
    if recursive is None:
        return DepthLimit (0)
    elif recursive.isdigit ():
        return DepthLimit (int (recursive))
    elif recursive == 'prefix':
        return PrefixLimit (url)
    else:
        raise ValueError ('Unsupported')

def main ():
    parser = argparse.ArgumentParser(description='Save website to WARC using Google Chrome.')
    parser.add_argument('--debug', help='Enable debug messages', action='store_true')
    parser.add_argument('--browser', help='DevTools URL', metavar='URL')
    parser.add_argument('--recursive', help='Follow links recursively')
    parser.add_argument('--concurrency', '-j', type=int, default=1)
    parser.add_argument('--timeout', default=10, type=int, help='Maximum time for archival', metavar='SEC')
    parser.add_argument('--idle-timeout', default=2, type=int, help='Maximum idle seconds (i.e. no requests)', dest='idleTimeout', metavar='SEC')
    parser.add_argument('--log-buffer', default=defaultSettings.logBuffer, type=int, dest='logBuffer', metavar='LINES')
    parser.add_argument('--max-body-size', default=defaultSettings.maxBodySize, type=int, dest='maxBodySize', help='Max body size', metavar='BYTES')
    parser.add_argument('--behavior', help='Comma-separated list of enabled behavior scripts',
            dest='enabledBehaviorNames',
            default=list (behavior.availableMap.keys ()),
            choices=list (behavior.availableMap.keys ()))
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
        level = logging.DEBUG if args.debug else logging.INFO
        logging.basicConfig (level=level)

        try:
            recursionPolicy = parseRecursive (args.recursive, args.url)
        except ValueError:
            parser.error ('Invalid argument for --recursive')
        service = ChromeService ()
        if args.browser:
            service = NullService (args.browser)
        settings = ControllerSettings (maxBodySize=args.maxBodySize,
                logBuffer=args.logBuffer, idleTimeout=args.idleTimeout,
                timeout=args.timeout)
        with open (args.output, 'wb') as fd:
            handler = [StatsHandler (), WarcHandler (fd)]
            b = list (map (lambda x: behavior.availableMap[x], args.enabledBehaviorNames))
            controller = RecursiveController (args.url, fd, settings=settings,
                    recursionPolicy=recursionPolicy, service=service,
                    handler=handler, behavior=b)
            controller.run ()
            r = handler[0].stats
    json.dump (r, sys.stdout)

    return True

