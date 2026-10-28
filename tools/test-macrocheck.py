#!/usr/bin/env python3
"""Check that the macro gate itself is right.

    python3 tools/test-macrocheck.py

The gate is the one thing in this pipeline that can say "no" to a build, so
it has to be right itself - and for four runs it was not, in four
different ways, each of which cost an hour of CI:

  run 124: the gate did not exist, and zx279133-switch.c used a macro it
           could not see.
  run 125: the gate's own include walk recursed until Python gave up.
  run 126: the gate called seventy-one enum members undefined, because it
           only knew about #define, and failed a build over a driver that
           compiles.
  run 226: the gate did not know about the tree's own enums, so
           PHY_INTERFACE_MODE_1000BASE_X - a name the tree does not have,
           the tree spells it PHY_INTERFACE_MODE_1000BASEX - went through
           the gate and killed the build forty minutes into it.

Each of those is a case below.  The fixture is a throw-away tree in a
temporary directory; nothing outside it is touched.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "macrocheck.py")

HEADER = """\
#ifndef FIXTURE_H
#define FIXTURE_H

#define ZX279133_FIXTURE_MACRO 0x1234

enum {
	ZX279133_FIXTURE_ENUM_A,
	ZX279133_FIXTURE_ENUM_B = 7,
	ZX279133_FIXTURE_ENUM_COUNT,
};

#endif
"""

USES_HEADER = """\
#include "fixture.h"

int fixture_uses_header(void)
{
	return ZX279133_FIXTURE_MACRO + ZX279133_FIXTURE_ENUM_A +
	       ZX279133_FIXTURE_ENUM_B + ZX279133_FIXTURE_ENUM_COUNT;
}
"""

USES_NO_HEADER = """\
/* No #include "fixture.h" here: this is run 124's mistake. */
int fixture_uses_no_header(void)
{
	return ZX279133_FIXTURE_MACRO;
}
"""

USES_ENUM_NO_HEADER = """\
/* No #include "fixture.h" here either. */
int fixture_uses_enum_no_header(void)
{
	return ZX279133_FIXTURE_ENUM_A;
}
"""

# A file whose own enum supplies the name, the way zx279133-netdev.c does.
LOCAL_ENUM = """\
enum {
	ZX279133_FIXTURE_LOCAL_A,
	ZX279133_FIXTURE_LOCAL_COUNT,
};

int fixture_local_enum(void)
{
	return ZX279133_FIXTURE_LOCAL_A + ZX279133_FIXTURE_LOCAL_COUNT;
}
"""

# The tree's own PHY interface enum, and a driver file that names its
# members.  Run 226's case: a member that the tree does not have is an
# undefined identifier, and only the compiler can say so.
PHY_HEADER = """\
#ifndef FIXTURE_PHY_H
#define FIXTURE_PHY_H

enum phy_interface_t {
	PHY_INTERFACE_MODE_NA,
	PHY_INTERFACE_MODE_1000BASEX,
	PHY_INTERFACE_MODE_INTERNAL,
};

#endif
"""

USES_PHY_OK = """\
int fixture_uses_phy_ok(void)
{
	return PHY_INTERFACE_MODE_1000BASEX;
}
"""

USES_PHY_BAD = """\
/* The tree spells this one PHY_INTERFACE_MODE_1000BASEX: run 226's mistake. */
int fixture_uses_phy_bad(void)
{
	return PHY_INTERFACE_MODE_1000BASE_X;
}
"""


def build(root):
    drv = os.path.join(root, "drivers", "net", "ethernet", "zte")
    os.makedirs(drv)
    with open(os.path.join(drv, "fixture.h"), "w") as fh:
        fh.write(HEADER)
    with open(os.path.join(drv, "uses-header.c"), "w") as fh:
        fh.write(USES_HEADER)
    with open(os.path.join(drv, "uses-no-header.c"), "w") as fh:
        fh.write(USES_NO_HEADER)
    with open(os.path.join(drv, "uses-enum-no-header.c"), "w") as fh:
        fh.write(USES_ENUM_NO_HEADER)
    with open(os.path.join(drv, "local-enum.c"), "w") as fh:
        fh.write(LOCAL_ENUM)
    inc = os.path.join(root, "include", "linux")
    os.makedirs(inc)
    with open(os.path.join(inc, "phy.h"), "w") as fh:
        fh.write(PHY_HEADER)
    with open(os.path.join(drv, "uses-phy-ok.c"), "w") as fh:
        fh.write(USES_PHY_OK)
    with open(os.path.join(drv, "uses-phy-bad.c"), "w") as fh:
        fh.write(USES_PHY_BAD)
    return drv


def run(root):
    r = subprocess.run([sys.executable, GATE, root], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def main():
    tmp = tempfile.mkdtemp(prefix="macrocheck-selftest-")
    try:
        build(tmp)
        rc, out = run(tmp)
        problems = [l.strip() for l in out.splitlines()
                    if " is used but " in l or " is defined in " in l
                    or " is not a member of " in l]
        names = [l.split(":")[1].strip() for l in problems if ":" in l]

        failures = []

        def want(cond, what):
            if not cond:
                failures.append(what)

        want(rc != 0, "the fixture must fail the gate: two files cannot see "
                      "the header that defines their names")
        want(any("uses-no-header.c" in p and "ZX279133_FIXTURE_MACRO" in p
                 for p in problems),
             "a macro used without including its header must be reported")
        want(any("uses-enum-no-header.c" in p and "ZX279133_FIXTURE_ENUM_A" in p
                 for p in problems),
             "an enum member used without including its header must be reported")
        want(not any("uses-header.c" in p for p in problems),
             "a file that includes the header must be clean")
        want(not any("local-enum.c" in p for p in problems),
             "an enum member defined in the file itself must be clean: "
             "this is run 126's case")
        want(not any("ZX279133_FIXTURE_ENUM" in n and "uses-no-header" in p
                     for p in problems for n in [p.split(":")[1].strip()]),
             "the macro case must not be confused with the enum case")
        want(any("uses-phy-bad.c" in p and "PHY_INTERFACE_MODE_1000BASE_X" in p
                 for p in problems),
             "a member of the tree's own enum that the tree does not have "
             "must be reported: this is run 226's case")
        want(any("PHY_INTERFACE_MODE_1000BASEX" in p and "uses-phy-bad.c" in p
                 for p in problems),
             "the report must name the member the tree does have")
        want(not any("uses-phy-ok.c" in p for p in problems),
             "a member of the tree's own enum that the tree has must be clean")

        for f in failures:
            print("FAIL: %s" % f)
        if failures:
            print("--- the gate said ---")
            print(out)
            return 1
        print("test-macrocheck: the gate is right about macros, about enum "
              "members, and about a name that is not defined at all")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
