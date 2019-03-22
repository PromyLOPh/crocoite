Installation
------------

These dependencies must be present to run crocoite:

- Python ≥3.6
- PyYAML_
- aiohttp_
- websockets_
- warcio_
- html5lib_
- yarl_
- multidict_
- bottom_ (IRC client)
- `Google Chrome`_

.. _PyYAML: https://pyyaml.org/wiki/PyYAML
.. _aiohttp: https://aiohttp.readthedocs.io/
.. _websockets: https://websockets.readthedocs.io/
.. _warcio: https://github.com/webrecorder/warcio
.. _html5lib: https://github.com/html5lib/html5lib-python
.. _bottom: https://github.com/numberoverzero/bottom
.. _Google Chrome: https://www.google.com/chrome/
.. _yarl: https://yarl.readthedocs.io/
.. _multidict: https://multidict.readthedocs.io/

The following commands clone the repository from GitHub_, set up a virtual
environment and install crocoite:

.. _GitHub: https://github.com/PromyLOPh/crocoite

.. code:: bash

    git clone https://github.com/PromyLOPh/crocoite.git
    cd crocoite
    virtualenv -p python3 sandbox
    source sandbox/bin/activate
    pip install .

It is recommended to install at least Micrsoft’s Corefonts_ as well as DejaVu_,
Liberation_ or a similar font family covering a wide range of character sets.
Otherwise page screenshots may be unusable due to missing glyphs.

.. _Corefonts: http://corefonts.sourceforge.net/
.. _DejaVu: https://dejavu-fonts.github.io/
.. _Liberation: https://pagure.io/liberation-fonts

