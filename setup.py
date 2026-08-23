# Copyright (C) 2026 DiaNur Fx Group, SYD, Australia.

from os import path as op

try:
    from setuptools import setup, find_packages
    _has_setuptools = True
except ImportError:
    from distutils.core import setup

here = op.abspath(op.dirname(__file__))

with open("README.md", "r") as f:
    long_description = f.read()

with open('requirements.txt', 'rt') as fh:
    requirements = fh.read().splitlines()

setup(
    name="trading_engine",
    version="1.0",
    author="Nur",
    author_email="mohammad.nuruzzaman@ausloans.com.au",
    description="Chain of reasoning thoughts: Intelligent Trading Solutions",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/drnuruzzaman/dianur_fx",
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3.12.9",
        "License :: DiaNur License",
        'Development Status :: 1 - Staging/Stable',
        "Operating System :: OS Independent",
    ],
    python_requires='==3.12.9',
    install_requires=requirements
)
