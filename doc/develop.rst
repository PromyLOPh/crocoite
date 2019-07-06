Development
-----------

Generally crocoite provides reasonable defaults for Google Chrome via
:py:mod:`crocoite.devtools`. When debugging this software it might be necessary
to open a non-headless instance of the browser by running

.. code:: bash

   google-chrome-stable --remote-debugging-port=9222 --auto-open-devtools-for-tabs

and then passing the option :option:`--browser=http://localhost:9222` to
:program:`crocoite-single`. This allows human intervention through the
browser’s builtin console.

Release guide
^^^^^^^^^^^^^

crocoite uses `semantic versioning`_. To create a new release, bump the version
number in ``setup.py`` according to the linked guide, create distribution
packages::

    python setup.py sdist bdist_wheel

Verify them::

    twine check dist/*

Try to install and use them in a separate sandbox. And finally sign and upload
a new version to pypi_::

    gpg --detach-sign --armor dist/*.tar.gz
    twine upload dist/*

Then update the documentation using :program:`sphing-doc` and upload it as well.

.. _semantic versioning: https://semver.org/spec/v2.0.0.html
.. _pypi: https://pypi.org

