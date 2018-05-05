#!/usr/bin/env python3

# Copyright (c) 2018 crocoite contributors
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
Merge multiple WARC files into a single file, writing revisit records for items
which occur multiple times
"""

import shutil, sys, re, os, logging
from warcio.archiveiterator import ArchiveIterator
from warcio.warcwriter import WARCWriter

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Merge WARCs, reads filenames from stdin.')
    parser.add_argument('--verbose', '-v', action='store_true')
    parser.add_argument('output', type=argparse.FileType ('wb'), help='Output WARC')

    args = parser.parse_args()
    loglevel = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig (level=loglevel)

    unique = 0
    revisit = 0
    payloadMap = {}
    writer = WARCWriter (args.output, gzip=True)
    for l in sys.stdin:
        l = l.strip ()
        with open (l, 'rb') as fd:
            for record in ArchiveIterator (fd):
                if record.rec_type in {'resource', 'response'}:
                    headers = record.rec_headers
                    rid = headers.get_header('WARC-Record-ID')
                    csum = headers.get_header('WARC-Payload-Digest')
                    dup = payloadMap.get (csum, None)
                    if dup is None:
                        payloadMap[csum] = {'uri': headers.get_header('WARC-Target-URI'),
                                'id': rid, 'date': headers.get_header('WARC-Date')}
                        unique += 1
                    else:
                        logging.debug ('Record {} is duplicate of {}'.format (rid, dup['id']))
                        record = writer.create_revisit_record (dup['uri'], csum, dup['uri'], dup['date'])
                        record.rec_headers.add_header ('WARC-Truncated', 'length')
                        record.rec_headers.add_header ('WARC-Refers-To', dup['id'])
                        revisit += 1
                else:
                    unique += 1
                writer.write_record (record)
    logging.info ('Wrote {} unique records, {} revisits'.format (unique, revisit))

