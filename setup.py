from distutils.core import setup

setup(
    name='crocoite',
    version='0.1.0',
    author='Lars-Dominik Braun',
    author_email='lars+crocoite@6xq.net',
    packages=['crocoite'],
    license='LICENSE.txt',
    description='Save website to WARC using Google Chrome.',
    long_description=open('README.rst').read(),
    install_requires=[
        'pychrome',
        'warcio',
        'html5lib>=0.999999999',
        'Celery',
    ],
    entry_points={
    'console_scripts': [
            'crocoite-standalone = crocoite.cli:main',
            ],
    },
    package_data={
            'crocoite': ['data/*'],
    },
)
