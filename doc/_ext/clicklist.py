"""
Render click.yaml config file into human-readable list of supported sites
"""

import pkg_resources, yaml
from docutils import nodes
from docutils.parsers.rst import Directive
from yarl import URL

class ClickList (Directive):
    def run(self):
        # XXX: do this once only
        fd = pkg_resources.resource_stream ('crocoite', 'data/click.yaml')
        config = list (yaml.safe_load_all (fd))

        l = nodes.definition_list ()
        for site in config:
            urls = set ()
            v = nodes.definition ()
            vl = nodes.bullet_list ()
            v += vl
            for s in site['selector']:
                i = nodes.list_item ()
                i += nodes.paragraph (text=s['description'])
                vl += i
                urls.update (map (lambda x: URL(x).with_path ('/'), s.get ('urls', [])))

            item = nodes.definition_list_item ()
            term = ', '.join (map (lambda x: x.host, urls)) if urls else site['match']
            k = nodes.term (text=term)
            item += k

            item += v
            l += item
        return [l]

def setup(app):
    app.add_directive ("clicklist", ClickList)

    return {
        'version': '0.1',
        'parallel_read_safe': True,
        'parallel_write_safe': True,
    }

