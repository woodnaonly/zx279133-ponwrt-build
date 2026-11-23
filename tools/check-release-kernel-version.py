#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that a release names the kernel it ships, and that the name is right.

The gate in front of a release has to know which kernel the release ships, so
that a stale image cannot be flashed under a fresh release's name.  Writing
that version into the gate is what made the gate refuse the other line's
image, so it is read from the release's own report instead - and a report
that cannot name its kernel is no use for that.

Two things are checked here, and the second one is the one that matters:

  * the report names a kernel version at all, and
  * the version it names is the version the image itself announces.

The second check reads the banner out of the image and compares it with the
report.  A report that says "7.3.4" about an image that says "6.18.38" is
worse than a report that says nothing at all, because it gets believed.  So
the comparison is made here, between two files on disk, and not by the tool
that wrote the report in the first place.

Usage:
  check-release-kernel-version.py --report REPORT --image IMAGE
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))


def load_sysupgrade():
    """Import check_sysupgrade.py the way it lives on disk.

    It is a script with a main() and a sys.exit at the bottom, so it is
    loaded by path rather than imported by name.
    """
    path = TOOLS / "check_sysupgrade.py"
    spec = importlib.util.spec_from_file_location("check_sysupgrade", path)
    if spec is None or spec.loader is None:
        raise ValueError("cannot load " + str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require(condition, message):
    if not condition:
        raise ValueError(message)


def report_version(report):
    """The kernel version the report names, and whether it names one."""
    kernel = report.get("kernel")
    require(isinstance(kernel, dict), "the report has no kernel section")
    version = kernel.get("version")
    known = kernel.get("version_known")
    if known is None:
        # A report written before the field existed.  It says nothing about
        # the kernel either way, and that is what it is taken to mean.
        known = bool(version)
    return version, bool(known)


def image_version(image, sysupgrade):
    """The version the image announces, read out of its own kernel."""
    data = Path(image).read_bytes()
    kernel = sysupgrade.inspect_kernel(data)
    return kernel["version"]


def check(report_path, image_path):
    sysupgrade = load_sysupgrade()
    try:
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("cannot read the report: " + repr(error)[:120])
    version, known = report_version(report)
    require(known, "the report says the image carries no kernel version banner")
    require(bool(version), "the report does not name a kernel version")
    announced = image_version(image_path, sysupgrade)
    require(bool(announced),
            "the image carries no kernel version banner: " + str(image_path))
    require(announced == version,
            "the report names kernel %s but the image announces %s"
            % (version, announced))
    return version, announced


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    args = parser.parse_args()
    try:
        version, announced = check(args.report, args.image)
    except (ValueError, OSError) as error:
        parser.exit(1, "release kernel version: %s\n" % error)
    print("the release ships kernel %s and the image announces it" % version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
