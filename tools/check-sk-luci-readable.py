#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that every file the image serves is one uhttpd will serve.

uhttpd hands a file out only when the file is readable by everyone:

    if (!(pi->stat.st_mode & S_IROTH))
        goto error;              /* -> 403 Forbidden */

That test is on the file's own mode, not on what the process running the
server may read: a file at 0600 is refused even though uhttpd runs as
root and root can read it.  Everything under the document root is served
that way, so one file at 0600 is one page that does not open - and the
browser says

    HTTP error 403 while loading class file "/luci-static/resources/..."

which names the URL and not the reason.

This image shipped one.  The LED trigger

    /www/luci-static/resources/view/system/led-trigger/zx279133-los.js

came out of the package build at 0600 - the only file in the whole rootfs
without the world-read bit - and the LED page died with exactly that
error while every other page opened.  Nothing in the build looked at the
modes of what it shipped, so nothing said so.

So this gate asks the built rootfs and not the source tree.  Git only
knows two modes (644 and 755), so a source-side check cannot see a mode
the package build produced: the file is minified and staged on the way
in, and that is where the mode is decided.

A directory has the same rule with one bit more: uhttpd has to be able
to walk into it, so a directory needs the world-read and world-execute
bits - 0755, not 0700.

Usage:
  check-sk-luci-readable.py --root ROOT
"""
import argparse
import os
import stat
import sys
from pathlib import Path

# The bits uhttpd asks for: readable by others, and for a directory the
# execute bit as well, because the server has to walk into it.
WORLD_READ = 0o004
WORLD_TRAVERSE = 0o005


def served(mode, is_dir=False):
    """Whether uhttpd will hand a file with this mode out.

    The rule is the server's own: file.c asks the file's mode for the
    world-read bit and answers 403 Forbidden when it is not there.  It is
    not a question of what the process running the server may read.
    """
    wanted = WORLD_TRAVERSE if is_dir else WORLD_READ
    return bool(mode & wanted)


def entries(www):
    """Every file and directory under the document root, with its mode.

    The mode is the one of what the server opens, so a symlink is judged
    by its target - which is what uhttpd's own stat() does.
    """
    found = []
    for path in sorted(www.rglob('*')):
        try:
            mode = os.stat(path).st_mode
        except OSError:
            continue
        found.append((path, stat.S_IMODE(mode), stat.S_ISDIR(mode)))
    return found


def check(root):
    """What was checked, and what uhttpd would refuse."""
    www = root / 'www'
    if not www.is_dir():
        return None, ['there is no www/ under ' + str(root) +
                    ': the gate was pointed at something that is not a '
                    'rootfs tree']
    found = entries(www)
    files = [entry for entry in found if not entry[2]]
    if not files:
        return found, ['the document root under ' + str(root) + ' holds no file']
    refused = []
    for path, mode, is_dir in found:
        if not served(mode, is_dir):
            refused.append('%s is mode %04o, and uhttpd answers 403 for it'
                           % (path.relative_to(root).as_posix(), mode))
    return found, refused


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True,
                        help='the built rootfs tree')
    args = parser.parse_args(argv)

    root = Path(args.root)
    found, problems = check(root)
    for problem in problems:
        print('FAIL ' + problem)
    if found is None:
        print('check-sk-luci-readable: the gate found no document root')
        return 1
    files = [entry for entry in found if not entry[2]]
    if problems:
        print('check-sk-luci-readable: %d of %d file(s) under www/ are not '
              'world-readable' % (len(problems), len(files)))
        return 1
    print('ok   every file under www/ is world-readable: %d file(s) checked'
          % len(files))
    print('check-sk-luci-readable: every check passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
