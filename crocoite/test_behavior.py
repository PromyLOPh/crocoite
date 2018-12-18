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

import asyncio, os, yaml, re
from functools import partial
import pytest
from yarl import URL

import pkg_resources
from .logger import Logger
from .devtools import Process
from .behavior import Scroll, Behavior
from .controller import SinglePageController

with pkg_resources.resource_stream (__name__, os.path.join ('data', 'click.yaml')) as fd:
    sites = list (yaml.load_all (fd))
clickParam = []
for o in sites:
    for s in o['selector']:
        for u in s.get ('urls', []):
            clickParam.append ((u, s['selector']))

class ClickTester (Behavior):
    """
    Test adapter checking a given selector exists after loading the page
    """

    __slots__ = ('selector', )

    name = 'testclick'

    def __init__ (self, loader, logger, selector):
        super ().__init__ (loader, logger)
        self.selector = selector

    async def onfinish (self):
        tab = self.loader.tab
        results = await tab.DOM.getDocument ()
        rootNode = results['root']['nodeId']
        results = await tab.DOM.querySelectorAll (nodeId=rootNode, selector=self.selector)
        assert results['nodeIds'], self.selector

        # XXX: this is not true for every element we click. Github uses <button
        # type=submit> and <form> without an event listener on the <button>
#        # verify that an event listener exists
#        for nid in results['nodeIds']:
#            obj = (await tab.DOM.resolveNode (nodeId=nid))['object']
#            assert obj['type'] == 'object'
#            listeners = (await tab.DOMDebugger.getEventListeners (objectId=obj['objectId']))['listeners']
#            assert any (map (lambda x: x['type'] == 'click', listeners)), listeners

        return
        yield

@pytest.mark.parametrize("url,selector", clickParam)
@pytest.mark.asyncio
@pytest.mark.xfail(reason='depends on network access')
async def test_click_selectors (url, selector):
    """
    Make sure the CSS selector exists on an example url
    """
    logger = Logger ()
    # Some selectors are loaded dynamically and require scrolling
    controller = SinglePageController (url=url, logger=logger,
            service=Process (),
            behavior=[Scroll, partial(ClickTester, selector=selector)])
    await controller.run ()

matchParam = []
for o in sites:
    for s in o['selector']:
        for u in s.get ('urls', []):
            matchParam.append ((o['match'], URL (u)))

@pytest.mark.parametrize("match,url", matchParam)
@pytest.mark.asyncio
async def test_click_match (match, url):
    """ Test urls must match """
    # keep this aligned with click.js
    assert re.match (match, url.host, re.I)

