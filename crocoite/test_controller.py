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

import asyncio

from yarl import URL
from aiohttp import web

import pytest

from .logger import Logger
from .controller import ControllerSettings, SinglePageController, SetEntry, \
        IdleStateTracker
from .browser import PageIdle
from .devtools import Process
from .test_browser import loader

@pytest.mark.asyncio
async def test_controller_timeout ():
    """ Make sure the controller terminates, even if the site keeps reloading/fetching stuff """

    async def f (req):
        return web.Response (body="""<html>
<body>
<p>hello</p>
<script>
window.setTimeout (function () { window.location = '/' }, 250);
window.setInterval (function () { fetch('/').then (function (e) { console.log (e) }) }, 150);
</script>
</body>
</html>""", status=200, content_type='text/html', charset='utf-8')

    url = URL.build (scheme='http', host='localhost', port=8080)
    app = web.Application ()
    app.router.add_route ('GET', '/', f)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, url.host, url.port)
    await site.start()

    loop = asyncio.get_event_loop ()
    try:
        logger = Logger ()
        settings = ControllerSettings (idleTimeout=1, timeout=5)
        controller = SinglePageController (url=url, logger=logger,
                service=Process (), behavior=[], settings=settings)
        # give the controller a little more time to finish, since there are
        # hard-coded asyncio.sleep calls in there right now.
        # XXX fix this
        before = loop.time ()
        await asyncio.wait_for (controller.run (), timeout=settings.timeout*2)
        after = loop.time ()
        assert after-before >= settings.timeout, (settings.timeout*2, after-before)
    finally:
        # give the browser some time to close before interrupting the
        # connection by destroying the HTTP server
        await asyncio.sleep (1)
        await runner.cleanup ()

@pytest.mark.asyncio
async def test_controller_idle_timeout ():
    """ Make sure the controller terminates, even if the site keeps reloading/fetching stuff """

    async def f (req):
        return web.Response (body="""<html>
<body>
<p>hello</p>
<script>
window.setInterval (function () { fetch('/').then (function (e) { console.log (e) }) }, 2000);
</script>
</body>
</html>""", status=200, content_type='text/html', charset='utf-8')

    url = URL.build (scheme='http', host='localhost', port=8080)
    app = web.Application ()
    app.router.add_route ('GET', '/', f)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, url.host, url.port)
    await site.start()

    loop = asyncio.get_event_loop ()
    try:
        logger = Logger ()
        settings = ControllerSettings (idleTimeout=1, timeout=60)
        controller = SinglePageController (url=url, logger=logger,
                service=Process (), behavior=[], settings=settings)
        before = loop.time ()
        await asyncio.wait_for (controller.run (), settings.timeout*2)
        after = loop.time ()
        assert settings.idleTimeout <= after-before <= settings.idleTimeout*2+3
    finally:
        await runner.cleanup ()

def test_set_entry ():
    a = SetEntry (1, a=2, b=3)
    assert a == a
    assert hash (a) == hash (a)

    b = SetEntry (1, a=2, b=4)
    assert a == b
    assert hash (a) == hash (b)

    c = SetEntry (2, a=2, b=3)
    assert a != c
    assert hash (a) != hash (c)

@pytest.mark.asyncio
async def test_idle_state_tracker ():
    # default is idle
    loop = asyncio.get_event_loop ()
    idle = IdleStateTracker (loop)
    assert idle._idle

    # idle change
    await idle.push (PageIdle (False))
    assert not idle._idle

    # nothing happens for other objects
    await idle.push ({})
    assert not idle._idle

    # no state change -> wait does not return
    with pytest.raises (asyncio.TimeoutError):
        await asyncio.wait_for (idle.wait (0.1), timeout=1)

    # wait at least timeout
    delta = 0.2
    timeout = 1
    await idle.push (PageIdle (True))
    assert idle._idle
    start = loop.time ()
    await idle.wait (timeout)
    end = loop.time ()
    assert (timeout-delta) < (end-start) < (timeout+delta)

