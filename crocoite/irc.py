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

import asyncio, argparse, json, tempfile, time, random, os
from datetime import datetime
from urllib.parse import urlsplit
from enum import IntEnum, unique
from collections import defaultdict
from abc import abstractmethod
from functools import wraps
import bottom
import websockets

from .util import StrJsonEncoder

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
    return f'{b:.1f} {prefixes[0]}'

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
        # if we use subparsers it’s important to return self, so we can show
        # the correct help
        raise Exception (self, message)

    def format_usage (self):
        return super().format_usage ().replace ('\n', ' ')

class Status(IntEnum):
    """ Job status """
    undefined = 0
    pending = 1
    running = 2
    aborted = 3
    finished = 4

# see https://arxiv.org/html/0901.4016 on how to build proquints (human
# pronouncable unique ids)
toConsonant = 'bdfghjklmnprstvz'
toVowel = 'aiou'

def u16ToQuint (v):
    """ Transform a 16 bit unsigned integer into a single quint """
    assert 0 <= v < 2**16
    # quints are “big-endian”
    return ''.join ([
            toConsonant[(v>>(4+2+4+2))&0xf],
            toVowel[(v>>(4+2+4))&0x3],
            toConsonant[(v>>(4+2))&0xf],
            toVowel[(v>>4)&0x3],
            toConsonant[(v>>0)&0xf],
            ])

def uintToQuint (v, length=2):
    """ Turn any integer into a proquint with fixed length """
    assert 0 <= v < 2**(length*16)

    return '-'.join (reversed ([u16ToQuint ((v>>(x*16))&0xffff) for x in range (length)]))

def makeJobId ():
    """ Create job id from time and randomness source """
    # allocate 48 bits for the time (in milliseconds) and add 16 random bits
    # at the end (just to be sure) for a total of 64 bits. Should be enough to
    # avoid collisions.
    randbits = 16
    stamp = (int (time.time ()*1000) << randbits) | random.randint (0, 2**randbits-1)
    return uintToQuint (stamp, 4)

class Job:
    """ Archival job """

    __slots__ = ('id', 'stats', 'rstats', 'started', 'finished', 'nick', 'status', 'process', 'url')

    def __init__ (self, url, nick):
        self.id = makeJobId ()
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
        return (f"{self.url} ({self.id}) {self.status.name}. "
                f"{rstats.get ('have', 0)} pages finished, "
                f"{rstats.get ('pending', 0)} pending; "
                f"{stats.get ('crashed', 0)} crashed, "
                f"{stats.get ('requests', 0)} requests, "
                f"{stats.get ('failed', 0)} failed, "
                f"{prettyBytes (stats.get ('bytesRcv', 0))} received.")

@unique
class NickMode(IntEnum):
    # the actual numbers don’t matter, but their order must be strictly
    # increasing (with priviledge level)
    operator = 100
    voice = 10

    @classmethod
    def fromMode (cls, mode):
        return {'v': cls.voice, 'o': cls.operator}[mode]

    @classmethod
    def fromNickPrefix (cls, mode):
        return {'@': cls.operator, '+': cls.voice}[mode]

    @property
    def human (self):
        return {self.operator: 'operator', self.voice: 'voice'}[self]

class User:
    """ IRC user """
    __slots__ = ('name', 'modes')

    def __init__ (self, name, modes=None):
        self.name = name
        self.modes = modes or set ()

    def __eq__ (self, b):
        return self.name == b.name

    def __hash__ (self):
        return hash (self.name)

    def __repr__ (self):
        return f'<User {self.name} {self.modes}>'

    def hasPriv (self, p):
        if p is None:
            return True
        else:
            return self.modes and max (self.modes) >= p

    @classmethod
    def fromName (cls, name):
        """ Get mode and name from NAMES command """
        try:
            modes = {NickMode.fromNickPrefix (name[0])}
            name = name[1:]
        except KeyError:
            modes = set ()
        return cls (name, modes)

class ReplyContext:
    __slots__ = ('client', 'target', 'user')

    def __init__ (self, client, target, user):
        self.client = client
        self.target = target
        self.user = user

    def __call__ (self, message):
        self.client.send ('PRIVMSG', target=self.target,
                message=f'{self.user.name}: {message}')

class RefCountEvent:
    """
    Ref-counted event that triggers if a) armed and b) refcount drops to zero.
    
    Must be used as a context manager.
    """
    __slots__ = ('count', 'event', 'armed')

    def __init__ (self):
        self.armed = False
        self.count = 0
        self.event = asyncio.Event ()

    def __enter__ (self):
        self.count += 1
        self.event.clear ()

    def __exit__ (self, exc_type, exc_val, exc_tb):
        self.count -= 1
        if self.armed and self.count == 0:
            self.event.set ()

    async def wait (self):
        await self.event.wait ()

    def arm (self):
        self.armed = True
        if self.count == 0:
            self.event.set ()

class ArgparseBot (bottom.Client):
    """
    Simple IRC bot using argparse
    
    Tracks user’s modes, reconnects on disconnect
    """

    __slots__ = ('channels', 'nick', 'parser', 'users', '_quit')

    def __init__ (self, host, port, ssl, nick, logger, channels=None, loop=None):
        super().__init__ (host=host, port=port, ssl=ssl, loop=loop)
        self.channels = channels or []
        self.nick = nick
        # map channel -> nick -> user
        self.users = defaultdict (dict)
        self.logger = logger.bind (context=type (self).__name__)
        self.parser = self.getParser ()

        # bot does not accept new queries in shutdown mode, unless explicitly
        # permitted by the parser
        self._quit = RefCountEvent ()

        # register bottom event handler
        self.on('CLIENT_CONNECT', self.onConnect)
        self.on('PING', self.onKeepalive)
        self.on('PRIVMSG', self.onMessage)
        self.on('CLIENT_DISCONNECT', self.onDisconnect)
        self.on('RPL_NAMREPLY', self.onNameReply)
        self.on('CHANNELMODE', self.onMode)
        self.on('PART', self.onPart)
        self.on('JOIN', self.onJoin)
        # XXX: we would like to handle KICK, but bottom does not support that at the moment

    @abstractmethod
    def getParser (self):
        pass

    def cancel (self):
        self.logger.info ('cancel', uuid='1eb34aea-a854-4fec-90b2-7f8a3812a9cd')
        self._quit.arm ()
    
    async def run (self):
        await self.connect ()
        await self._quit.wait ()
        self.send ('QUIT', message='Bye.')
        await self.disconnect ()

    async def onConnect (self, **kwargs):
        self.logger.info ('connect', nick=self.nick, uuid='01f7b138-ea53-4609-88e9-61f3eca3e7e7')

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
            self.logger.info ('join', channel=c, uuid='367063a5-9069-4025-907c-65ba88af8593')
            self.send ('JOIN', channel=c)
            # no need for NAMES here, server sends this automatically

    async def onNameReply (self, channel, users, **kwargs):
        # channels may be too big for a single message
        addusers = dict (map (lambda x: (x.name, x), map (User.fromName, users)))
        if channel not in self.users:
            self.users[channel] = addusers
        else:
            self.users[channel].update (addusers)

    @staticmethod
    def parseMode (mode):
        """ Parse mode strings like +a, -b, +a-b, -b+a, … """
        action = '+'
        ret = []
        for c in mode:
            if c in {'+', '-'}:
                action = c
            else:
                ret.append ((action, c))
        return ret

    async def onMode (self, channel, modes, params, **kwargs):
        if channel not in self.channels:
            return

        for (action, mode), nick in zip (self.parseMode (modes), params):
            try:
                m = NickMode.fromMode (mode)
                u = self.users[channel].get (nick, User (nick))
                if action == '+':
                    u.modes.add (m)
                elif action == '-':
                    u.modes.remove (m)
            except KeyError:
                # unknown mode, ignore
                pass

    async def onPart (self, nick, channel, **kwargs):
        if channel not in self.channels:
            return

        try:
            self.users[channel].pop (nick)
        except KeyError:
            # gone already
            pass

    async def onJoin (self, nick, channel, **kwargs):
        if channel not in self.channels:
            return

        self.users[channel][nick] = User (nick)

    async def onKeepalive (self, message, **kwargs):
        """ Ping received """
        self.send('PONG', message=message)

    async def onMessage (self, nick, target, message, **kwargs):
        """ Message received """
        if target in self.channels and message.startswith (self.nick + ':'):
            user = self.users[target].get (nick, User (nick))
            reply = ReplyContext (client=self, target=target, user=user)

            # channel message that starts with our nick
            command = message.split (' ')[1:]
            try:
                args = self.parser.parse_args (command)
            except Exception as e:
                reply (f'{e.args[1]} -- {e.args[0].format_usage ()}')
                return
            if not args or not hasattr (args, 'func'):
                reply (f'Sorry, I don’t understand {command}')
                return

            minPriv = getattr (args, 'minPriv', None)
            if self._quit.armed and not getattr (args, 'allowOnShutdown', False):
                reply ('Sorry, I’m shutting down and cannot accept your request right now.')
            elif not user.hasPriv (minPriv):
                reply (f'Sorry, you need the privilege {minPriv.human} to use this command.')
            else:
                with self._quit:
                    await args.func (user=user, args=args, reply=reply)

    async def onDisconnect (self, **kwargs):
        """ Auto-reconnect """
        self.logger.info ('disconnect', uuid='4c74b2c8-2403-4921-879d-2279ad85db72')
        while True:
            if not self._quit.armed:
                await asyncio.sleep (10, loop=self.loop)
                self.logger.info ('reconnect', uuid='c53555cb-e1a4-4b69-b1c9-3320269c19d7')
                try:
                    await self.connect ()
                finally:
                    break

def jobExists (func):
    """ Chromebot job exists """
    @wraps (func)
    async def inner (self, **kwargs):
        # XXX: not sure why it works with **kwargs, but not (user, args, reply)
        args = kwargs.get ('args')
        reply = kwargs.get ('reply')
        j = self.jobs.get (args.id, None)
        if not j:
            reply (f'Job {args.id} is unknown')
        else:
            ret = await func (self, job=j, **kwargs)
            return ret
    return inner

class Chromebot (ArgparseBot):
    __slots__ = ('jobs', 'tempdir', 'destdir', 'processLimit', 'blacklist', 'needVoice')

    def __init__ (self, host, port, ssl, nick, logger, channels=None,
            tempdir=None, destdir='.', processLimit=1,
            blacklist={}, needVoice=False, loop=None):
        self.needVoice = needVoice

        super().__init__ (host=host, port=port, ssl=ssl, nick=nick,
                logger=logger, channels=channels, loop=loop)

        self.jobs = {}
        self.tempdir = tempdir or tempfile.gettempdir()
        self.destdir = destdir
        self.processLimit = asyncio.Semaphore (processLimit)
        self.blacklist = blacklist

    def getParser (self):
        parser = NonExitingArgumentParser (prog=self.nick + ': ', add_help=False)
        subparsers = parser.add_subparsers(help='Sub-commands')

        archiveparser = subparsers.add_parser('a', help='Archive a site', add_help=False)
        #archiveparser.add_argument('--timeout', default=1*60*60, type=int, help='Maximum time for archival', metavar='SEC', choices=[60, 1*60*60, 2*60*60])
        #archiveparser.add_argument('--idle-timeout', default=10, type=int, help='Maximum idle seconds (i.e. no requests)', dest='idleTimeout', metavar='SEC', choices=[1, 10, 20, 30, 60])
        #archiveparser.add_argument('--max-body-size', default=None, type=int, dest='maxBodySize', help='Max body size', metavar='BYTES', choices=[1*1024*1024, 10*1024*1024, 100*1024*1024])
        archiveparser.add_argument('--concurrency', '-j', default=1, type=int, help='Parallel workers for this job', choices=range (1, 5))
        archiveparser.add_argument('--recursive', '-r', help='Enable recursion', choices=['0', '1', 'prefix'], default='0')
        archiveparser.add_argument('--insecure', '-k',
                help='Disable certificate checking', action='store_true')
        archiveparser.add_argument('url', help='Website URL', type=isValidUrl, metavar='URL')
        archiveparser.set_defaults (func=self.handleArchive,
                minPriv=NickMode.voice if self.needVoice else None)

        statusparser = subparsers.add_parser ('s', help='Get job status', add_help=False)
        statusparser.add_argument('id', help='Job id', metavar='UUID')
        statusparser.set_defaults (func=self.handleStatus, allowOnShutdown=True)

        abortparser = subparsers.add_parser ('r', help='Revoke/abort job', add_help=False)
        abortparser.add_argument('id', help='Job id', metavar='UUID')
        abortparser.set_defaults (func=self.handleAbort, allowOnShutdown=True,
                minPriv=NickMode.voice if self.needVoice else None)

        return parser

    def isBlacklisted (self, url):
        for k, v in self.blacklist.items():
            if k.match (url):
                return v
        return False

    async def handleArchive (self, user, args, reply):
        """ Handle the archive command """

        msg = self.isBlacklisted (args.url)
        if msg:
            reply (f'{args.url} cannot be queued: {msg}')
            return

        # make sure the job id is unique. Since ids are time-based we can just
        # wait.
        while True:
            j = Job (args.url, user.name)
            if j.id not in self.jobs:
                break
            time.sleep (0.01)
        self.jobs[j.id] = j

        logger = self.logger.bind (job=j.id)

        showargs = {
                'recursive': args.recursive,
                'concurrency': args.concurrency,
                }
        if args.insecure:
            showargs['insecure'] = args.insecure
        warcinfo = {'chromebot': {
                'jobid': j.id,
                'user': user.name,
                'queued': j.started,
                'url': args.url,
                'recursive': args.recursive,
                'concurrency': args.concurrency,
                }}
        grabCmd = ['crocoite-single']
        grabCmd.extend (['--warcinfo',
                '!' + json.dumps (warcinfo, cls=StrJsonEncoder)])
        if args.insecure:
            grabCmd.append ('--insecure')
        grabCmd.extend (['{url}', '{dest}'])
        # prefix warcinfo with !, so it won’t get expanded
        cmdline = ['crocoite',
                '--tempdir', self.tempdir,
                '--recursion', args.recursive,
                '--concurrency', str (args.concurrency),
                args.url,
                os.path.join (self.destdir,
                        j.id + '-{host}-{date}-{seqnum}.warc.gz'),
                '--'] + grabCmd

        strargs = ', '.join (map (lambda x: '{}={}'.format (*x), showargs.items ()))
        reply (f'{args.url} has been queued as {j.id} with {strargs}')
        logger.info ('queue', user=user.name, url=args.url, cmdline=cmdline,
                uuid='36cc34a6-061b-4cc5-84a9-4ab6552c8d75')

        async with self.processLimit:
            if j.status == Status.pending:
                # job was not aborted
                j.process = await asyncio.create_subprocess_exec (*cmdline,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                        stdin=asyncio.subprocess.DEVNULL,
                        start_new_session=True, limit=100*1024*1024)
                while True:
                    data = await j.process.stdout.readline ()
                    if not data:
                        break

                    # job is marked running after the first message is received from it
                    if j.status == Status.pending:
                        logger.info ('start', uuid='46e62d60-f498-4ab0-90e1-d08a073b10fb')
                        j.status = Status.running

                    data = json.loads (data)
                    msgid = data.get ('uuid')
                    if msgid == '24d92d16-770e-4088-b769-4020e127a7ff':
                        j.stats = data
                    elif msgid == '5b8498e4-868d-413c-a67e-004516b8452c':
                        j.rstats = data

                    # forward message, so the dashboard can use it
                    logger.info ('message',
                            uuid='5c0f9a11-dcd8-4182-a60f-54f4d3ab3687',
                            data=data)
                code = await j.process.wait ()

        if j.status == Status.running:
            logger.info ('finish', uuid='7b40ffbb-faab-4224-90ed-cd4febd8f7ec')
            j.status = Status.finished
        j.finished = datetime.utcnow ()

        stats = j.stats
        rstats = j.rstats
        reply (j.formatStatus ())

    @jobExists
    async def handleStatus (self, user, args, reply, job):
        """ Handle status command """

        rstats = job.rstats
        reply (job.formatStatus ())

    @jobExists
    async def handleAbort (self, user, args, reply, job):
        """ Handle abort command """

        if job.status not in {Status.pending, Status.running}:
            reply ('This job is not running.')
            return

        job.status = Status.aborted
        self.logger.info ('abort', job=job.id, user=user.name,
                uuid='865b3b3e-a54a-4a56-a545-f38a37bac295')
        if job.process and job.process.returncode is None:
            job.process.terminate ()

class Dashboard:
    __slots__ = ('fd', 'clients', 'loop', 'log', 'maxLog', 'pingInterval', 'pingTimeout')
    # these messages will not be forwarded to the browser
    ignoreMsgid = {
            # connect
            '01f7b138-ea53-4609-88e9-61f3eca3e7e7',
            # join
            '367063a5-9069-4025-907c-65ba88af8593',
            # disconnect
            '4c74b2c8-2403-4921-879d-2279ad85db72',
            # reconnect
            'c53555cb-e1a4-4b69-b1c9-3320269c19d7',
            }

    def __init__ (self, fd, loop, maxLog=1000, pingInterval=30, pingTimeout=10):
        self.fd = fd
        self.clients = set ()
        self.loop = loop
        # log buffer
        self.log = []
        self.maxLog = maxLog
        self.pingInterval = pingInterval
        self.pingTimeout = pingTimeout

    async def client (self, websocket, path):
        self.clients.add (websocket)
        try:
            for l in self.log:
                buf = json.dumps (l)
                await websocket.send (buf)
            while True:
                try:
                    msg = await asyncio.wait_for (websocket.recv(), timeout=self.pingInterval)
                except asyncio.TimeoutError:
                    try:
                        pong = await websocket.ping()
                        await asyncio.wait_for (pong, timeout=self.pingTimeout)
                    except asyncio.TimeoutError:
                        break
                except websockets.exceptions.ConnectionClosed:
                    break
        finally:
            self.clients.remove (websocket)

    def handleStdin (self):
        buf = self.fd.readline ()
        if not buf:
            return

        try:
            data = json.loads (buf)
        except json.decoder.JSONDecodeError:
            # ignore invalid
            return
        msgid = data['uuid']

        if msgid in self.ignoreMsgid:
            return

        # a few messages may contain sensitive information that we want to hide
        if msgid == '36cc34a6-061b-4cc5-84a9-4ab6552c8d75':
            # queue
            del data['cmdline']
        elif msgid == '5c0f9a11-dcd8-4182-a60f-54f4d3ab3687':
            nesteddata = data['data']
            nestedmsgid = nesteddata['uuid']
            if nestedmsgid == 'd1288fbe-8bae-42c8-af8c-f2fa8b41794f':
                del nesteddata['command']
            
        buf = json.dumps (data)
        for c in self.clients:
            # XXX can’t await here
            asyncio.ensure_future (c.send (buf))

        self.log.append (data)
        while len (self.log) > self.maxLog:
            self.log.pop (0)

    def run (self, host='localhost', port=6789):
        self.loop.add_reader (self.fd, self.handleStdin)
        return websockets.serve(self.client, host, port)

