crocoite
========

Archive websites using Google Chrome and its DevTools protocol.
Tested with Google Chrome 62.0.3202.89 for Linux only.

Dependencies
------------

- Python 3
- pychrome_ 
- warcio_
- html5lib

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
  parsed data. Character encoding for text documents is changed to UTF-8.
- Some sites request assets based on screen resolution, pixel ratio and
  supported image formats (webp). Replaying those with different parameters
  won’t work, since assets for those are missing. Example: missguided.com.
- Some fetch different scripts based on user agent. Example: youtube.com.
- Requests containing randomly generated JavaScript callback function names
  won’t work. Example: weather.com.

Most of these issues can be worked around by using the DOM snapshot, which is
also saved. This causes its own set of issues though:

- JavaScript-based navigation does not work.
- Scripts modifying styles based on scrolling position are stuck at the end of
  page state at the moment. Example: twitter.com
- CSS-based asset loading (screen size, pixel ratio, …) still does not work.
- Canvas contents are probably not preserved.

