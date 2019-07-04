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
Communication with Google Chrome through its DevTools protocol.
"""

import json, asyncio, logging, os
from tempfile import mkdtemp
import shutil
import aiohttp, websockets
from yarl import URL

from .util import StrJsonEncoder

logger = logging.getLogger (__name__)

class Browser:
    """
    Communicate with Google Chrome through its DevTools protocol.
    
    Asynchronous context manager that creates a new Tab when entering.
    Destroyed upon exit.
    """

    __slots__ = ('session', 'url', 'tab', 'loop')

    def __init__ (self, url, loop=None):
        self.url = URL (url)
        self.session = None
        self.tab = None
        self.loop = loop

    async def __aiter__ (self):
        """ List all tabs """
        async with aiohttp.ClientSession (loop=self.loop) as session:
            async with session.get (self.url.with_path ('/json/list')) as r:
                resp = await r.json ()
                for tab in resp:
                    if tab['type'] == 'page':
                        yield tab

    async def __aenter__ (self):
        """ Create tab """
        assert self.tab is None
        assert self.session is None
        self.session = aiohttp.ClientSession (loop=self.loop)
        async with self.session.get (self.url.with_path ('/json/new')) as r:
            resp = await r.json ()
            self.tab = await Tab.create (**resp)
            return self.tab

    async def __aexit__ (self, *args):
        assert self.tab is not None
        assert self.session is not None
        await self.tab.close ()
        async with self.session.get (self.url.with_path (f'/json/close/{self.tab.id}')) as r:
            resp = await r.text ()
            assert resp == 'Target is closing'
        self.tab = None
        await self.session.close ()
        self.session = None
        return False

class TabFunction:
    """
    Helper class for infinite-depth tab functions.

    A method usually consists of namespace (Page, Network, …) and function name
    (getFoobar) separated by a dot. This class creates these function names
    while providing an intuitive Python interface (tab.Network.getFoobar).

    This was inspired by pychrome.
    """

    __slots__ = ('name', 'tab')

    def __init__ (self, name, tab):
        self.name = name
        self.tab = tab

    def __eq__ (self, b):
        assert isinstance (b, TabFunction)
        return self.name == b.name

    def __hash__ (self):
        return hash (self.name)

    def __getattr__ (self, k):
        return TabFunction (f'{self.name}.{k}', self.tab)

    async def __call__ (self, **kwargs):
        return await self.tab (self.name, **kwargs)

    def __repr__ (self):
        return f'<TabFunction {self.name}>'

class TabException (Exception):
    pass

class Crashed (TabException):
    pass

class MethodNotFound (TabException):
    pass

class InvalidParameter (TabException):
    pass

# map error codes to native exceptions
errorMap = {-32601: MethodNotFound, -32602: InvalidParameter}

class Tab:
    """
    Communicate with a single Google Chrome browser tab.
    """
    __slots__ = ('id', 'wsUrl', 'ws', 'msgid', 'transactions', 'queue', '_recvHandle', 'crashed')

    def __init__ (self, tabid, ws):
        """ Do not use this method, use Browser context manager. """
        self.id = tabid
        self.ws = ws
        self.msgid = 1
        self.crashed = False
        self.transactions = {}
        self.queue = asyncio.Queue ()

    def __getattr__ (self, k):
        return TabFunction (k, self)

    async def __call__ (self, method, **kwargs):
        """
        Actually call browser method with kwargs
        """

        if self.crashed or self._recvHandle.done ():
            raise Crashed ()

        msgid = self.msgid
        self.msgid += 1
        message = {'method': method, 'params': kwargs, 'id': msgid}
        t = self.transactions[msgid] = {'event': asyncio.Event (), 'result': None}
        logger.debug (f'← {message}')
        await self.ws.send (json.dumps (message, cls=StrJsonEncoder))
        await t['event'].wait ()
        ret = t['result']
        del self.transactions[msgid]
        if isinstance (ret, Exception):
            raise ret
        return ret

    async def _recvProcess (self):
        """
        Receive process that dispatches received websocket frames

        These are either events which will be put into a queue or request
        responses which unblock a __call__.
        """

        async def markCrashed (reason):
            # all pending requests can be considered failed since the
            # browser state is lost
            for v in self.transactions.values ():
                v['result'] = Crashed (reason)
                v['event'].set ()
            # and all future requests will fail as well until reloaded
            self.crashed = True
            await self.queue.put (Crashed (reason))

        while True:
            try:
                msg = await self.ws.recv ()
                msg = json.loads (msg)
            except Exception as e:
                # right now we cannot recover from this
                await markCrashed (e)
                break
            logger.debug (f'→ {msg}')
            if 'id' in msg:
                msgid = msg['id']
                t = self.transactions.get (msgid, None)
                if t is not None:
                    if 'error' in msg:
                        e = msg['error']
                        t['result'] = errorMap.get (e['code'], TabException) (e['code'], e['message'])
                    else:
                        t['result'] = msg['result']
                    t['event'].set ()
                else:
                    # ignore stale result
                    pass # pragma: no cover
            elif 'method' in msg:
                # special treatment
                if msg['method'] == 'Inspector.targetCrashed':
                    await markCrashed ('target')
                else:
                    await self.queue.put (msg)
            else:
                assert False # pragma: no cover

    async def run (self):
        self._recvHandle = asyncio.ensure_future (self._recvProcess ())

    async def close (self):
        self._recvHandle.cancel ()
        await self.ws.close ()
        # no join, throw away the queue. There will be nobody listening on the
        # other end.
        #await self.queue.join ()

    @property
    def pending (self):
        return self.queue.qsize ()

    async def get (self):
        def getattrRecursive (obj, name):
            if '.' in name:
                n, ext = name.split ('.', 1)
                return getattrRecursive (getattr (obj, n), ext)
            return getattr (obj, name)

        if self.crashed:
            raise Crashed ()

        ret = await self.queue.get ()
        if isinstance (ret, Exception):
            raise ret
        return getattrRecursive (self, ret['method']), ret['params']

    @classmethod
    async def create (cls, **kwargs):
        """ Async init """
        # increase size limit of a single frame to something ridiciously high,
        # so we can safely grab screenshots
        maxSize = 100*1024*1024 # 100 MB
        # chrome does not like pings and kills the connection, disable them
        ws = await websockets.connect(kwargs['webSocketDebuggerUrl'],
                max_size=maxSize, ping_interval=None)
        ret = cls (kwargs['id'], ws)
        await ret.run ()
        return ret

class Process:
    """ Start Google Chrome listening on a random port """

    __slots__ = ('binary', 'windowSize', 'p', 'userDataDir')

    def __init__ (self, binary='google-chrome-stable', windowSize=(1920, 1080)):
        self.binary = binary
        self.windowSize = windowSize
        self.p = None

    async def __aenter__ (self):
        assert self.p is None
        self.userDataDir = mkdtemp (prefix=__package__ + '-chrome-userdata-')
        # see https://github.com/GoogleChrome/chrome-launcher/blob/master/docs/chrome-flags-for-tools.md
        args = [self.binary,
                '--window-size={},{}'.format (*self.windowSize),
                f'--user-data-dir={self.userDataDir}', # use temporory user dir
                '--no-default-browser-check',
                '--no-first-run', # don’t show first run screen
                '--disable-breakpad', # no error reports
                '--disable-extensions',
                '--disable-infobars',
                '--disable-notifications', # no libnotify
                '--disable-background-networking', # disable background services (updating, safe browsing, …)
                '--safebrowsing-disable-auto-update',
                '--disable-sync', # no google account syncing
                '--metrics-recording-only', # do not submit metrics
                '--disable-default-apps',
                '--disable-background-timer-throttling',
                '--disable-client-side-phishing-detection',
                '--disable-popup-blocking',
                '--disable-prompt-on-repost',
                '--enable-automation', # enable various automation-related things
                '--password-store=basic',
                '--headless',
                '--disable-gpu',
                '--hide-scrollbars', # hide scrollbars on screenshots
                '--mute-audio', # don’t play any audio
                '--remote-debugging-port=0', # pick a port. XXX: we may want to use --remote-debugging-pipe instead
                '--homepage=about:blank',
                'about:blank']
        # start new session, so ^C does not affect subprocess
        self.p = await asyncio.create_subprocess_exec (*args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                stdin=asyncio.subprocess.DEVNULL,
                start_new_session=True)
        port = None
        # chrome writes its current active devtools port to a file. due to the
        # sleep() this is rather ugly, but should work with all versions of the
        # browser.
        for i in range (100):
            try:
                with open (os.path.join (self.userDataDir, 'DevToolsActivePort'), 'r') as fd:
                    port = int (fd.readline ().strip ())
                    break
            except FileNotFoundError:
                await asyncio.sleep (0.2)
        if port is None:
            raise Exception ('Chrome died on us.')

        return URL.build(scheme='http', host='localhost', port=port)

    async def __aexit__ (self, *exc):
        self.p.terminate ()
        await self.p.wait ()

        # Try to delete the temporary directory multiple times. It looks like
        # Chrome will change files in there even after it exited (i.e. .wait()
        # returned). Very strange.
        for i in range (5):
            try:
                shutil.rmtree (self.userDataDir)
                break
            except:
                await asyncio.sleep (0.2)

        self.p = None
        return False

class Passthrough:
    __slots__ = ('url', )

    def __init__ (self, url):
        self.url = URL (url)

    async def __aenter__ (self):
        return self.url

    async def __aexit__ (self, *exc):
        return False

