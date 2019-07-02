# Copyright (c) 2017 crocoite contributors
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

"""
Random utility functions
"""

import random, sys, platform, os, json, urllib
from datetime import datetime
import hashlib, pkg_resources

from yarl import URL

class StrJsonEncoder (json.JSONEncoder):
    """ JSON encoder that turns unknown classes into a string and thus never
    fails """
    def default (self, obj):
        if isinstance (obj, datetime):
            return obj.isoformat ()

        # make sure serialization always succeeds
        try:
            return json.JSONEncoder.default(self, obj)
        except TypeError:
            return str (obj)

async def getFormattedViewportMetrics (tab):
    layoutMetrics = await tab.Page.getLayoutMetrics ()
    # XXX: I’m not entirely sure which one we should use here
    viewport = layoutMetrics['layoutViewport']
    return f"{viewport['clientWidth']}x{viewport['clientHeight']}"

def getSoftwareInfo ():
    """ Get software info for inclusion into warcinfo """
    return {
            'platform': platform.platform (),
            'python': {
                'implementation': platform.python_implementation(),
                'version': platform.python_version (),
                'build': platform.python_build ()
                },
            'self': getRequirements (__package__)
        }

def getRequirements (dist):
    """ Get dependencies of a package.

    Figure out packages’ dependencies based on setup/distutils, then look at
    modules loaded and compute hashes of each loaded dependency.

    This does not and cannot protect against malicious people. It’s only
    purpose is to recreate this exact environment.
    """

    pending = {dist}
    have = set ()
    packages = []
    while pending:
        d = pkg_resources.get_distribution (pending.pop ())

        modules = list (filter (lambda x: x, d.get_metadata ('top_level.txt').split ('\n')))
        modhashes = {}
        # hash loaded modules
        for m in sys.modules.values ():
            f = getattr (m, '__file__', None)
            pkg = getattr (m, '__package__', None)
            # is loaded?
            if pkg in modules:
                if f and os.path.isfile (f):
                    with open (f, 'rb') as fd:
                        contents = fd.read ()
                        h = hashlib.new ('sha512')
                        h.update (contents)
                        modhashes[m.__name__] = {'sha512': h.hexdigest (), 'len': len (contents)}
                else:
                    modhashes[m.__name__] = {}

        # only if one of the packages’ modules is actually loaded
        if modhashes:
            packages.append ({'projectName': d.project_name, 'modules': modhashes, 'version': d.version})

        have.add (dist)
        pending.update (d.requires ())
        pending.difference_update (have)
    return packages

