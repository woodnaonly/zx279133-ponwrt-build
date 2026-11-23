#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Controls for tools/check-sk-modules-builtin.py.

The gate that says the four zte drivers are built into the kernel used to
read the merged .config, which says the drivers were configured in.  The
kernel's own modules.builtin says the objects were really linked, and
that is the stronger of the two - but it exists in two shapes, and a check
that only knows one of them passes on the wrong file and fails on the
right one.

The first version of that check matched the bare name of the object
against the whole line, so it found nothing in the kernel's own list,
whose lines carry a path:

  kernel/drivers/net/ethernet/zte/npu.ko

and it passed on the copy OpenWrt installs into the image, whose lines
are bare:

  npu.ko

Both shapes are therefore checked here, and the negative control takes
one object out of the list: the check has to name the one that is
missing, not just fail.

    python3 tests/test_sk_modules_builtin.py
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools" / "check-sk-modules-builtin.py"

OBJECTS = ("npu", "zx279133-lan-conduit", "zx279133-switch", "fnoffload")

# The shape the kernel's build writes: every object by its path.  This is
# the one the CI build actually has, and the one the first version of the
# check could not read.
KERNEL_SHAPE = tuple(
    "kernel/drivers/net/ethernet/zte/%s.ko" % o for o in OBJECTS)

# The shape OpenWrt installs into the image: bare names.  This is the one
# the product's rootfs carries.
IMAGE_SHAPE = tuple("%s.ko" % o for o in OBJECTS)

# A couple of objects that are not ours, so a check that matches
# "something that looks like a zte module" cannot pass.
NOISE = ("kernel/drivers/net/mdio/mdio-zx279133.ko",
         "kernel/drivers/gpio/gpio-zx279133.ko")


def run(path):
    """Run the gate and return (returncode, stdout)."""
    proc = subprocess.run([sys.executable, str(GATE), "--list", str(path)],
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    failures = []
    checks = 0
    tmp = Path(tempfile.mkdtemp(prefix="sk-mb-"))

    try:
        # ---- the kernel's own list ------------------------------------
        kernel_list = tmp / "modules.builtin"
        kernel_list.write_text("\n".join(KERNEL_SHAPE + NOISE) + "\n",
                                encoding="utf-8", newline="\n")
        rc, out = run(kernel_list)
        checks += 1
        if rc != 0:
            failures.append("the gate did not pass on the kernel's own list "
                            "(rc=%d):\n%s" % (rc, out))
        if args.verbose:
            print("kernel shape: rc=%d %s" % (rc, out.strip()))

        # ---- the copy in the image -------------------------------------
        image_list = tmp / "image-modules.builtin"
        image_list.write_text("\n".join(IMAGE_SHAPE + NOISE) + "\n",
                              encoding="utf-8", newline="\n")
        rc2, out2 = run(image_list)
        checks += 1
        if rc2 != 0:
            failures.append("the gate did not pass on the image's list "
                            "(rc=%d):\n%s" % (rc2, out2))
        if args.verbose:
            print("image shape: rc=%d %s" % (rc2, out2.strip()))

        # ---- and one object taken out of it ----------------------------
        # The check has to say WHICH one is missing.  A gate that only
        # says "something is wrong" sends the reader hunting.
        short_list = tmp / "short-modules.builtin"
        short_list.write_text("\n".join(KERNEL_SHAPE[:-1]) + "\n",
                              encoding="utf-8", newline="\n")
        rc3, out3 = run(short_list)
        checks += 1
        if rc3 != 1:
            failures.append("a list without one object exited %d, expected 1"
                            % rc3)
        checks += 1
        if "fnoffload" not in out3:
            failures.append("the gate did not name the missing object:\n%s"
                            % out3)
        checks += 1
        if "npu" in out3.split("fnoffload")[0]:
            failures.append("the gate named an object that is there:\n%s"
                            % out3)
        if args.verbose:
            print("one missing: rc=%d %s" % (rc3, out3.strip()))

        # ---- and an empty list ----------------------------------------
        empty_list = tmp / "empty-modules.builtin"
        empty_list.write_text("\n", encoding="utf-8", newline="\n")
        rc4, out4 = run(empty_list)
        checks += 1
        if rc4 != 1:
            failures.append("an empty list exited %d, expected 1" % rc4)
        checks += 1
        for o in OBJECTS:
            if o not in out4:
                failures.append("an empty list did not name %s:\n%s"
                                % (o, out4))
        if args.verbose:
            print("empty: rc=%d %s" % (rc4, out4.strip().replace("\n", " ")))

        # ---- and no list at all ----------------------------------------
        rc5, out5 = run(tmp / "not-there")
        checks += 1
        if rc5 != 1:
            failures.append("a missing list exited %d, expected 1" % rc5)
        if args.verbose:
            print("absent: rc=%d %s" % (rc5, out5.strip()))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        for f in failures:
            print("FAIL: %s" % f)
        print("%d of %d controls failed" % (len(failures), checks))
        return 1
    print("modules.builtin: %d controls, 0 failed" % checks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
