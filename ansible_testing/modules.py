#!/usr/bin/env python

from __future__ import print_function

import os
import re
import abc
import ast
import sys
import yaml
import argparse
import traceback

from fnmatch import fnmatch
from distutils.version import StrictVersion

from utils import find_globals

from ansible.plugins import module_loader
from ansible.executor.module_common import REPLACER_WINDOWS
from ansible.utils.module_docs import get_docstring, BLACKLIST_MODULES

from ansible import __version__ as ansible_version
from ansible.module_utils import basic as module_utils_basic

# We only use StringIO, since we cannot setattr on cStringIO
from StringIO import StringIO


BLACKLIST_DIRS = frozenset(('.git',))
INDENT_REGEX = re.compile(r'([\t]*)')
BASIC_RESERVED = frozenset((r for r in dir(module_utils_basic) if r[0] != '_'))


class Validator(object):
    """Validator instances are intended to be run on a single object.  if you
    are scanning multiple objects for problems, you'll want to have a separate
    Validator for each one."""
    __metaclass__ = abc.ABCMeta

    def __init__(self):
        self.reset()

    def reset(self):
        """Reset the test results"""
        self.errors = []
        self.warnings = []
        self.traces = []

    @abc.abstractproperty
    def object_name(self):
        """Name of the object we validated"""
        pass

    @abc.abstractproperty
    def object_path(self):
        """Path of the object we validated"""
        pass

    @abc.abstractmethod
    def validate(self, reset=True):
        """Run this method to generate the test results"""
        if reset:
            self.reset()

    def report(self, warnings=False):
        """Print out the test results"""
        if self.errors or (warnings and self.warnings):
            print('=' * 76)
            print(self.object_path)
            print('=' * 76)

        ret = []

        for trace in self.traces:
            print(trace)
        for error in self.errors:
            print('ERROR: %s' % error)
            ret.append(1)
        if warnings:
            for warning in self.warnings:
                print('WARNING: %s' % warning)
                # ret.append(1)  # Don't incrememt exit status for warnings

        if self.errors or (warnings and self.warnings):
            print()

        return len(ret)


class ModuleValidator(Validator):
    BLACKLIST_PATTERNS = ('.git*', '*.pyc', '*.pyo', '.*', '*.md')
    BLACKLIST_FILES = frozenset(('.git', '.gitignore', '.travis.yml',
                                 '.gitattributes', '.gitmodules', 'COPYING',
                                 '__init__.py', 'VERSION', 'test-docs.sh'))
    BLACKLIST = BLACKLIST_FILES.union(BLACKLIST_MODULES)

    BOTTOM_IMPORTS = frozenset((
        'ansible.module_utils.basic',
        'ansible.module_utils.urls',
        'ansible.module_utils.facts',
        'ansible.module_utils.splitter',
        'ansible.module_utils.known_hosts',
        'ansible.module_utils.rax',
    ))
    BOTTOM_IMPORTS_BLACKLIST = frozenset((
        'command.py',
    ))

    PS_DOC_BLACKLIST = frozenset((
        'slurp.ps1',
        'setup.ps1'
    ))

    def __init__(self, path):
        super(ModuleValidator, self).__init__()

        self.path = path
        self.basename = os.path.basename(self.path)
        self.name, _ = os.path.splitext(self.basename)

        self._python_module_override = False

        with open(path) as f:
            self.text = f.read()
        self.length = len(self.text.splitlines())
        try:
            self.ast = ast.parse(self.text)
        except:
            self.ast = None

    @property
    def object_name(self):
        return self.basename

    @property
    def object_path(self):
        return self.path

    def _python_module(self):
        if self.path.endswith('.py') or self._python_module_override:
            return True
        return False

    def _powershell_module(self):
        if self.path.endswith('.ps1'):
            return True
        return False

    def _just_docs(self):
        try:
            for child in self.ast.body:
                if not isinstance(child, ast.Assign):
                    return False
            return True
        except AttributeError:
            return False

    def _is_bottom_import_blacklisted(self):
        return self.object_name in self.BOTTOM_IMPORTS_BLACKLIST

    def _is_new_module(self):
        return not module_loader.has_plugin(self.name)

    def _check_interpreter(self, powershell=False):
        if powershell:
            if not self.text.startswith('#!powershell\n'):
                self.errors.append('Interpreter line is not "#!powershell"')
            return

        if not self.text.startswith('#!/usr/bin/python'):
            self.errors.append('Interpreter line is not "#!/usr/bin/python"')

    def _check_for_sys_exit(self):
        if 'sys.exit(' in self.text:
            self.errors.append('sys.exit() call found. Should be '
                               'exit_json/fail_json')

    def _check_for_gpl3_header(self):
        if ('GNU General Public License' not in self.text and
                'version 3' not in self.text):
            self.errors.append('GPLv3 license header not found')

    def _check_for_tabs(self):
        for line_no, line in enumerate(self.text.splitlines()):
            indent = INDENT_REGEX.search(line)
            if indent and '\t' in line:
                index = line.index('\t')
                self.errors.append('indentation contains tabs. line %d '
                                   'column %d' % (line_no + 1, index))

    def _find_json_import(self):
        for child in self.ast.body:
            if isinstance(child, ast.Import):
                for name in child.names:
                    if name.name == 'json':
                        self.warnings.append('JSON import found, '
                                             'already provided by '
                                             'ansible.module_utils.basic')

    def _find_requests_import(self):
        for child in self.ast.body:
            if isinstance(child, ast.Import):
                for name in child.names:
                    if name.name == 'requests':
                        self.errors.append('requests import found, '
                                           'should use '
                                           'ansible.module_utils.urls '
                                           'instead')

    def _find_module_utils(self, main):
        linenos = []
        for child in self.ast.body:
            found_module_utils_import = False
            if isinstance(child, ast.ImportFrom):
                if child.module.startswith('ansible.module_utils.'):
                    found_module_utils_import = True

                    if child.module in self.BOTTOM_IMPORTS:
                        if (child.lineno < main - 10 and
                                not self._is_bottom_import_blacklisted()):
                            self.errors.append('%s import not near call to '
                                               'main()' % child.module)

                    linenos.append(child.lineno)

                    if not child.names:
                        self.errors.append('%s: not a "from" import"' %
                                           child.module)

                    found_alias = False
                    for name in child.names:
                        if isinstance(name, ast.alias):
                            found_alias = True
                            if name.asname or name.name != '*':
                                self.errors.append('%s: did not import "*"' %
                                                   child.module)

                if found_module_utils_import and not found_alias:
                    self.errors.append('%s: did not import "*"' % child.module)

        if not linenos:
            self.errors.append('Did not find a module_utils import')

        return linenos

    def _find_main_call(self):
        lineno = False
        if_bodies = []
        for child in self.ast.body:
            if isinstance(child, ast.If):
                try:
                    if child.test.left.id == '__name__':
                        if_bodies.extend(child.body)
                except AttributeError:
                    pass

        bodies = self.ast.body
        bodies.extend(if_bodies)

        for child in bodies:
            if isinstance(child, ast.Expr):
                if isinstance(child.value, ast.Call):
                    if (isinstance(child.value.func, ast.Name) and
                            child.value.func.id == 'main'):
                        lineno = child.lineno
                        if lineno < self.length - 1:
                            self.errors.append('Call to main() not the last '
                                               'line')

        if not lineno:
            self.errors.append('Did not find a call to main')

        return lineno or 0

    def _find_has_import(self):
        for child in self.ast.body:
            found_try_except_import = False
            found_has = False
            if isinstance(child, ast.TryExcept):
                bodies = child.body
                for handler in child.handlers:
                    bodies.extend(handler.body)
                for grandchild in bodies:
                    if isinstance(grandchild, ast.Import):
                        found_try_except_import = True
                    if isinstance(grandchild, ast.Assign):
                        for target in grandchild.targets:
                            if target.id.lower().startswith('has_'):
                                found_has = True
            if found_try_except_import and not found_has:
                self.warnings.append('Found Try/Except block without HAS_ '
                                     'assginment')

    def _find_ps_replacers(self):
        if 'WANT_JSON' not in self.text:
            self.errors.append('WANT_JSON not found in module')

        if REPLACER_WINDOWS not in self.text:
            self.errors.append('"%s" not found in module' % REPLACER_WINDOWS)

    def _find_ps_docs_py_file(self):
        if self.object_name in self.PS_DOC_BLACKLIST:
            return
        py_path = self.path.replace('.ps1', '.py')
        if not os.path.isfile(py_path):
            self.errors.append('Missing python documentation file')

    def _get_docs(self):
        docs = None
        examples = None
        ret = None
        for child in self.ast.body:
            if isinstance(child, ast.Assign):
                for grandchild in child.targets:
                    if grandchild.id == 'DOCUMENTATION':
                        docs = child.value.s
                    elif grandchild.id == 'EXAMPLES':
                        examples = child.value.s[1:]
                    elif grandchild.id == 'RETURN':
                        ret = child.value.s

        return docs, examples, ret

    def _find_redeclarations(self):
        g = set()
        find_globals(g, self.ast.body)
        redeclared = BASIC_RESERVED.intersection(g)
        if redeclared:
            self.warnings.append('Redeclared basic.py variable or '
                                 'function: %s' % ', '.join(redeclared))

    def _check_version_added(self, doc):
        if not self._is_new_module():
            return

        try:
            version_added = StrictVersion(str(doc.get('version_added', '0.0')))
        except ValueError:
            self.errors.append('version_added is not a valid version '
                               'number: %s' % version_added)
            return

        strict_ansible_version = StrictVersion(ansible_version)
        should_be = '.'.join(ansible_version.split('.')[:2])

        if (version_added < strict_ansible_version or
                strict_ansible_version < version_added):
            self.errors.append('version_added should be %s. Currently %s' %
                               (should_be, version_added))

    def validate(self):
        super(ModuleValidator, self).validate()

        # Blacklists -- these files are not checked
        if not frozenset((self.basename,
                          self.name)).isdisjoint(self.BLACKLIST):
            return
        for pat in self.BLACKLIST_PATTERNS:
            if fnmatch(self.basename, pat):
                return

#        if self._powershell_module():
#            self.warnings.append('Cannot check powershell modules at this '
#                                 'time.  Skipping')
#            return
        if not self._python_module() and not self._powershell_module():
            self.errors.append('Official Ansible modules must have a .py '
                               'extension for python modules or a .ps1 '
                               'for powershell modules')
            self._python_module_override = True

        if self._python_module() and self.ast is None:
            self.errors.append('Python SyntaxError while parsing module')
            return

        if self._python_module():
            sys_stdout = sys.stdout
            sys_stderr = sys.stderr
            sys.stdout = sys.stderr = StringIO()
            setattr(sys.stdout, 'encoding', sys_stdout.encoding)
            setattr(sys.stderr, 'encoding', sys_stderr.encoding)
            try:
                doc, examples, ret = get_docstring(self.path, verbose=True)
                trace = None
            except:
                doc = None
                _, examples, ret = self._get_docs()
                trace = traceback.format_exc()
            finally:
                sys.stdout = sys_stdout
                sys.stderr = sys_stderr
            if trace:
                self.traces.append(trace)
            if not bool(doc):
                self.errors.append('Invalid or no DOCUMENTATION provided')
            else:
                self._check_version_added(doc)
            if not bool(examples):
                self.errors.append('No EXAMPLES provided')
            if not bool(ret):
                if self._is_new_module():
                    self.errors.append('No RETURN provided')
                else:
                    self.warnings.append('No RETURN provided')
            else:
                try:
                    yaml.safe_load(ret)
                except:
                    self.errors.append('RETURN is not valid YAML')
                    self.traces.append(traceback.format_exc())

        if self._python_module() and not self._just_docs():
            self._check_for_sys_exit()
            self._find_json_import()
            self._find_requests_import()
            main = self._find_main_call()
            self._find_module_utils(main)
            self._find_has_import()
            self._check_for_tabs()
            self._find_redeclarations()

        if self._powershell_module():
            self._find_ps_replacers()
            self._find_ps_docs_py_file()

        self._check_for_gpl3_header()
        if not self._just_docs():
            self._check_interpreter(powershell=self._powershell_module())


class PythonPackageValidator(Validator):
    def __init__(self, path):
        super(PythonPackageValidator, self).__init__()

        self.path = path
        self.basename = os.path.basename(path)

    @property
    def object_name(self):
        return self.basename

    @property
    def object_path(self):
        return self.path

    def validate(self):
        super(PythonPackageValidator, self).validate()

        init_file = os.path.join(self.path, '__init__.py')
        if not os.path.exists(init_file):
            self.errors.append('Ansible module subdirectories must contain an '
                               '__init__.py')


def re_compile(value):
    """
    Argparse expects things to raise TypeError, re.compile raises an re.error
    exception

    This function is a shorthand to convert the re.error exception to a
    TypeError
    """

    try:
        return re.compile(value)
    except re.error as e:
        raise TypeError(e)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('modules', help='Path to module or module directory')
    parser.add_argument('-w', '--warnings', help='Show warnings',
                        action='store_true')
    parser.add_argument('--exclude', help='RegEx exclusion pattern',
                        type=re_compile)
    args = parser.parse_args()

    args.modules = args.modules.rstrip('/')

    exit = []

    # Allow testing against a single file
    if os.path.isfile(args.modules):
        path = args.modules
        if args.exclude and args.exclude.search(path):
            sys.exit(0)
        mv = ModuleValidator(path)
        mv.validate()
        exit.append(mv.report(args.warnings))
        sys.exit(sum(exit))

    for root, dirs, files in os.walk(args.modules):
        basedir = root[len(args.modules)+1:].split('/', 1)[0]
        if basedir in BLACKLIST_DIRS:
            continue
        for dirname in dirs:
            if root == args.modules and dirname in BLACKLIST_DIRS:
                continue
            path = os.path.join(root, dirname)
            if args.exclude and args.exclude.search(path):
                continue
            pv = PythonPackageValidator(path)
            pv.validate()
            exit.append(pv.report(args.warnings))

        for filename in files:
            path = os.path.join(root, filename)
            if args.exclude and args.exclude.search(path):
                continue
            mv = ModuleValidator(path)
            mv.validate()
            exit.append(mv.report(args.warnings))

    sys.exit(sum(exit))


if __name__ == '__main__':
    main()
