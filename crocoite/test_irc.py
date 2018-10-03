import pytest
from .irc import ArgparseBot

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

