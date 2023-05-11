.. These are examples of badges you might want to add to your README:
   please update the URLs accordingly

    .. image:: https://api.cirrus-ci.com/github/<USER>/flexmeasures-client.svg?branch=main
        :alt: Built Status
        :target: https://cirrus-ci.com/github/<USER>/flexmeasures-client
    .. image:: https://readthedocs.org/projects/flexmeasures-client/badge/?version=latest
        :alt: ReadTheDocs
        :target: https://flexmeasures-client.readthedocs.io/en/stable/
    .. image:: https://img.shields.io/coveralls/github/<USER>/flexmeasures-client/main.svg
        :alt: Coveralls
        :target: https://coveralls.io/r/<USER>/flexmeasures-client
    .. image:: https://img.shields.io/pypi/v/flexmeasures-client.svg
        :alt: PyPI-Server
        :target: https://pypi.org/project/flexmeasures-client/
    .. image:: https://img.shields.io/conda/vn/conda-forge/flexmeasures-client.svg
        :alt: Conda-Forge
        :target: https://anaconda.org/conda-forge/flexmeasures-client
    .. image:: https://pepy.tech/badge/flexmeasures-client/month
        :alt: Monthly Downloads
        :target: https://pepy.tech/project/flexmeasures-client
    .. image:: https://img.shields.io/twitter/url/http/shields.io.svg?style=social&label=Twitter
        :alt: Twitter
        :target: https://twitter.com/flexmeasures-client

.. image:: https://img.shields.io/badge/-PyScaffold-005CA0?logo=pyscaffold
    :alt: Project generated with PyScaffold
    :target: https://pyscaffold.org/
.. image::https://img.shields.io/badge/python-3.9+-blue.svg
    :target: https://www.python.org/downloads/

|

===================
FlexMeasures Client
===================


The FlexMeasures Client provides a python package to connect to a `FlexMeasures <https://github.com/FlexMeasures/flexmeasures>`_ server to manage flexible assets.

The Flexmeasures Client package provides functionality for authentication, posting sensor data, triggering schedules and retrieving schedules from a FlexMeasures instance through the API.  


Getting Started
===============

Install using ``pip``::

    pip install flexmeasures-client

Initialize client::

    from client import FlexMeasuresClient
    client = FlexMeasuresClient(email="email@email.com", password="pw"

Retrieve available assets::

    await fm.get_assets()




.. _pyscaffold-notes:

Making Changes & Contributing
=============================

This project uses `pre-commit`_, please make sure to install it before making any
changes::

    pip install pre-commit
    cd flexmeasures-client
    pre-commit install

It is a good idea to update the hooks to the latest version::

    pre-commit autoupdate

Don't forget to tell your contributors to also install and use pre-commit.

.. _pre-commit: https://pre-commit.com/

Note
====

This project has been set up using PyScaffold 4.4. For details and usage
information on PyScaffold see https://pyscaffold.org/.
