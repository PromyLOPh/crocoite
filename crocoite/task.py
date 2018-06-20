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
Celery distributed tasks
"""

import os, logging

from urllib.parse import urlsplit
from datetime import datetime
from operator import attrgetter
from itertools import chain

def _monkeyPatchSyncTasks ():
    """ Result sets don’t support the argument disable_sync_subtasks argument """
    import celery.result
    celery.result.assert_will_not_block = lambda: None

_monkeyPatchSyncTasks ()
from celery import Celery
from celery.utils.log import get_task_logger

from .browser import ChromeService, BrowserCrashed
from .controller import SinglePageController, ControllerSettings, RecursiveController, defaultSettings, DepthLimit, PrefixLimit, StatsHandler
from . import behavior
from .cli import parseRecursive
from .warc import WarcHandler

app = Celery ('crocoite.distributed')
app.config_from_object('celeryconfig')
app.conf.task_routes = {
        'crocoite.task.archive': {'queue': 'crocoite.archive'},
        'crocoite.task.controller': {'queue': 'crocoite.controller'},
        # <method>.chunks is actually a starmap job
        'celery.starmap': {'queue': 'crocoite.archive'},
        }
app.conf.task_default_queue = 'crocoite.default'
# disable prefetching, since our tasks usually run for a _very_ long time
app.conf.worker_prefetch_multiplier = 1
logger = get_task_logger('crocoite.distributed.archive')

@app.task(bind=True, track_started=True)
def archive (self, url, settings, enabledBehaviorNames):
    """
    Archive a single URL

    Supports these config keys (celeryconfig):

    warc_filename = '{domain}-{date}-{id}.warc.gz'
    temp_dir = '/tmp/'
    finished_dir = '/tmp/finished'
    """

    parsedUrl = urlsplit (url)
    outFile = app.conf.warc_filename.format (
                    id=self.request.root_id,
                    domain=parsedUrl.hostname.replace ('/', '-'),
                    date=datetime.utcnow ().isoformat (),
                    )
    outPath = os.path.join (app.conf.temp_dir, outFile)
    fd = open (outPath, 'wb')

    handler = [StatsHandler (), WarcHandler (fd)]
    enabledBehavior = list (filter (lambda x: x.name in enabledBehaviorNames, behavior.available))
    settings = ControllerSettings (**settings)
    try:
        controller = SinglePageController (url, fd, behavior=enabledBehavior,
                settings=settings, handler=handler)
        controller.run ()
    except BrowserCrashed:
        # nothing we can do about that
        logger.error ('browser crashed for {}'.format (url))

    os.makedirs (app.conf.finished_dir, exist_ok=True)
    outPath = os.path.join (app.conf.finished_dir, outFile)
    os.rename (fd.name, outPath)

    return handler[0].stats

from collections import UserDict

class IntegerDict (UserDict):
    """ Dict with dict/dict per-item arithmetic propagation, i.e. {1: 2}+{1: 1}={1: 3} """
    def __add__ (self, b):
        newdict = self.__class__ (self)
        for k, v in b.items ():
            if k in self:
                newdict[k] += v
            else:
                newdict[k] = v
        return newdict

class DistributedRecursiveController (RecursiveController):
    """ Distributed, recursive controller using celery """

    def __init__ (self, url, service=ChromeService (), behavior=behavior.available, \
            logger=logging.getLogger(__name__), settings=defaultSettings,
            recursionPolicy=DepthLimit (0), concurrency=1):
        super ().__init__ (url, None, service, behavior, logger, settings, recursionPolicy)
        self.concurrency = concurrency
        self.stats = IntegerDict ()

    def fetch (self, urls):
        def chunksIter (urls):
            for u in urls:
                yield (u, self.settings.toDict (), list (map (attrgetter ('name'), self.behavior)))
        itemsPerTask = len (urls)//self.concurrency
        if itemsPerTask <= 0:
            itemsPerTask = len (urls)
        result = archive.chunks (chunksIter (urls), itemsPerTask).apply_async ().get ()
        self.stats = sum (chain.from_iterable (result), self.stats)

@app.task(bind=True, track_started=True)
def controller (self, url, settings, enabledBehaviorNames, recursive, concurrency):
    """ Recursive controller """

    recursionPolicy = parseRecursive (recursive, url)
    enabledBehavior = list (filter (lambda x: x.name in enabledBehaviorNames, behavior.available))
    settings = ControllerSettings (**settings)
    controller = DistributedRecursiveController (url, None, behavior=enabledBehavior,
            settings=settings, recursionPolicy=recursionPolicy, concurrency=concurrency)
    controller.run ()
    return dict (controller.stats)


