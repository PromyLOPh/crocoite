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

import asyncio
import pytest

from aiohttp import web
import websockets

from .browser import ChromeService, NullService
from .devtools import Browser, Tab, MethodNotFound, Crashed, InvalidParameter

@pytest.fixture
async def browser ():
    with ChromeService () as url:
        yield Browser (url)

@pytest.fixture
async def tab (browser):
    async with browser as tab:
        yield tab
        # make sure there are no transactions left over (i.e. no unawaited requests)
        assert not tab.transactions

async def hello(request):
    return web.Response(text="Hello, world")

@pytest.fixture
async def server ():
    """ Simple HTTP server for testing notifications """
    app = web.Application()
    app.add_routes([web.get('/', hello)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', 8080)
    await site.start()
    yield app
    await runner.cleanup ()

@pytest.mark.asyncio
async def test_tab_create (tab):
    """ Creating tabs works """
    assert isinstance (tab, Tab)
    version = await tab.Browser.getVersion ()
    assert 'protocolVersion' in version
    assert tab.pending == 0

@pytest.mark.asyncio
async def test_tab_close (browser):
    """ Tabs are closed after using them """
    async with browser as tab:
        tid = tab.id
    # give the browser some time to close the tab
    await asyncio.sleep (0.5)
    tabs = [t['id'] async for t in browser]
    assert tid not in tabs

@pytest.mark.asyncio
async def test_tab_notify_enable_disable (tab):
    """ Make sure enabling/disabling notifications works for all known namespaces """
    for name in ('Debugger', 'DOM', 'Log', 'Network', 'Page', 'Performance', 'Profiler', 'Runtime', 'Security'):
        f = getattr (tab, name)
        await f.enable ()
        await f.disable ()

@pytest.mark.asyncio
async def test_tab_unknown_method (tab):
    with pytest.raises (MethodNotFound):
        await tab.Nonexistent.foobar ()

@pytest.mark.asyncio
async def test_tab_invalid_argument (tab):
    # should be string
    with pytest.raises (InvalidParameter):
        await tab.Page.captureScreenshot (format=123)

    with pytest.raises (InvalidParameter):
        await tab.Page.captureScreenshot (format=[123])

    with pytest.raises (InvalidParameter):
        await tab.Page.captureScreenshot (format={123: '456'})

@pytest.mark.asyncio
async def test_tab_crash (tab):
    with pytest.raises (Crashed):
        await tab.Page.crash ()

    # caling anything else now should fail as well
    with pytest.raises (Crashed):
        version = await tab.Browser.getVersion ()

@pytest.mark.asyncio
async def test_load (tab, server):
    await tab.Network.enable ()
    await tab.Page.navigate (url='http://localhost:8080')
    method, req = await tab.get ()
    assert method == tab.Network.requestWillBeSent
    method, resp = await tab.get ()
    assert method == tab.Network.responseReceived
    assert tab.pending == 0
    body = await tab.Network.getResponseBody (requestId=req['requestId'])
    assert body['body'] == "Hello, world"
    await tab.Network.disable ()

@pytest.mark.asyncio
async def test_recv_failure(browser):
    """ Inject failure into receiver process and crash it """
    async with browser as tab:
        await tab.ws.close ()
        with pytest.raises (Crashed):
            await tab.Browser.getVersion ()

    async with browser as tab:
        await tab.ws.close ()
        with pytest.raises (Crashed):
            await tab.get ()

    async with browser as tab:
        handle = asyncio.ensure_future (tab.get ())
        await tab.ws.close ()
        with pytest.raises (Crashed):
            await handle

def test_tab_function (tab):
    assert tab.Network.enable.name == 'Network.enable'
    assert tab.Network.disable == tab.Network.disable
    assert tab.Network.enable != tab.Network.disable
    assert tab.Network != tab.Network.enable
    assert callable (tab.Network.enable)
    assert not callable (tab.Network.enable.name)
    assert 'Network.enable' in repr (tab.Network.enable)

def test_tab_function_hash (tab):
    d = {tab.Network.enable: 1, tab.Network.disable: 2, tab.Page: 3, tab.Page.enable: 4}
    assert len (d) == 4

@pytest.mark.asyncio
async def test_ws_ping(tab):
    """
    Chrome does not like websocket pings and closes the connection if it
    receives one. Not sure why.
    """
    with pytest.raises (Crashed):
        await tab.ws.ping ()
        await tab.Browser.getVersion ()

