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

import random
from urllib.parse import urlsplit, urlunsplit

def randomString (length=None, chars='abcdefghijklmnopqrstuvwxyz'):
    if length is None:
        length = random.randint (16, 32)
    return ''.join (map (lambda x: random.choice (chars), range (length)))

def packageUrl (path):
    """
    Create URL for package data stored into WARC
    """
    return 'urn:' + __package__ + ':' + path

def getFormattedViewportMetrics (tab):
    layoutMetrics = tab.Page.getLayoutMetrics ()
    # XXX: I’m not entirely sure which one we should use here
    return '{}x{}'.format (layoutMetrics['layoutViewport']['clientWidth'],
                layoutMetrics['layoutViewport']['clientHeight'])

def removeFragment (u):
    """ Remove fragment from url (i.e. #hashvalue) """
    s = urlsplit (u)
    return urlunsplit ((s.scheme, s.netloc, s.path, s.query, ''))

