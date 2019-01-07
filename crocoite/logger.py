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

"""
Simple logger inspired by structlog.

It is usually used like this: Classes are passed a logger instance. They bind
context to their name, so identifying the source of messages is easier. Every
log message carries a unique id (uuid) for automated identification as well as
a short human-readable message (msg) and arbitrary payload.
"""

import sys, json
from datetime import datetime
from functools import partial
from enum import IntEnum

from pytz import utc

from .util import StrJsonEncoder

class Level(IntEnum):
    DEBUG = 0
    INFO = 1
    WARNING = 2
    ERROR = 3

class Logger:
    def __init__ (self, consumer=None, bindings=None):
        self.bindings = bindings or {}
        self.consumer = consumer or []

    def __call__ (self, level, *args, **kwargs):
        if not isinstance (level, Level):
            level = Level[level.upper ()]
        kwargs['level'] = level
        if args:
            if len (args) == 1:
                args, = args
            kwargs['msg'] = args
        # do not overwrite arguments
        for k, v in self.bindings.items ():
            if k not in kwargs:
                kwargs[k] = v
        for c in self.consumer:
            kwargs = c (**kwargs)
        return kwargs

    def __getattr__ (self, k):
        """ Bind all method names to level, so Logger.info, Logger.warning, … work """
        return partial (self.__call__, k)

    def bind (self, **kwargs):
        d = self.bindings.copy ()
        d.update (kwargs)
        # consumer is not a copy intentionally, so attaching to the parent
        # logger will attach to all children as well
        return self.__class__ (consumer=self.consumer, bindings=d)

    def unbind (self, **kwargs):
        d = self.bindings.copy ()
        for k in kwargs.keys ():
            del d[k]
        return self.__class__ (consumer=self.consumer, bindings=d)

    def connect (self, consumer):
        self.consumer.append (consumer)

    def disconnect (self, consumer):
        self.consumer.remove (consumer)

class Consumer:
    def __call__ (self, **kwargs): # pragma: no cover
        raise NotImplementedError ()

class NullConsumer (Consumer):
    def __call__ (self, **kwargs):
        return kwargs

class PrintConsumer (Consumer):
    """
    Simple printing consumer
    """
    def __call__ (self, **kwargs):
        sys.stderr.write (str (kwargs))
        sys.stderr.write ('\n')
        sys.stderr.flush ()
        return kwargs

class JsonPrintConsumer (Consumer):
    def __init__ (self, minLevel=Level.DEBUG):
        self.minLevel = minLevel

    def __call__ (self, **kwargs):
        if kwargs['level'] >= self.minLevel:
            json.dump (kwargs, sys.stdout, cls=StrJsonEncoder)
            sys.stdout.write ('\n')
            sys.stdout.flush ()
        return kwargs

class DatetimeConsumer (Consumer):
    def __call__ (self, **kwargs):
        kwargs['date'] = datetime.utcnow ().replace (tzinfo=utc)
        return kwargs

class WarcHandlerConsumer (Consumer):
    def __init__ (self, warc, minLevel=Level.DEBUG):
        self.warc = warc
        self.minLevel = minLevel

    def __call__ (self, **kwargs):
        if kwargs['level'] >= self.minLevel:
            self.warc._writeLog (json.dumps (kwargs, cls=StrJsonEncoder))
        return kwargs

