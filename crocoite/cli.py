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

import argparse, sys, signal, asyncio, os, json
from traceback import TracebackException
from enum import IntEnum
from yarl import URL
try:
    import manhole
    manhole.install (patch_fork=False, oneshot_on='USR1')
except ModuleNotFoundError:
    pass

from . import behavior, browser
from .controller import SinglePageController, \
        ControllerSettings, StatsHandler, LogHandler, \
        RecursiveController, DepthLimit, PrefixLimit
from .devtools import Passthrough, Process
from .warc import WarcHandler
from .logger import Logger, JsonPrintConsumer, DatetimeConsumer, \
        WarcHandlerConsumer, Level
from .devtools import Crashed

class SingleExitStatus(IntEnum):
    """ Exit status for single-shot command line """
    Ok = 0
    Fail = 1
    BrowserCrash = 2
    Navigate = 3

def single ():
    """
    One-shot command line interface and pywb_ playback:

    .. code:: bash

        pip install pywb
        crocoite-grab http://example.com/ example.com.warc.gz
        rm -rf collections && wb-manager init test && wb-manager add test example.com.warc.gz
        wayback &
        $BROWSER http://localhost:8080

    .. _pywb: https://github.com/ikreymer/pywb
    """
    parser = argparse.ArgumentParser(description='Save website to WARC using Google Chrome.')
    parser.add_argument('--browser', help='DevTools URL', metavar='URL')
    parser.add_argument('--timeout', default=1*60*60, type=int, help='Maximum time for archival', metavar='SEC')
    parser.add_argument('--idle-timeout', default=30, type=int, help='Maximum idle seconds (i.e. no requests)', dest='idleTimeout', metavar='SEC')
    parser.add_argument('--behavior', help='Enable behavior script',
            dest='enabledBehaviorNames',
            default=list (behavior.availableMap.keys ()),
            choices=list (behavior.availableMap.keys ()),
            metavar='NAME', nargs='*')
    parser.add_argument('--warcinfo', help='Add extra information to warcinfo record',
            metavar='JSON', type=json.loads)
    parser.add_argument('-k', '--insecure',
            action='store_true',
            help='Disable certificate validation')
    parser.add_argument('url', help='Website URL', type=URL, metavar='URL')
    parser.add_argument('output', help='WARC filename', metavar='FILE')

    args = parser.parse_args ()

    logger = Logger (consumer=[DatetimeConsumer (), JsonPrintConsumer ()])

    ret = SingleExitStatus.Fail
    service = Process ()
    if args.browser:
        service = Passthrough (args.browser)
    settings = ControllerSettings (
            idleTimeout=args.idleTimeout,
            timeout=args.timeout,
            insecure=args.insecure,
            )
    with open (args.output, 'wb') as fd, WarcHandler (fd, logger) as warcHandler:
        logger.connect (WarcHandlerConsumer (warcHandler))
        handler = [StatsHandler (), LogHandler (logger), warcHandler]
        b = list (map (lambda x: behavior.availableMap[x], args.enabledBehaviorNames))
        controller = SinglePageController (url=args.url, settings=settings,
                service=service, handler=handler, behavior=b, logger=logger,
                warcinfo=args.warcinfo)
        try:
            loop = asyncio.get_event_loop()
            run = asyncio.ensure_future (controller.run ())
            stop = lambda signum: run.cancel ()
            loop.add_signal_handler (signal.SIGINT, stop, signal.SIGINT)
            loop.add_signal_handler (signal.SIGTERM, stop, signal.SIGTERM)
            loop.run_until_complete(run)
            loop.close()
            ret = SingleExitStatus.Ok
        except Crashed:
            ret = SingleExitStatus.BrowserCrash
        except asyncio.CancelledError:
            # don’t log this one
            pass
        except browser.NavigateError:
            ret = SingleExitStatus.Navigate
        except Exception as e:
            ret = SingleExitStatus.Fail
            logger.error ('cli exception',
                    uuid='7fd69858-ecaa-4225-b213-8ab880aa3cc5',
                    traceback=list (TracebackException.from_exception (e).format ()))
        finally:
            r = handler[0].stats
            logger.info ('stats', context='cli', uuid='24d92d16-770e-4088-b769-4020e127a7ff', **r)
            logger.info ('exit', context='cli', uuid='9b1bd603-f7cd-4745-895a-5b894a5166f2', status=ret)

    return ret

def parsePolicy (recursive, url):
    if recursive is None:
        return DepthLimit (0)
    elif recursive.isdigit ():
        return DepthLimit (int (recursive))
    elif recursive == 'prefix':
        return PrefixLimit (url)
    raise ValueError ('Unsupported')

def recursive ():
    """
    crocoite is built with the Unix philosophy (“do one thing and do it well”) in
    mind. Thus ``crocoite-grab`` can only save a single page. If you want recursion
    use ``crocoite-recursive``, which follows hyperlinks according to ``--policy``.
    It can either recurse a maximum number of levels or grab all pages with the
    same prefix as the start URL:

    .. code:: bash

       crocoite-recursive --policy prefix http://www.example.com/dir/ output

    will save all pages in ``/dir/`` and below to individual files in the output
    directory ``output``. You can customize the command used to grab individual
    pages by appending it after ``output``. This way distributed grabs (ssh to a
    different machine and execute the job there, queue the command with Slurm, …)
    are possible.
    """

    logger = Logger (consumer=[DatetimeConsumer (), JsonPrintConsumer ()])

    parser = argparse.ArgumentParser(description='Recursively run crocoite-grab.')
    parser.add_argument('--policy', help='Recursion policy', metavar='POLICY')
    parser.add_argument('--tempdir', help='Directory for temporary files', metavar='DIR')
    parser.add_argument('--prefix', help='Output filename prefix, supports templates {host} and {date}', metavar='FILENAME', default='{host}-{date}-')
    parser.add_argument('--concurrency', '-j', help='Run at most N jobs', metavar='N', default=1, type=int)
    parser.add_argument('url', help='Seed URL', type=URL, metavar='URL')
    parser.add_argument('output', help='Output directory', metavar='DIR')
    parser.add_argument('command', help='Fetch command, supports templates {url} and {dest}', metavar='CMD', nargs='*', default=['crocoite-grab', '{url}', '{dest}'])

    args = parser.parse_args ()
    try:
        policy = parsePolicy (args.policy, args.url)
    except ValueError:
        parser.error ('Invalid argument for --policy')

    os.makedirs (args.output, exist_ok=True)

    controller = RecursiveController (url=args.url, output=args.output,
            command=args.command, logger=logger, policy=policy,
            tempdir=args.tempdir, prefix=args.prefix,
            concurrency=args.concurrency)

    run = asyncio.ensure_future (controller.run ())
    loop = asyncio.get_event_loop()
    stop = lambda signum: run.cancel ()
    loop.add_signal_handler (signal.SIGINT, stop, signal.SIGINT)
    loop.add_signal_handler (signal.SIGTERM, stop, signal.SIGTERM)
    loop.run_until_complete(run)
    loop.close()

    return 0

def irc ():
    """
    A simple IRC bot (“chromebot”) is provided with the command ``crocoite-irc``.
    It reads its configuration from a config file like the example provided in
    ``contrib/chromebot.json`` and supports the following commands:

    a <url> -j <concurrency> -r <policy>
        Archive <url> with <concurrency> processes according to recursion <policy>
    s <uuid>
        Get job status for <uuid>
    r <uuid>
        Revoke or abort running job with <uuid>
    """

    import json, re
    from .irc import Chromebot

    logger = Logger (consumer=[DatetimeConsumer (), JsonPrintConsumer ()])

    parser = argparse.ArgumentParser(description='IRC bot.')
    parser.add_argument('--config', '-c', help='Config file location', metavar='PATH', default='chromebot.json')

    args = parser.parse_args ()

    with open (args.config) as fd:
        config = json.load (fd)
    s = config['irc']
    blacklist = dict (map (lambda x: (re.compile (x[0], re.I), x[1]), config['blacklist'].items ()))

    loop = asyncio.get_event_loop()
    bot = Chromebot (
            host=s['host'],
            port=s['port'],
            ssl=s['ssl'],
            nick=s['nick'],
            channels=s['channels'],
            tempdir=config['tempdir'],
            destdir=config['destdir'],
            processLimit=config['process_limit'],
            logger=logger,
            blacklist=blacklist,
            needVoice=config['need_voice'],
            loop=loop)
    stop = lambda signum: bot.cancel ()
    loop.add_signal_handler (signal.SIGINT, stop, signal.SIGINT)
    loop.add_signal_handler (signal.SIGTERM, stop, signal.SIGTERM)
    loop.run_until_complete(bot.run ())

def dashboard ():
    from .irc import Dashboard

    loop = asyncio.get_event_loop()
    d = Dashboard (sys.stdin, loop)
    loop.run_until_complete(d.run ())
    loop.run_forever()

