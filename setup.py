from setuptools import setup

setup(
    name='crocoite',
    version='1.0.0',
    author='Lars-Dominik Braun',
    author_email='lars+crocoite@6xq.net',
    url='https://6xq.net/crocoite/',
    packages=['crocoite'],
    license='LICENSE.txt',
    description='Save website to WARC using Google Chrome.',
    long_description=open('README.rst').read(),
    long_description_content_type='text/x-rst',
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
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'License :: OSI Approved :: MIT License',
        'Operating System :: POSIX',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Topic :: Internet :: WWW/HTTP',
    ],
)
