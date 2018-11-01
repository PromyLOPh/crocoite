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

import pytest, html5lib
from html5lib.serializer import HTMLSerializer
from html5lib.treewalkers import getTreeWalker

from .html import StripTagFilter, StripAttributeFilter

def test_strip_tag ():
    d = html5lib.parse ('<a>barbaz<b>foobar</b>.</a><b>foobar</b>.<b attr=1><c></c>')
    stream = StripTagFilter (getTreeWalker ('etree')(d), ['b', 'c'])
    serializer = HTMLSerializer ()
    assert serializer.render (stream) == '<a>barbaz.</a>.'

def test_strip_attribute ():
    d = html5lib.parse ('<a b=1 c="yes" d></a><br b=2 c="no" d keep=1>')
    stream = StripAttributeFilter (getTreeWalker ('etree')(d), ['b', 'c', 'd'])
    serializer = HTMLSerializer ()
    assert serializer.render (stream) == '<a></a><br keep=1>'

