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
        'pychrome',
        'warcio',
        'html5lib>=0.999999999',
        'bottom',
        'pytz',
    ],
    entry_points={
    'console_scripts': [
            'crocoite-grab = crocoite.cli:single',
            'crocoite-recursive = crocoite.cli:recursive',
            'crocoite-irc = crocoite.cli:irc',
            'crocoite-merge-warc = crocoite.tools:mergeWarc',
            'crocoite-extract-screenshot = crocoite.tools:extractScreenshot',
            ],
    },
    package_data={
            'crocoite': ['data/*'],
    },
    setup_requires=["pytest-runner"],
    tests_require=["pytest"],
)
