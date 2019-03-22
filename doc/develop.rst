Development
-----------

Generally crocoite provides reasonable defaults for Google Chrome via its
`devtools module`_. When debugging this software it might be necessary to open
a non-headless instance of the browser by running

.. code:: bash

   google-chrome-stable --remote-debugging-port=9222 --auto-open-devtools-for-tabs

and then passing the option ``--browser=http://localhost:9222`` to
``crocoite-grab``. This allows human intervention through the browser’s builtin
console.

.. _devtools module: crocoite/devtools.py

