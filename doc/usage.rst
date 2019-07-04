Usage
-----

One-shot command line interface and pywb_ playback:

.. code:: bash

    pip install pywb
    crocoite http://example.com/ example.com.warc.gz
    rm -rf collections && wb-manager init test && wb-manager add test example.com.warc.gz
    wayback &
    $BROWSER http://localhost:8080

.. _pywb: https://github.com/ikreymer/pywb

IRC bot
^^^^^^^

A simple IRC bot (“chromebot”) is provided with the command ``crocoite-irc``.
It reads its configuration from a config file like the example provided in
``contrib/chromebot.json`` and supports the following commands:

a <url> -j <concurrency> -r <policy>
    Archive <url> with <concurrency> processes according to recursion <policy>
s <uuid>
    Get job status for <uuid>
r <uuid>
    Revoke or abort running job with <uuid>
