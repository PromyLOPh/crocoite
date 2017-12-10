crocoite
========

Archive websites using Google Chrome and its DevTools protocol.
Tested with Google Chrome 62.0.3202.89 for Linux only.

Dependencies
------------

- Python 3
- pychrome_ 
- warcio_
- html5lib_

.. _pychrome: https://github.com/fate0/pychrome
.. _warcio: https://github.com/webrecorder/warcio
.. _html5lib: https://github.com/html5lib/html5lib-python

Usage
-----

One-shot commandline interface and pywb_ playback::

    google-chrome-stable --window-size=1920,1080 --remote-debugging-port=9222 &
    crocoite-standalone http://example.com/ example.com.warc.gz
    rm -rf collections && wb-manager init test && wb-manager add test example.com.warc.gz
    wayback &
    $BROWSER http://localhost:8080

For `headless Google Chrome`_ add the parameters ``--headless --disable-gpu``.

.. _pywb: https://github.com/ikreymer/pywb
.. _headless Google Chrome: https://developers.google.com/web/updates/2017/04/headless-chrome

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

    crocoite-standalone --distributed …

Alternative: IRC bot using sopel_. Use contrib/celerycrocoite.py

~/.sopel/default.cfg

.. code:: ini

    [core]
    nick = chromebot
    host = irc.efnet.fr
    port = 6667
    owner = someone
    extra = /path/to/crocoite/contrib
    enable = celerycrocoite
    channels = #somechannel

Then in #somechannel ``chromebot: ao <url>``

