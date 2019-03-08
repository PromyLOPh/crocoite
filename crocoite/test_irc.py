# Copyright (c) 2017–2018 crocoite contributors
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

import pytest
from .irc import ArgparseBot, RefCountEvent, User, NickMode

def test_mode_parse ():
    assert ArgparseBot.parseMode ('+a') == [('+', 'a')]
    assert ArgparseBot.parseMode ('+ab') == [('+', 'a'), ('+', 'b')]
    assert ArgparseBot.parseMode ('+a+b') == [('+', 'a'), ('+', 'b')]
    assert ArgparseBot.parseMode ('-a') == [('-', 'a')]
    assert ArgparseBot.parseMode ('-ab') == [('-', 'a'), ('-', 'b')]
    assert ArgparseBot.parseMode ('-a-b') == [('-', 'a'), ('-', 'b')]
    assert ArgparseBot.parseMode ('+a-b') == [('+', 'a'), ('-', 'b')]
    assert ArgparseBot.parseMode ('-a+b') == [('-', 'a'), ('+', 'b')]
    assert ArgparseBot.parseMode ('-ab+cd') == [('-', 'a'), ('-', 'b'), ('+', 'c'), ('+', 'd')]

@pytest.fixture
def event ():
    return RefCountEvent ()

def test_refcountevent_arm (event):
    event.arm ()
    assert event.event.is_set ()

def test_refcountevent_ctxmgr (event):
    with event:
        assert event.count == 1
        with event:
            assert event.count == 2

def test_refcountevent_arm_with (event):
    with event:
        event.arm ()
        assert not event.event.is_set ()
    assert event.event.is_set ()

def test_nick_mode ():
    a = User.fromName ('a')
    a2 = User.fromName ('a')
    a3 = User.fromName ('+a')
    b = User.fromName ('+b')
    c = User.fromName ('@c')

    # equality is based on name only, not mode
    assert a == a2
    assert a == a3
    assert a != b

    assert a.hasPriv (None) and not a.hasPriv (NickMode.voice) and not a.hasPriv (NickMode.operator)
    assert b.hasPriv (None) and b.hasPriv (NickMode.voice) and not b.hasPriv (NickMode.operator)
    assert c.hasPriv (None) and c.hasPriv (NickMode.voice) and c.hasPriv (NickMode.operator)

