import pytest
from .logger import Logger, Consumer, NullConsumer, Level, DatetimeConsumer

@pytest.fixture
def logger ():
    return Logger (consumer=[NullConsumer (), DatetimeConsumer ()])

class QueueConsumer (Consumer):
    def __init__ (self):
        self.data = []

    def __call__ (self, **kwargs):
        self.data.append (kwargs)
        return kwargs

def test_bind (logger):
    # simple bind
    logger = logger.bind (foo='bar')
    ret = logger.debug ()
    assert ret['foo'] == 'bar'

    # additional
    ret = logger.debug (bar='baz')
    assert ret['foo'] == 'bar'
    assert ret['bar'] == 'baz'
    
    # override
    ret = logger.debug (foo='baz')
    assert ret['foo'] == 'baz'

    # unbind
    logger = logger.unbind (foo=None)
    ret = logger.debug ()
    assert 'foo' not in ret

def test_consumer (logger):
    c = QueueConsumer ()
    logger.connect (c)
    ret = logger.debug (foo='bar')
    assert len (c.data) == 1
    assert c.data[0] == ret
    assert ret['foo'] == 'bar'
    c.data = []

    # inheritance
    logger = logger.bind (inherit=1)
    ret = logger.debug (foo='bar')
    assert len (c.data) == 1
    assert c.data[0] == ret
    assert ret['foo'] == 'bar'
    assert ret['inherit'] == 1
    c.data = []

    # removal
    logger.disconnect (c)
    ret = logger.debug (foo='bar')
    assert len (c.data) == 0
    assert ret['foo'] == 'bar'
    assert ret['inherit'] == 1

def test_multiarg (logger):
    # single argument
    ret = logger.debug('maybe', foo='bar')
    assert ret['msg'] == 'maybe'
    assert ret['foo'] == 'bar'

    # multi arguments
    ret = logger.debug('may', 'be', foo='bar')
    assert ret['msg'] == ('may', 'be')
    assert ret['foo'] == 'bar'

def test_call (logger):
    for level in ('debug', Level.DEBUG):
        ret = logger(level, 'arg1', 'arg2', foo='bar')
        assert ret['level'] == Level.DEBUG
        assert ret['msg'] == ('arg1', 'arg2')
        assert ret['foo'] == 'bar'

def test_datetime (logger):
    ret = logger.debug()
    assert 'date' in ret

def test_independence ():
    """ Make sure two instances are completely independent """
    l1 = Logger ()
    c = QueueConsumer ()
    l1.connect (c)
    l2 = Logger ()
    l2.info (nothing='nothing')
    assert not c.data

