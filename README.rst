crocoite
========

Archive websites using Google Chrome and its DevTools protocol.
Tested with Google Chrome 62.0.3202.89 for Linux only.

Dependencies
------------

- Python 3
- pychrome_ 
- warcio_

.. _pychrome: https://github.com/fate0/pychrome
.. _warcio: https://github.com/webrecorder/warcio

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

Caveats
-------

- Original HTTP requests/responses are not available. They are rebuilt from
  data available. Character encoding for text documents is changed to UTF-8.
- Some sites request different assets based on screen resolution, some fetch
  different scripts based on user agent.

