# Copyright (c) 2017–2018 crocoite contributors
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
IRC bot “chromebot”
"""

import asyncio, argparse, uuid, json, tempfile
from datetime import datetime
from urllib.parse import urlsplit
from enum import IntEnum
import bottom

### helper functions ###
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

def isValidUrl (s):
    url = urlsplit (s)
    if url.scheme and url.netloc and url.scheme in {'http', 'https'}:
        return s
    raise TypeError ()

class NonExitingArgumentParser (argparse.ArgumentParser):
    """ Argument parser that does not call exit(), suitable for interactive use """
    def exit (self, status=0, message=None):
        # should never be called
        pass

    def error (self, message):
        raise Exception (message)

    def format_usage (self):
        return super().format_usage ().replace ('\n', ' ')

class Status(IntEnum):
    undefined = 0
    pending = 1
    running = 2
    aborted = 3
    finished = 4

class Job:
    __slots__ = ('id', 'stats', 'rstats', 'started', 'finished', 'nick', 'status', 'process', 'url')

    def __init__ (self, url, nick):
        self.id = str (uuid.uuid4 ())
        self.stats = {}
        self.rstats = {}
        self.started = datetime.utcnow ()
        self.finished = None
        self.url = url
        # user who scheduled this job
        self.nick = nick
        self.status = Status.pending
        self.process = None

    def formatStatus (self):
        stats = self.stats
        rstats = self.rstats
        return '{} ({}) {}. {} pages finished, {} pending; {} crashed, {} requests, {} failed, {} received.'.format (
                self.url,
                self.id,
                self.status.name,
                rstats.get ('have', 0),
                rstats.get ('pending', 0),
                stats.get ('crashed', 0),
                stats.get ('requests', 0),
                stats.get ('failed', 0),
                prettyBytes (stats.get ('bytesRcv', 0)))

class Bot(bottom.Client):
    __slots__ = ('jobs', 'channels', 'nick', 'tempdir', 'destdir', 'parser')

    def __init__ (self, host, port, ssl, nick, channels=[], tempdir=tempfile.gettempdir(), destdir='.'):
        super().__init__ (host=host, port=port, ssl=ssl)
        self.jobs = {}
        self.channels = channels
        self.nick = nick
        self.tempdir = tempdir
        self.destdir = destdir

        self.parser = NonExitingArgumentParser (prog=self.nick + ': ', add_help=False)
        subparsers = self.parser.add_subparsers(help='Sub-commands')

        archiveparser = subparsers.add_parser('a', help='Archive a site')
        #archiveparser.add_argument('--timeout', default=1*60*60, type=int, help='Maximum time for archival', metavar='SEC', choices=[60, 1*60*60, 2*60*60])
        #archiveparser.add_argument('--idle-timeout', default=10, type=int, help='Maximum idle seconds (i.e. no requests)', dest='idleTimeout', metavar='SEC', choices=[1, 10, 20, 30, 60])
        #archiveparser.add_argument('--max-body-size', default=None, type=int, dest='maxBodySize', help='Max body size', metavar='BYTES', choices=[1*1024*1024, 10*1024*1024, 100*1024*1024])
        archiveparser.add_argument('--concurrency', '-j', default=1, type=int, help='Parallel workers for this job', choices=range (9))
        archiveparser.add_argument('--recursive', '-r', help='Enable recursion', choices=['0', '1', 'prefix'], default='0')
        archiveparser.add_argument('url', help='Website URL', type=isValidUrl)
        archiveparser.set_defaults (func=self.handleArchive)

        statusparser = subparsers.add_parser ('s', help='Get job status')
        statusparser.add_argument('id', help='Job id', metavar='UUID')
        statusparser.set_defaults (func=self.handleStatus)

        abortparser = subparsers.add_parser ('r', help='Revoke/abort job')
        abortparser.add_argument('id', help='Job id', metavar='UUID')
        abortparser.set_defaults (func=self.handleAbort)

        # register bottom event handler
        self.on('CLIENT_CONNECT', self.onConnect)
        self.on('PING', self.onKeepalive)
        self.on('PRIVMSG', self.onMessage)
        self.on('CLIENT_DISCONNECT', self.onDisconnect)

    async def handleArchive (self, args, nick, target, message, **kwargs):
        """ Handle the archive command """

        j = Job (args.url, nick)
        assert j.id not in self.jobs, 'duplicate job id'
        self.jobs[j.id] = j

        cmdline = ['crocoite-recursive', args.url, '--tempdir', self.tempdir,
                '--prefix', j.id + '-{host}-{date}-', '--policy',
                args.recursive, '--concurrency', str (args.concurrency),
                self.destdir]

        showargs = {
                'recursive': args.recursive,
                'concurrency': args.concurrency,
                }
        strargs = ', '.join (map (lambda x: '{}={}'.format (*x), showargs.items ()))
        self.send ('PRIVMSG', target=target, message='{}: {} has been queued as {} with {}'.format (
                nick, args.url, j.id, strargs))

        j.process = await asyncio.create_subprocess_exec (*cmdline, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, stdin=asyncio.subprocess.DEVNULL)
        while True:
            data = await j.process.stdout.readline ()
            if not data:
                break

            # job is marked running after the first message is received from it
            if j.status == Status.pending:
                j.status = Status.running

            data = json.loads (data)
            msgid = data.get ('uuid')
            if msgid == '24d92d16-770e-4088-b769-4020e127a7ff':
                j.stats = data
            elif msgid == '5b8498e4-868d-413c-a67e-004516b8452c':
                j.rstats = data
        code = await j.process.wait ()

        if j.status == Status.running:
            j.status = Status.finished
        j.finished = datetime.utcnow ()

        stats = j.stats
        rstats = j.rstats
        self.send ('PRIVMSG', target=target, message='{}: {}'.format (nick, j.formatStatus ()))

    async def handleStatus (self, args, nick, target, message, **kwargs):
        """ Handle status command """

        j = self.jobs.get (args.id, None)
        if not j:
            self.send ('PRIVMSG', target=target, message='{}: Job {} is unknown'.format (nick, args.id))
        else:
            rstats = j.rstats
            self.send ('PRIVMSG', target=target, message='{}: {}'.format (nick, j.formatStatus ()))

    async def handleAbort (self, args, nick, target, message, **kwargs):
        """ Handle abort command """

        j = self.jobs.get (args.id, None)
        if not j:
            self.send ('PRIVMSG', target=target, message='{}: Job {} is unknown'.format (nick, args.id))
        else:
            j.status = Status.aborted
            j.process.terminate ()

    async def onConnect (self, **kwargs):

        self.send('NICK', nick=self.nick)
        self.send('USER', user=self.nick, realname='https://github.com/PromyLOPh/crocoite')

        # Don't try to join channels until the server has
        # sent the MOTD, or signaled that there's no MOTD.
        done, pending = await asyncio.wait(
            [self.wait('RPL_ENDOFMOTD'), self.wait('ERR_NOMOTD')],
            loop=self.loop, return_when=asyncio.FIRST_COMPLETED)

        # Cancel whichever waiter's event didn't come in.
        for future in pending:
            future.cancel()

        for c in self.channels:
            self.send('JOIN', channel=c)

    async def onKeepalive (self, message, **kwargs):
        """ Ping received """
        self.send('PONG', message=message)

    async def onMessage (self, nick, target, message, **kwargs):
        """ Message received """
        if target in self.channels and message.startswith (self.nick):
            # channel message that starts with our nick
            command = message.split (' ')[1:]
            try:
                args = self.parser.parse_args (command)
            except Exception as e:
                self.send ('PRIVMSG', target=target, message='{} -- {}'.format (e.args[0], self.parser.format_usage ()))
                return
            if not args:
                self.send ('PRIVMSG', target=target, message='Sorry, I don’t understand {}'.format (command))
                return

            await args.func (args, nick, target, message, **kwargs)

    async def onDisconnect (**kwargs):
        """ Auto-reconnect """
        await asynio.sleep (10, loop=self.loop)
        await self.connect ()

