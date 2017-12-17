crocoite
========

Archive websites using `headless Google Chrome_` and its DevTools protocol.

.. _headless Google Chrome: https://developers.google.com/web/updates/2017/04/headless-chrome

Dependencies
------------

- Python 3
- pychrome_ 
- warcio_
- html5lib_
- Celery_

.. _pychrome: https://github.com/fate0/pychrome
.. _warcio: https://github.com/webrecorder/warcio
.. _html5lib: https://github.com/html5lib/html5lib-python
.. _Celery: http://www.celeryproject.org/

Usage
-----

One-shot commandline interface and pywb_ playback::

    crocoite-standalone http://example.com/ example.com.warc.gz
    rm -rf collections && wb-manager init test && wb-manager add test example.com.warc.gz
    wayback &
    $BROWSER http://localhost:8080

.. _pywb: https://github.com/ikreymer/pywb

Injecting JavaScript
^^^^^^^^^^^^^^^^^^^^

A lot of sites need some form of interaction to load more content. Twitter for
instance continously loads new posts when scrolling to the bottom of the page.
crocoite can emulate these user interactions by injecting JavaScript into the
page before loading it. For instance ``--onload=scroll.js`` scrolls the page to
the bottom.

If extra work is required before taking a DOM snapshot, additional scripts can
be run with ``--onsnapshot=canvas-snapshot.js``, which replaces all HTML
``<canvas>`` elements with a static picture of their current contents.

Example scripts can be found in the directory ``crocoite/data/``.

Caveats
-------

- Original HTTP requests/responses are not available. They are rebuilt from
  parsed data. Character encoding for text documents is changed to UTF-8.
- Some sites request assets based on screen resolution, pixel ratio and
  supported image formats (webp). Replaying those with different parameters
  won’t work, since assets for those are missing. Example: missguided.com.
- Some fetch different scripts based on user agent. Example: youtube.com.
- Requests containing randomly generated JavaScript callback function names
  won’t work. Example: weather.com.
- Range requests (Range: bytes=1-100) are captured as-is, making playback
  difficult
- Content body of HTTP redirects cannot be retrived due to race condition

Most of these issues can be worked around by using the DOM snapshot, which is
also saved. This causes its own set of issues though:

- JavaScript-based navigation does not work.

Distributed crawling
--------------------

Configure using celeryconfig.py

.. code:: python

    broker_url = 'pyamqp://'
    result_backend = 'rpc://'
    warc_filename = '{domain}-{date}-{id}.warc.gz'
    temp_dir = '/tmp/'
    finished_dir = '/tmp/finished'

Start a Celery worker::

    celery -A crocoite.cli worker --loglevel=info

Then queue archive job::

    crocoite-standalone --distributed http://example.com ''

The worker will create a temporary file named according to ``warc_filename`` in
``/tmp`` while archiving and move it to ``/tmp/finished`` when done.

IRC bot
^^^^^^^

Configure sopel_ (``~/.sopel/default.cfg``) to use the plugin located in
``contrib/celerycrocoite.py``

.. code:: ini

    [core]
    nick = chromebot
    host = irc.efnet.fr
    port = 6667
    owner = someone
    extra = /path/to/crocoite/contrib
    enable = celerycrocoite
    channels = #somechannel

Then start it by running ``sopel``. The bot must be addressed directly (i.e.
``chromebot: <command>``). The following commands are currently supported:

ao <url>
    Archives <url> and all of its resources (images, css, …). A unique UID
    (UUID) is assigned to each job.
s <uuid>
    Get status of job with <uuid>
r <uuid>
    Revoke job with <uuid>. If it started already the job will be killed.

.. _sopel: https://sopel.chat/

