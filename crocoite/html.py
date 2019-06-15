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
HTML helper
"""

from html5lib.treewalkers.base import TreeWalker
from html5lib.filters.base import Filter
from html5lib import constants

# HTML void tags, see https://html.spec.whatwg.org/multipage/syntax.html#void-elements
voidTags = {'area',
        'base',
        'br',
        'col',
        'embed',
        'hr',
        'img',
        'input',
        'link',
        'meta',
        'param',
        'source',
        'track',
        'wbr'}

# source: https://developer.mozilla.org/en-US/docs/Web/HTML/Global_attributes
eventAttributes = {'onabort',
        'onautocomplete',
        'onautocompleteerror',
        'onblur',
        'oncancel',
        'oncanplay',
        'oncanplaythrough',
        'onchange',
        'onclick',
        'onclose',
        'oncontextmenu',
        'oncuechange',
        'ondblclick',
        'ondrag',
        'ondragend',
        'ondragenter',
        'ondragexit',
        'ondragleave',
        'ondragover',
        'ondragstart',
        'ondrop',
        'ondurationchange',
        'onemptied',
        'onended',
        'onerror',
        'onfocus',
        'oninput',
        'oninvalid',
        'onkeydown',
        'onkeypress',
        'onkeyup',
        'onload',
        'onloadeddata',
        'onloadedmetadata',
        'onloadstart',
        'onmousedown',
        'onmouseenter',
        'onmouseleave',
        'onmousemove',
        'onmouseout',
        'onmouseover',
        'onmouseup',
        'onmousewheel',
        'onpause',
        'onplay',
        'onplaying',
        'onprogress',
        'onratechange',
        'onreset',
        'onresize',
        'onscroll',
        'onseeked',
        'onseeking',
        'onselect',
        'onshow',
        'onsort',
        'onstalled',
        'onsubmit',
        'onsuspend',
        'ontimeupdate',
        'ontoggle',
        'onvolumechange',
        'onwaiting'}

default_namespace = constants.namespaces["html"]

class ChromeTreeWalker (TreeWalker):
    """
    Recursive html5lib TreeWalker for Google Chrome method DOM.getDocument
    """

    def recurse (self, node):
        name = node['nodeName']
        if name.startswith ('#'):
            if name == '#text':
                yield from self.text (node['nodeValue'])
            elif name == '#comment':
                yield self.comment (node['nodeValue'])
            elif name == '#document':
                for child in node.get ('children', []):
                    yield from self.recurse (child)
            elif name == '#cdata-section':
                # html5lib cannot generate cdata, so we’re faking it by using
                # an empty tag
                yield from self.emptyTag (default_namespace,
                        '![CDATA[' + node['nodeValue'] + ']]', {})
            else:
                assert False, (name, node)
        else:
            attributes = node.get ('attributes', [])
            convertedAttr = {}
            for i in range (0, len (attributes), 2):
                convertedAttr[(default_namespace, attributes[i])] = attributes[i+1]

            children = node.get ('children', [])
            if name.lower() in voidTags and not children:
                yield from self.emptyTag (default_namespace, name, convertedAttr)
            else:
                yield self.startTag (default_namespace, name, convertedAttr)
                for child in node.get ('children', []):
                    yield from self.recurse (child)
                yield self.endTag ('', name)

    def __iter__ (self):
        assert self.tree['nodeName'] == '#document'
        return self.recurse (self.tree)

    def split (self):
        """
        Split response returned by DOM.getDocument(pierce=True) into independent documents
        """
        def recurse (node):
            contentDocument = node.get ('contentDocument')
            if contentDocument:
                assert contentDocument['nodeName'] == '#document'
                yield contentDocument
                yield from recurse (contentDocument)

            for child in node.get ('children', []):
                yield from recurse (child)

        if self.tree['nodeName'] == '#document':
            yield self.tree
        yield from recurse (self.tree)

class StripTagFilter (Filter):
    """
    Remove arbitrary tags
    """

    def __init__ (self, source, tags):
        Filter.__init__ (self, source)
        self.tags = set (map (str.lower, tags))

    def __iter__(self):
        delete = 0
        for token in Filter.__iter__(self):
            tokenType = token['type']
            if tokenType in {'StartTag', 'EmptyTag'}:
                if delete > 0 or token['name'].lower () in self.tags:
                    delete += 1
            if delete == 0:
                yield token
            if tokenType == 'EndTag' and delete > 0:
                delete -= 1

class StripAttributeFilter (Filter):
    """
    Remove arbitrary HTML attributes
    """

    def __init__ (self, source, attributes):
        Filter.__init__ (self, source)
        self.attributes = set (map (str.lower, attributes))

    def __iter__(self):
        for token in Filter.__iter__(self):
            data = token.get ('data')
            if data and token['type'] in {'StartTag', 'EmptyTag'}:
                newdata = {}
                for (namespace, k), v in data.items ():
                    if k.lower () not in self.attributes:
                        newdata[(namespace, k)] = v
                token['data'] = newdata
            yield token

