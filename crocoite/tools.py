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
Misc tools
"""

import shutil, sys, os, logging, argparse, json
from io import BytesIO

from warcio.archiveiterator import ArchiveIterator
from warcio.warcwriter import WARCWriter
from yarl import URL

from pkg_resources import parse_version, parse_requirements

from .util import getSoftwareInfo, StrJsonEncoder
from .warc import jsonMime, makeContentType

def mergeWarc (files, output):
    # stats
    unique = 0
    revisit = 0
    uniqueLength = 0
    revisitLength = 0

    payloadMap = {}
    writer = WARCWriter (output, gzip=True)

    # Add an additional warcinfo record, describing the transformations. This
    # is not ideal, since
    #   “A ‘warcinfo’ record describes the records that
    #   follow it […] until next ‘warcinfo’”
    #   -- https://iipc.github.io/warc-specifications/specifications/warc-format/warc-1.1/#warcinfo
    # A warcinfo record is expected at the beginning of every file. But it
    # might have written by a different software, so we don’t want to
    # strip/replace that information, but supplement it.
    warcinfo = {
            'software': getSoftwareInfo (),
            'tool': 'crocoite-merge', # not the name of the cli tool
            'parameters': {'inputs': files},
            }
    payload = BytesIO (json.dumps (warcinfo, indent=2).encode ('utf-8'))
    record = writer.create_warc_record ('', 'warcinfo',
            payload=payload,
            warc_headers_dict={'Content-Type': makeContentType (jsonMime, 'utf-8')})
    writer.write_record (record)

    for l in files:
        with open (l, 'rb') as fd:
            for record in ArchiveIterator (fd):
                if record.rec_type in {'resource', 'response'}:
                    headers = record.rec_headers
                    rid = headers.get_header('WARC-Record-ID')
                    csum = headers.get_header('WARC-Payload-Digest')
                    length = int (headers.get_header ('Content-Length'))
                    dup = payloadMap.get (csum, None)
                    if dup is None:
                        payloadMap[csum] = {'uri': headers.get_header('WARC-Target-URI'),
                                'id': rid, 'date': headers.get_header('WARC-Date')}
                        unique += 1
                        uniqueLength += length
                    else:
                        logging.debug (f'Record {rid} is duplicate of {dup["id"]}')
                        # Payload may be identical, but HTTP headers are
                        # (probably) not. Include them.
                        record = writer.create_revisit_record (
                                headers.get_header('WARC-Target-URI'), digest=csum,
                                refers_to_uri=dup['uri'], refers_to_date=dup['date'],
                                http_headers=record.http_headers)
                        record.rec_headers.add_header ('WARC-Truncated', 'length')
                        record.rec_headers.add_header ('WARC-Refers-To', dup['id'])
                        revisit += 1
                        revisitLength += length
                else:
                    unique += 1
                writer.write_record (record)
    json.dump (dict (
            unique=dict (records=unique, bytes=uniqueLength),
            revisit=dict (records=revisit, bytes=revisitLength),
            ratio=dict (
                    records=unique/(unique+revisit),
                    bytes=uniqueLength/(uniqueLength+revisitLength)
                    ),
            ),
            sys.stdout,
            cls=StrJsonEncoder)
    sys.stdout.write ('\n')

def mergeWarcCli():
    parser = argparse.ArgumentParser(description='Merge WARCs, reads filenames from stdin.')
    parser.add_argument('--verbose', '-v', action='store_true')
    parser.add_argument('output', type=argparse.FileType ('wb'), help='Output WARC')

    args = parser.parse_args()
    loglevel = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig (level=loglevel)

    mergeWarc([l.strip() for l in sys.stdin], args.output)

def extractScreenshot ():
    """
    Extract page screenshots from a WARC generated by crocoite into files
    """

    parser = argparse.ArgumentParser(description='Extract screenshots from '
            'WARC, write JSON info to stdout.')
    parser.add_argument('-f', '--force', action='store_true',
            help='Overwrite existing files')
    parser.add_argument('-1', '--one', action='store_true',
            help='Only extract the first screenshot into a file named prefix')
    parser.add_argument('input', type=argparse.FileType ('rb'),
            help='Input WARC')
    parser.add_argument('prefix', help='Output file prefix')

    args = parser.parse_args()

    i = 0
    with args.input:
        for record in ArchiveIterator (args.input):
            headers = record.rec_headers
            if record.rec_type != 'conversion' or \
                    headers['Content-Type'] != 'image/png' or \
                    'X-Crocoite-Screenshot-Y-Offset' not in headers:
                continue

            url = URL (headers.get_header ('WARC-Target-URI'))
            yoff = int (headers.get_header ('X-Crocoite-Screenshot-Y-Offset'))
            outpath = f'{args.prefix}{i:05d}.png' if not args.one else args.prefix
            if args.force or not os.path.exists (outpath):
                json.dump ({'file': outpath, 'url': url, 'yoff': yoff},
                        sys.stdout, cls=StrJsonEncoder)
                sys.stdout.write ('\n')
                with open (outpath, 'wb') as out:
                    shutil.copyfileobj (record.raw_stream, out)
                i += 1
            else:
                print (f'not overwriting {outpath}', file=sys.stderr)

            if args.one:
                break

class Errata:
    __slots__ = ('uuid', 'description', 'url', 'affects')

    def __init__ (self, uuid, description, affects, url=None):
        self.uuid = uuid
        self.description = description
        self.url = url
        # slightly abusing setuptool’s version parsing/matching here
        self.affects = list (parse_requirements(affects))

    def __contains__ (self, pkg):
        """
        Return True if the versions in pkg are affected by this errata

        pkg must be a mapping from project_name to version
        """
        matchedAll = []
        for a in self.affects:
            haveVersion = pkg.get (a.project_name, None)
            matchedAll.append (haveVersion is not None and haveVersion in a)
        return all (matchedAll)

    def __repr__ (self):
        return f'{self.__class__.__name__}({self.uuid!r}, {self.description!r}, {self.affects!r})'

    @property
    def fixable (self):
        return getattr (self, 'applyFix', None) is not None

    def toDict (self):
        return {'uuid': self.uuid,
                'description': self.description,
                'url': self.url,
                'affects': list (map (str, self.affects)),
                'fixable': self.fixable}

class FixableErrata(Errata):
    __slots__ = ('stats')

    def __init__ (self, uuid, description, affects, url=None):
        super().__init__ (uuid, description, affects, url)
        # statistics for fixable erratas
        self.stats = dict (records=dict (fixed=0, processed=0))

    def applyFix (self, record):
        raise NotImplementedError () # pragma: no cover

class ContentTypeErrata (FixableErrata):
    def __init__ (self):
        super().__init__ (
            uuid='552c13dc-56e5-4539-9ad8-184ccae60930',
            description='Content-Type header uses wrong argument name encoding instead of charset.',
            url='https://github.com/PromyLOPh/crocoite/issues/19',
            affects=['crocoite==1.0.0'])

    def applyFix (self, record):
        # XXX: this is ugly. warcio’s write_record replaces any Content-Type
        # header we’re setting with this one. But printing rec_headers shows
        # the header, not .content_type.
        contentType = record.content_type
        if '; encoding=' in contentType:
            contentType = contentType.replace ('; encoding=', '; charset=')
            record.content_type = contentType
            self.stats['records']['fixed'] += 1

        self.stats['records']['processed'] += 1
        return record

bugs = [
    Errata (uuid='34a176b3-ad3d-430f-a082-68087f304572',
            description='Generated by version < 1.0. No erratas are supported for this version.',
            affects=['crocoite<1.0'],
            ),
    ContentTypeErrata (),
    ]

def makeReport (fd):
    alreadyFixed = set ()

    for record in ArchiveIterator (fd):
        if record.rec_type == 'warcinfo':
            try:
                data = json.load (record.raw_stream)
                # errata records precceed everything else and indicate which
                # ones were fixed already
                if data['tool'] == 'crocoite-errata':
                    alreadyFixed.update (data['parameters']['errata'])
                else:
                    haveVersions = dict ([(pkg['projectName'], parse_version(pkg['version'])) for pkg in data['software']['self']])
                    yield from filter (lambda b: haveVersions in b and b.uuid not in alreadyFixed, bugs)
            except json.decoder.JSONDecodeError:
                pass

def errataCheck (args):
    hasErrata = False
    for item in makeReport (args.input):
        json.dump (item.toDict (), sys.stdout)
        sys.stdout.write ('\n')
        sys.stdout.flush ()
        hasErrata = True
    return int (hasErrata)

def errataFix (args):
    errata = args.errata

    with args.input as infd, args.output as outfd:
        writer = WARCWriter (outfd, gzip=True)

        warcinfo = {
                'software': getSoftwareInfo (),
                'tool': 'crocoite-errata', # not the name of the cli tool
                'parameters': {'errata': [errata.uuid]},
                }
        payload = BytesIO (json.dumps (warcinfo, indent=2).encode ('utf-8'))
        record = writer.create_warc_record ('', 'warcinfo',
                payload=payload,
                warc_headers_dict={'Content-Type': makeContentType (jsonMime, 'utf-8')})
        writer.write_record (record)

        for record in ArchiveIterator (infd):
            fixedRecord = errata.applyFix (record)
            writer.write_record (fixedRecord)
    json.dump (errata.stats, sys.stdout)
    sys.stdout.write ('\n')
    sys.stdout.flush ()

def uuidToErrata (uuid, onlyFixable=True):
    try:
        e = next (filter (lambda x: x.uuid == uuid, bugs))
    except StopIteration:
        raise argparse.ArgumentTypeError (f'Errata {uuid} does not exist')
    if not isinstance (e, FixableErrata):
        raise argparse.ArgumentTypeError (f'Errata {uuid} is not fixable')
    return e

def errata ():
    parser = argparse.ArgumentParser(description=f'Show/fix erratas for WARCs generated by {__package__}.')
    parser.add_argument('input', metavar='INPUT', type=argparse.FileType ('rb'), help='Input WARC')

    # XXX: required argument does not work here?!
    subparsers = parser.add_subparsers()

    checkparser = subparsers.add_parser('check', help='Show erratas')
    checkparser.set_defaults (func=errataCheck)

    fixparser = subparsers.add_parser('fix', help='Fix erratas')
    fixparser.add_argument('errata', metavar='UUID', type=uuidToErrata, help='Apply fix for this errata')
    fixparser.add_argument('output', metavar='OUTPUT', type=argparse.FileType ('wb'), help='Output WARC')
    fixparser.set_defaults (func=errataFix)

    args = parser.parse_args()

    if not hasattr (args, 'func'):
        parser.print_usage ()
        parser.exit ()

    return args.func (args)

