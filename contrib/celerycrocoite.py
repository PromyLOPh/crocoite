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

import os, logging
from sopel.module import nickname_commands, require_chanmsg, thread, example, require_privilege, VOICE
from sopel.tools import Identifier, SopelMemory
import celery
from urllib.parse import urlsplit

import crocoite.cli
from crocoite import behavior

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
    m = bot.memory['crocoite'] = SopelMemory ()
    m['jobs'] = {}

def isValidUrl (s):
    url = urlsplit (s)
    return url.scheme and url.netloc and url.scheme in {'http', 'https'}

@nickname_commands ('ao', 'archiveonly')
@require_chanmsg ()
@require_privilege (VOICE)
@thread (True)
@example ('ao http://example.com')
def archive (bot, trigger):
    """
    Archive a single page (no recursion) to WARC
    """

    def updateState (job, data):
        job['state'] = data

    url = trigger.group(2)
    if not url:
        bot.reply ('Need a URL')
        return
    if not isValidUrl (url):
        bot.reply ('{} is not a valid URL'.format (url))
        return

    args = {
            'url': url,
            'output': None,
            'onload': ['scroll.js'] + behavior.getByUrl (url),
            'onsnapshot': [],
            'browser': None,
            'logBuffer': 1000,
            'maxBodySize': 10*1024*1024,
            'idleTimeout': 10,
            # 1 hour
            'timeout': 1*60*60,
            'domSnapshot': False,
            'screenshot': False,
            }

    handle = crocoite.cli.archive.delay (**args)
    m = bot.memory['crocoite']
    jobs = m['jobs']
    # XXX: for some reason we cannot access the job’s state through handle,
    # instead use a callback quirk
    j = jobs[handle.id] = {'handle': handle, 'trigger': trigger, 'state': {}}

    # pretty-print a few selected args
    showargs = {
            'onload': ','.join (args['onload']),
            'idleTimeout': prettyTimeDelta (args['idleTimeout']),
            'timeout': prettyTimeDelta (args['timeout']),
            }
    strargs = ', '.join (map (lambda x: '{}={}'.format (*x), showargs.items ()))
    bot.reply ('{} has been queued as {} with {}'.format (url, handle.id, strargs))

    try:
        result = handle.get (on_message=lambda x: updateState (j, x))
        stats = result['stats']
        bot.reply ('{} ({}) finished. {} requests, {} failed, {} received.'.format (url,
                handle.id, stats['requests'], stats['failed'],
                prettyBytes (stats['bytesRcv'])))
    except Exception as e:
        # json serialization does not work well with exceptions. If their class
        # names are unique we can still distinguish them.
        ename = type (e).__name__
        if ename == 'TaskRevokedError':
            bot.reply ('{} ({}) was revoked'.format (url, handle.id))
        else:
            bot.reply ('{} ({}) failed'.format (url, handle.id))
            logging.exception ('{} ({}) failed'.format (url, handle.id))
    finally:
        del jobs[handle.id]

@nickname_commands ('s', 'status')
@example ('s c251f09e-3c26-481f-96e0-4b5f58bd1170')
@require_chanmsg ()
def status (bot, trigger):
    """
    Retrieve status for a job
    """

    m = bot.memory['crocoite']
    jobs = m['jobs']

    i = trigger.group(2)
    if not i or i not in jobs:
        bot.reply("Job not found.")
        return
    
    j = jobs[i]
    jtrigger = j['trigger']
    jhandle = j['handle']
    jstate = j['state']
    jresult = jstate.get ('result', {})
    bot.reply ('{}: {}, queued {}, by {}'.format (jhandle.id,
            jstate.get ('status', 'UNKNOWN'), jtrigger.time, jtrigger.nick))

@nickname_commands ('r', 'revoke')
@example ('r c251f09e-3c26-481f-96e0-4b5f58bd1170')
@require_privilege (VOICE)
@require_chanmsg ()
def revoke (bot, trigger):
    """
    Cancel (revoke) a job
    """

    m = bot.memory['crocoite']
    jobs = m['jobs']

    i = trigger.group(2)
    if not i or i not in jobs:
        bot.reply ("Job not found.")
        return
    
    j = jobs[i]
    jhandle = j['handle']
    jhandle.revoke (terminate=True)
    # response is handled by long-running initiation thread

