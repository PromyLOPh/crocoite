from setuptools import setup

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
        'warcio',
        'html5lib>=0.999999999',
        'bottom',
        'pytz',
        'websockets',
        'aiohttp',
        'PyYAML',
        'yarl',
        'multidict',
    ],
    extras_require={
        'manhole': ['manhole>=1.6'],
    },
    entry_points={
    'console_scripts': [
            # the main executable
            'crocoite = crocoite.cli:recursive',
            # backend helper
            'crocoite-single = crocoite.cli:single',
            # irc bot and dashboard
            'crocoite-irc = crocoite.cli:irc',
            'crocoite-irc-dashboard = crocoite.cli:dashboard',
            # misc tools
            'crocoite-merge-warc = crocoite.tools:mergeWarcCli',
            'crocoite-extract-screenshot = crocoite.tools:extractScreenshot',
            'crocoite-errata = crocoite.tools:errata',
            ],
    },
    package_data={
            'crocoite': ['data/*'],
    },
    setup_requires=['pytest-runner'],
    tests_require=["pytest", 'pytest-asyncio', 'pytest-cov', 'hypothesis'],
    python_requires='>=3.6',
)
