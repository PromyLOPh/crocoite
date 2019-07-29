# -*- coding: utf-8 -*-
import os, sys

# -- Project information -----------------------------------------------------

project = 'crocoite'
copyright = '2019 crocoite contributors'
author = 'crocoite contributors'

# -- General configuration ---------------------------------------------------

sys.path.append(os.path.abspath("./_ext"))
extensions = [
    'sphinx.ext.viewcode',
    'sphinx.ext.autodoc',
    'clicklist',
]

# Add any paths that contain templates here, relative to this directory.
templates_path = ['_templates']

source_suffix = '.rst'
master_doc = 'index'
language = 'en'
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']
pygments_style = 'tango'

# -- Options for HTML output -------------------------------------------------

html_theme = 'alabaster'
html_theme_options = {
    "description": "Preservation for the modern web",
    "github_user": "PromyLOPh",
    "github_repo": "crocoite",
    "travis_button": True,
    "github_button": True,
    "codecov_button": True,
    "fixed_sidebar": True,
}
#html_static_path = ['_static']
html_sidebars = {
   '**': ['about.html', 'navigation.html', 'searchbox.html'],
}

