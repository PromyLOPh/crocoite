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
Module for Sopel IRC bot
"""

import os, logging, argparse
from sopel.module import nickname_commands, require_chanmsg, thread, example, require_privilege, VOICE
from sopel.tools import Identifier, SopelMemory
import celery, celery.exceptions
from celery.result import AsyncResult
from urllib.parse import urlsplit
from threading import Thread
from queue import Queue
import queue

from crocoite import behavior, task
from crocoite.controller import defaultSettings

def prettyTimeDelta (seconds):
    """
    Pretty-print seconds to human readable string 1d 1h 1m 1s
    """
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    s = [(days, 'd'), (hours, 'h'), (minutes, 'm'), (seconds, 's')]
    s = filter (lambda x: x[0] != 0, s)
    return ' '.join (map (lambda x: '{}{}'.format (*x), s))

def prettyBytes (b):
    """
    Pretty-print bytes
    """
    prefixes = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
    while b >= 1024 and len (prefixes) > 1:
        b /= 1024
        prefixes.pop (0)
    return '{:.1f} {}'.format (b, prefixes[0])

def setup (bot):
    m = bot.memory['crocoite'] = {}
    q = m['q'] = Queue ()
    t = m['t'] = Thread (target=celeryWorker, args=(bot, q))
    t.start ()

def shutdown (bot):
    m = bot.memory['crocoite']
    q = m['q']
    t = m['t']
    q.put_nowait (None)
    t.join ()

def isValidUrl (s):
    url = urlsplit (s)
    if url.scheme and url.netloc and url.scheme in {'http', 'https'}:
        return s
    raise TypeError ()

def checkCompletedJobs (bot, jobs):
    delete = set ()
    for i, data in jobs.items ():
        handle = data['handle']
        trigger = data['trigger']
        args = data['args']
        url = args['url']
        channel = trigger.sender
        user = trigger.nick
        if Identifier (channel) not in bot.channels:
            continue
        try:
            result = handle.get (timeout=0.1)
            stats = result['stats']
            bot.msg (channel, '{}: {} ({}) finished. {} requests, {} failed, {} received.'.format (user, url,
                    handle.id, stats['requests'], stats['failed'],
                    prettyBytes (stats['bytesRcv'])))
            delete.add (handle.id)
        except celery.exceptions.TimeoutError:
            pass
        except Exception as e:
            # json serialization does not work well with exceptions. If their class
            # names are unique we can still distinguish them.
            ename = type (e).__name__
            if ename == 'TaskRevokedError':
                bot.msg (channel, '{}: {} ({}) was revoked'.format (user, url, handle.id))
            else:
                bot.msg (channel, '{} ({}) failed'.format (user, url, handle.id))
                logging.exception ('{} ({}) failed'.format (url, handle.id))
            delete.add (handle.id)
    for d in delete:
        del jobs[d]

def celeryWorker (bot, q):
    """
    Serialize celery operations in a single thread. This is a workaround for
    https://github.com/celery/celery/issues/4480
    """

    jobs = {}

    while True:
        try:
            item = q.get (timeout=1)
        except queue.Empty:
            checkCompletedJobs (bot, jobs)
            continue

        if item is None:
            break
        action, trigger, args = item
        if action == 'a':
            handle = task.controller.delay (**args)
            j = jobs[handle.id] = {'handle': handle, 'trigger': trigger, 'args': args}

            # pretty-print a few selected args
            showargs = {
                    'idleTimeout': prettyTimeDelta (args['settings']['idleTimeout']),
                    'timeout': prettyTimeDelta (args['settings']['timeout']),
                    'maxBodySize': prettyBytes (args['settings']['maxBodySize']),
                    'recursive': args['recursive'],
                    'concurrency': args['concurrency'],
                    }
            strargs = ', '.join (map (lambda x: '{}={}'.format (*x), showargs.items ()))
            bot.msg (trigger.sender, '{}: {} has been queued as {} with {}'.format (trigger.nick, args['url'], handle.id, strargs))
        elif action == 'status':
            if args and args in jobs:
                j = jobs[args]
                jtrigger = j['trigger']
                handle = j['handle']
                bot.msg (trigger.sender, '{}: {}, queued {}, by {}'.format (handle.id,
                        handle.status, jtrigger.time, jtrigger.nick))
            else:
                bot.msg (trigger.sender, "Job not found.")
        elif action == 'revoke':
            if args and args in jobs:
                j = jobs[args]
                handle = j['handle']
                handle.revoke (terminate=True)
                # response is handled above
            else:
                bot.msg (trigger.sender, "Job not found.")
        q.task_done ()

class NonExitingArgumentParser (argparse.ArgumentParser):
    def exit (self, status=0, message=None):
        # should never be called
        pass

    def error (self, message):
        raise Exception (message)

archiveparser = NonExitingArgumentParser (prog='a', add_help=False)
archiveparser.add_argument('--timeout', default=1*60*60, type=int, help='Maximum time for archival', metavar='SEC', choices=[60, 1*60*60, 2*60*60])
archiveparser.add_argument('--idle-timeout', default=10, type=int, help='Maximum idle seconds (i.e. no requests)', dest='idleTimeout', metavar='SEC', choices=[1, 10, 20, 30, 60])
archiveparser.add_argument('--max-body-size', default=defaultSettings.maxBodySize, type=int, dest='maxBodySize', help='Max body size', metavar='BYTES', choices=[1*1024*1024, 10*1024*1024, defaultSettings.maxBodySize, 100*1024*1024])
archiveparser.add_argument('--concurrency', default=1, type=int, help='Parallel workers for this job', choices=range (9))
archiveparser.add_argument('--recursive', help='Enable recursion', choices=['0', '1', '2', '3', 'prefix'])
archiveparser.add_argument('url', help='Website URL', type=isValidUrl)

@nickname_commands ('a', 'archive')
@require_chanmsg ()
@require_privilege (VOICE)
@example ('a http://example.com')
def archive (bot, trigger):
    """
    Archive a URL to WARC
    """

    try:
        args = archiveparser.parse_args (trigger.group (2).split ())
    except Exception as e:
        bot.reply ('{} -- {}'.format (e.args[0], archiveparser.format_usage ()))
        return
    if not args:
        bot.reply ('Sorry, I don’t understand {}'.format (trigger.group (2)))
        return
    blacklistedBehavior = {'domSnapshot', 'screenshot'}
    settings = dict (maxBodySize=args.maxBodySize,
            logBuffer=defaultSettings.logBuffer, idleTimeout=args.idleTimeout,
            timeout=args.timeout)
    args = dict (url=args.url,
            enabledBehaviorNames=list (behavior.availableNames-blacklistedBehavior),
            settings=settings, recursive=args.recursive,
            concurrency=args.concurrency)
    q = bot.memory['crocoite']['q']
    q.put_nowait (('a', trigger, args))

@nickname_commands ('s', 'status')
@example ('s c251f09e-3c26-481f-96e0-4b5f58bd1170')
@require_chanmsg ()
def status (bot, trigger):
    """
    Retrieve status for a job
    """

    i = trigger.group(2)
    q = bot.memory['crocoite']['q']
    q.put_nowait (('status', trigger, i))

@nickname_commands ('r', 'revoke')
@example ('r c251f09e-3c26-481f-96e0-4b5f58bd1170')
@require_privilege (VOICE)
@require_chanmsg ()
def revoke (bot, trigger):
    """
    Cancel (revoke) a job
    """

    i = trigger.group(2)
    q = bot.memory['crocoite']['q']
    q.put_nowait (('revoke', trigger, i))

