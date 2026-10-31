#!/usr/bin/env python3
"""Check that the two name gates themselves are right.

    python3 tools/test-check-c-gates.py

A gate that says "no" to a good build costs an hour of CI, and a gate that
says "yes" to a bad one costs the same.  These two gates were written after
run 293, which stopped on two errors of the same kind:

    zx279133-pon.c:127  static int zxpon_set_identity(struct sk_buff *skb,
                       struct genl_info *info)
    zxpon.h:105         void zxpon_set_identity(struct zxpon *z, ...);

    zx279133-pon.c:110  genlmsg_put_reply(msg, info, &zxpon_family, ...)
    zx279133-pon.c:222  static struct genl_family zxpon_family = { ... };

so each of them has a fixture that is the shape that broke the build, and a
fixture that is the shape that must stay quiet - a local of the same name, a
tentative definition above the use, a member of somebody else's struct.

The fixtures are a throw-away tree in a temporary directory; nothing outside
it is touched.
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
COLLISION_GATE = os.path.join(HERE, "check-c-name-collision.py")
USE_BEFORE_DECL_GATE = os.path.join(HERE, "check-c-use-before-decl.py")

# The header that declares the name the .c file is not allowed to take.
HEADER = """\
#ifndef FIXTURE_H
#define FIXTURE_H

void fixture_shared(int a);

#endif
"""

# Run 293's shape: a static function whose name the header has.
COLLISION = """\
#include "fixture.h"

static int fixture_shared(int a)
{
	return a;
}
"""

# The same file with a name of its own: quiet.
CLEAN = """\
#include "fixture.h"

static int fixture_private(int a)
{
	return a;
}

int fixture_call(void)
{
	return fixture_private(1) + fixture_shared(2);
}
"""

# Run 293's other shape: the object is used above its definition.
OBJECT_LATE = """\
static int fixture_bump(void)
{
	return ++fixture_count;
}

static int fixture_count;
"""

# The same object, declared above the use: quiet.
OBJECT_EARLY = """\
static int fixture_count;

static int fixture_bump(void)
{
	return ++fixture_count;
}
"""

# A local of the same name: the compiler knows the name, so it says nothing.
OBJECT_SHADOWED = """\
static int fixture_bump(void)
{
	int fixture_count = 0;

	return ++fixture_count;
}

static int fixture_count;
"""


def build(root):
    drv = os.path.join(root, "kernel-overlays", "drivers", "net", "ethernet", "zte")
    os.makedirs(drv)
    with open(os.path.join(drv, "fixture.h"), "w") as fh:
        fh.write(HEADER)
    for name, text in (("collision.c", COLLISION),
                       ("clean.c", CLEAN),
                       ("object-late.c", OBJECT_LATE),
                       ("object-early.c", OBJECT_EARLY),
                       ("object-shadowed.c", OBJECT_SHADOWED)):
        with open(os.path.join(drv, name), "w") as fh:
            fh.write(text)
    return drv


def run(gate, root):
    r = subprocess.run([sys.executable, gate, root], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def main():
    tmp = tempfile.mkdtemp(prefix="check-c-gates-selftest-")
    failures = []
    try:
        build(tmp)

        rc, out = run(COLLISION_GATE, tmp)
        want = [
            (rc != 0, "the fixture must fail the collision gate"),
            ("collision.c" in out and "fixture_shared" in out,
             "a static function whose name a header declares must be reported"),
            ("clean.c" not in out,
             "a static function with a name of its own must be clean"),
        ]

        rc, out = run(USE_BEFORE_DECL_GATE, tmp)
        want += [
            (rc != 0, "the fixture must fail the use-before-decl gate"),
            ("object-late.c" in out and "fixture_count" in out,
             "an object used above its definition must be reported"),
            ("object-early.c" not in out,
             "an object declared above the use must be clean"),
            ("object-shadowed.c" not in out,
             "a local of the same name must be clean: the compiler knows the "
             "name, and says nothing about it"),
            ("clean.c" not in out,
             "a file whose names are all declared must be clean"),
        ]

        for cond, what in want:
            if not cond:
                failures.append(what)

        if failures:
            for f in failures:
                print("FAIL: %s" % f)
            print("--- the gates said ---")
            print(out)
            return 1
        print("test-check-c-gates: the name gates are right about a name a "
              "header has taken, and about a name used above its declaration")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
