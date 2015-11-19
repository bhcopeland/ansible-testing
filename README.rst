ansible-testing
===============

Python module to help test or validate Ansible, specifically ansible
modules

Installation
------------

This module must be installed alongside the current development
release of Ansible to appropriately test the current developemnt
state of modules.

Current Validations
-------------------

Modules
~~~~~~~

Errors
^^^^^^

#. Interpreter line is not ``#!/usr/bin/python``
#. ``main()`` not at the bottom of the file
#. Module does not include ``from ansible.module_utils.basic import *``
#. ``module_utils`` imports at the top (excluding whitelisted
   ``module_utils``)
#. Invalid ``module_utils`` import
#. Missing ``DOCUMENTATION`` or invalid YAML
#. Missing ``EXAMPLES``
#. Invalid Python Syntax
#. Tabbed indentation
#. Use of ``sys.exit()``
#. Missing GPLv3 license header in module
#. Powershell module missing ``WANT_JSON``
#. Powershell module missing ``REPLACER_WINDOWS``

Warnings
^^^^^^^^

#. Whitelisted ``module_utils`` imports at the top
#. Try/Except ``HAS_`` expression missing
#. Missing ``RETURN``
#. ``import json`` found
#. Module contains duplicate globals from basic.py

Notes
^^^^^

#. ``module_utils`` imports not at bottom may be error or warning
   depending on the import.

Module Directories (Python Packages)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

#. Missing ``__init__.py``
