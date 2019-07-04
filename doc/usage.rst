Usage
-----

Quick start using pywb_, expects Google Chrome to be installed already:

.. code:: bash

    pip install crocoite pywb
    crocoite http://example.com/ example.com.warc.gz
    wb-manager init test && wb-manager add test example.com.warc.gz
    wayback &
    $BROWSER http://localhost:8080

.. _pywb: https://github.com/ikreymer/pywb

It is recommended to install at least Micrsoft’s Corefonts_ as well as DejaVu_,
Liberation_ or a similar font family covering a wide range of character sets.
Otherwise page screenshots may be unusable due to missing glyphs.

.. _Corefonts: http://corefonts.sourceforge.net/
.. _DejaVu: https://dejavu-fonts.github.io/
.. _Liberation: https://pagure.io/liberation-fonts

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
