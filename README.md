# ansible-testing
Python module to help test Ansible, specifically ansible modules

## Current Tests

### Modules

1. Interpreter line is `#!/usr/bin/python`
1. `main()` at the bottom of the file
1. Module includes `from ansible.module_utils` imports
1. `module_utils` imports at the bottom (error/warning)
1. Invalid `module_utils` import
1. Try/Except `HAS_` expression (warning)
1. Has `DOCUMENTATION` and is valid YAML
1. Has `EXAMPLES`
1. Has `RETURN` (warning)
1. `import json` found (warning)
1. Python syntax error
1. Module calls `sys.exit()`
1. Missing GPLv3 license header in module

#### Notes

1. `module_utils` imports not at bottom may be error or warning depending on the import

### Module Directories (Python Packages)

1. Missing `__init__.py`
