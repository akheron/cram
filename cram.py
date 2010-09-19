#!/usr/bin/env python
"""Functional testing framework for command line applications"""

import difflib
import itertools
import os
import re
import subprocess
import sys
import shutil
import time
import tempfile

_natsub = re.compile(r'\d+').sub
def _natkey(s):
    """Return a key usable for natural sorting.

    >>> [_natkey(s) for s in ('foo', 'foo1', 'foo10')]
    ['foo', 'foo11', 'foo210']
    """
    return _natsub(lambda i: str(len(i.group())) + i.group(), s)

def istest(path):
    """Return whether or not a file is a test.

    >>> [istest(s) for s in ('foo', '.foo.t', 'foo.t')]
    [False, False, True]
    """
    return not path.startswith('.') and path.endswith('.t')

def findtests(paths):
    """Yield tests in paths in naturally sorted order"""
    for p in sorted(paths, key=_natkey):
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                if os.path.basename(root).startswith('.'):
                    continue
                for f in sorted(files, key=_natkey):
                    if istest(f):
                        yield os.path.normpath(os.path.join(root, f))
        elif istest(os.path.basename(p)):
            yield os.path.normpath(p)

def _match(pattern, s):
    """Match pattern or return False if invalid.

    >>> bool(_match('foo.*', 'foobar'))
    True
    >>> _match('***', 'foobar')
    False
    """
    try:
        return re.match(pattern, s)
    except re.error:
        return False

def test(path):
    """Run test at path and run input, output, and diff.

    This returns a 3-tuple containing the following:

        (list of lines in test, same list with actual output, diff)

    diff is a generator that yields the diff between the two lists.
    """
    f = open(path)
    p = subprocess.Popen(['/bin/sh', '-'], bufsize=-1, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         universal_newlines=True,
                         close_fds=os.name == 'posix')
    salt = 'CRAM%s' % time.time()

    expected, after = {}, {}
    refout, postout = [], []

    i = pos = prepos = -1
    for i, line in enumerate(f):
        refout.append(line)
        if line.startswith('  $ '):
            after.setdefault(pos, []).append(line)
            prepos = pos
            pos = i
            p.stdin.write('echo "\\n%s %s $?"\n' % (salt, i))
            p.stdin.write(line[4:])
        elif line.startswith('  > '):
            after.setdefault(prepos, []).append(line)
            p.stdin.write(line[4:])
        elif line.startswith('  '):
            expected.setdefault(pos, []).append(line[2:])
        else:
            after.setdefault(pos, []).append(line)
    p.stdin.write('echo "\\n%s %s $?"\n' % (salt, i + 1))

    pos = -1
    ret = 0
    for i, line in enumerate(p.communicate()[0].splitlines(True)):
        if line.startswith(salt):
            presalt = postout.pop()
            if presalt != '  \n':
                postout.append(presalt[:-1] + '%\n')
            ret = int(line.split()[2])
            if ret != 0:
                postout.append('  [%s]\n' % ret)
            postout += after.pop(pos, [])
            pos = int(line.split()[1])
        else:
            eline = None
            if expected.get(pos):
                eline = expected[pos].pop(0)

            if eline == line:
                postout.append('  ' + line)
            elif eline and _match(eline, line):
                postout.append('  ' + eline)
            else:
                postout.append('  ' + line)
    postout += after.pop(pos, [])

    dpath = os.path.abspath(path)
    diff = difflib.unified_diff(refout, postout, dpath, dpath + '.err')
    for firstline in diff:
        break
    else:
        return refout, postout, []
    return refout, postout, itertools.chain([firstline], diff)

def _prompt(question, answers, auto=None):
    """Write a prompt to stdout and ask for answer in stdin.

    answers should be a string, with each character a single
    answer. An uppercase letter is considered the default answer.

    If an invalid answer is given, this asks again until it gets a
    valid one.

    If auto is set, the question is answered automatically with the
    specified value.
    """
    default = [c for c in answers if c.isupper()]
    while True:
        sys.stdout.write('%s [%s] ' % (question, answers))
        if auto is not None:
            sys.stdout.write(auto + '\n')
            return auto

        answer = sys.stdin.readline().strip().lower()
        if not answer and default:
            return default[0]
        elif answer and answer in answers:
            return answer

def run(paths, verbose=False, interactive=False, cwd=None, basetmp=None,
        keeptmp=False, answer=None):
    """Run tests in paths and yield output.

    If verbose is True, filenames and status information are yielded.

    If cwd is set, os.chdir(cwd) is called after each test is run.

    If basetmp is set, each test is run in a random temporary
    directory inside basetmp.

    If basetmp is set and keeptmp is True, temporary directories are
    preserved after use.

    If interactive is True, a prompt is written to stdout asking if
    changed output should be merged back into the original test. The
    answer is read from stdin.
    """
    if cwd is None:
        cwd = os.getcwd()

    seen = set()
    for path in findtests(paths):
        if path in seen:
            continue
        seen.add(path)

        abspath = os.path.join(cwd, path)
        if verbose:
            yield '%s: ' % path
        if not os.stat(abspath).st_size:
            if verbose:
                yield 'empty\n'
        else:
            if basetmp:
                tmpdir = tempfile.mkdtemp('', os.path.basename(path) + '-',
                                          basetmp)
            try:
                if basetmp:
                    os.chdir(tmpdir)
                refout, postout, diff = test(abspath)
            finally:
                if basetmp:
                    os.chdir(cwd)
                    if not keeptmp:
                        shutil.rmtree(tmpdir)
            if diff:
                if verbose:
                    yield 'failed\n'
                else:
                    yield '\n'
                errpath = abspath + '.err'
                errfile = open(errpath, 'w')
                try:
                    for line in postout:
                        errfile.write(line)
                finally:
                    errfile.close()
                for line in diff:
                    yield line
                if interactive:
                    if _prompt('Accept this change?', 'yN', answer) == 'y':
                        shutil.copy(errpath, abspath)
                        os.remove(errpath)
                        if verbose:
                            yield '%s: merged output\n' % path
            elif verbose:
                yield 'passed\n'
        if not verbose:
            yield '.'

def main(args):
    """Main entry point.

    args should not contain the script name.
    """
    from optparse import OptionParser

    p = OptionParser(usage='cram [OPTIONS] TESTS...')
    p.add_option('-v', '--verbose', action='store_true',
                 help='show filenames and test status')
    p.add_option('-i', '--interactive', action='store_true',
                 help='interactively merge changed test output')
    p.add_option('-y', '--yes', action='store_true',
                 help='answer yes to all questions')
    p.add_option('-n', '--no', action='store_true',
                 help='answer no to all questions')
    p.add_option('-D', '--tmpdir', action='store',
                 default=None, metavar='DIR',
                 help="run tests in DIR")
    p.add_option('--keep-tmpdir', action='store_true',
                 help='keep temporary directories')
    p.add_option('-E', action='store_false',
                 dest='sterilize', default=True,
                 help="don't reset common environment variables")

    opts, paths = p.parse_args(args)
    if opts.yes and opts.no:
        sys.stderr.write('options -y and -n are mutually exclusive\n')
        return 2

    if not paths:
        sys.stdout.write(p.get_usage())
        return 1

    os.environ['RUNDIR'] = os.environ['TESTDIR'] = os.getcwd()
    if opts.tmpdir:
        oldcwd = os.getcwd()
        basetmp = None
        if not os.path.isdir(opts.tmpdir):
            sys.stderr.write('no such directory: %s\n' % opts.tmpdir)
            return 2
        try:
            os.chdir(opts.tmpdir)
        except OSError:
            e = sys.exc_info()[1]
            sys.stderr.write("can't change directory: %s\n" % e.strerror)
            return 2
    else:
        oldcwd = None
        basetmp = os.environ['TESTDIR'] = tempfile.mkdtemp('', 'cramtests-')
        proctmp = os.path.join(basetmp, 'tmp')
        os.mkdir(proctmp)
        for s in ('TMPDIR', 'TEMP', 'TMP'):
            os.environ[s] = proctmp

    if opts.sterilize:
        for s in ('LANG', 'LC_ALL', 'LANGUAGE'):
            os.environ[s] = 'C'
        os.environ['TZ'] = 'GMT'
        os.environ['CDPATH'] = ''
        os.environ['COLUMNS'] = '80'
        os.environ['GREP_OPTIONS'] = ''

    if opts.yes:
        answer = 'y'
    elif opts.no:
        answer = 'n'
    else:
        answer = None

    try:
        for s in run(paths, opts.verbose, opts.interactive, oldcwd,
                     basetmp, opts.keep_tmpdir, answer):
            sys.stdout.write(s)
            sys.stdout.flush()
        if not opts.verbose:
            sys.stdout.write('\n')
    finally:
        if not opts.tmpdir and not opts.keep_tmpdir:
            shutil.rmtree(basetmp)

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
