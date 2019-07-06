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

Recursion
^^^^^^^^^

By default crocoite will only retrieve the URL specified on the command line.
However it can follow links as well. There’s currently two recursion strategies
available, depth- and prefix-based.

.. code:: bash

   crocoite -r 1 https://example.com/ example.com.warc.gz

will retrieve ``example.com`` and all pages directly refered to by it.
Increasing the number increases the depth, so a value of :samp:`2` would first grab
``example.com``, queue all pages linked there as well as every reference on
each of those pages.

On the other hand

.. code:: bash

   crocoite -r prefix https://example.com/dir/ example.com.warc.gz

will retrieve the URL specified and all pages referenced which have the same
URL prefix. There trailing slash is significant. Without it crocoite would also
grab ``/dir-something`` or ``/dir.html`` for example.

If an output file template is used each page is written to an individual file. For example

.. code:: bash

   crocoite -r prefix https://example.com/ '{host}-{date}-{seqnum}.warc.gz'

will write one file page page to files like
:file:`example.com-2019-09-09T15:15:15+02:00-1.warc.gz`. ``seqnum`` is unique to
each page of a single job and should always be used.

When running a recursive job, increasing the concurrency (i.e. how many pages
are fetched at the same time) can speed up the process. For example you can
pass :option:`-j 4` to retrieve four pages at the same time. Keep in mind that each
process starts a full browser that requires a lot of resources (one to two GB
of RAM and one or two CPU cores).

Customizing
^^^^^^^^^^^

Under the hood crocoite starts one instance of :program:`crocoite-single` to fetch
each page. You can customize its options by appending a command template like
this:

.. code:: bash

   crocoite -r prefix https://example.com example.com.warc.gz -- \
        crocoite-single --timeout 5 -k '{url}' '{dest}'

This reduces the global timeout to 5 seconds and ignores TLS errors. If an
option is prefixed with an exclamation mark (``!``) it will not be expanded.
This is useful for passing :option:`--warcinfo`, which expects JSON-encoded data.

IRC bot
^^^^^^^

A simple IRC bot (“chromebot”) is provided with the command :program:`crocoite-irc`.
It reads its configuration from a config file like the example provided in
:file:`contrib/chromebot.json` and supports the following commands:

a <url> -j <concurrency> -r <policy> -k
    Archive <url> with <concurrency> processes according to recursion <policy>
s <uuid>
    Get job status for <uuid>
r <uuid>
    Revoke or abort running job with <uuid>
