#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Negative controls for tools/check-release-kernel-version.py.

The tool is the gate in front of a release: it says whether the release names
the kernel it ships.  These are the ways it has to say no, and the one way it
has to say yes.

  * a report that names no kernel at all,
  * a report that says the image carries no banner,
  * a report that names a kernel the image does not announce,
  * an image that carries no banner at all,
  * a report that is not a report,
  * a report with no kernel section,
  * and the case that has to pass: a report and an image that agree.

The last one matters as much as the others.  A gate that refuses everything
is not a gate, it is an outage.
"""
import gzip
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import zlib

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))


def load(name):
    path = TOOLS / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load " + str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = load("check-release-kernel-version.py")


def uimage(version=None):
    """A legacy uImage whose kernel announces `version`, if it has one."""
    raw = bytearray(b"\x00" * 4096)
    if version is not None:
        text = ("Linux version %s (builder@host) (aarch64-linux-gcc 13.3.0) "
                "13.3.0, GNU ld 2.42\n" % version).encode()
        raw[1024:1024 + len(text)] = text
    payload = gzip.compress(bytes(raw), mtime=0)
    header = struct.pack(">7I4B32s", 0x27051956, 0, 0, len(payload),
                         0x80200000, 0x80200000, zlib.crc32(payload),
                         5, 22, 2, 1, b"test")
    header = header[:4] + struct.pack(">I", zlib.crc32(header)) + header[8:]
    return header + payload


def report(version, known=True):
    return {"image": "x", "sizes": {},
            "kernel": {"raw_bytes": 4096, "raw_sha256": "0" * 64,
                       "version": version, "version_known": known},
            "metadata": {}, "native_signature_present": False, "layout": {}}


def main():
    checks = 0
    failures = []

    def check(ok, what, detail=""):
        nonlocal checks
        checks += 1
        if ok:
            print("  ok   %s" % what)
        else:
            print("  FAIL %s%s" % (what, (" - " + detail) if detail else ""))
            failures.append(what)

    with tempfile.TemporaryDirectory(prefix="sk-release-kver-") as tmp:
        work = Path(tmp)

        image = work / "flash-uImage"
        image.write_bytes(uimage("7.3.4"))
        good = work / "good.json"
        good.write_text(json.dumps(report("7.3.4")), encoding="utf-8")

        # The case that has to pass.
        try:
            version, announced = gate.check(good, image)
            check(version == "7.3.4" and announced == "7.3.4",
                  "a report and an image that agree are accepted",
                  "%s / %s" % (version, announced))
        except Exception as error:
            check(False, "a report and an image that agree are accepted",
                  repr(error)[:160])

        # A report written before the version_known field existed still says
        # which kernel it ships, so it is still usable.
        old = work / "old.json"
        old.write_text(json.dumps({"kernel": {"version": "7.3.4"}}),
                       encoding="utf-8")
        try:
            version, announced = gate.check(old, image)
            check(version == "7.3.4",
                  "a report without the version_known field is still accepted",
                  str(version))
        except Exception as error:
            check(False, "a report without the version_known field is still accepted",
                  repr(error)[:160])

        # A report that names no kernel.  The reason it is refused for is
        # part of what is checked: a report that says "this image has no
        # banner" and a report that says "this image has a banner but does
        # not say which" are two different faults, and a gate that only
        # knows "refused" cannot tell them apart.
        for what, payload, why in (
                ("a report with no version at all",
                 report(None, known=False),
                 "carries no kernel version banner"),
                ("a report with a null version",
                 report(None, known=True),
                 "does not name a kernel version"),
                ("a report with no kernel section",
                 {"image": "x"},
                 "no kernel section"),
        ):
            path = work / "sysupgrade-report.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            try:
                gate.check(path, image)
                check(False, what + " is refused")
            except ValueError as error:
                check(True, what + " is refused")
                check(why in str(error),
                      what + " is refused for this reason", str(error))

        # A report that names a kernel the image does not announce.
        path = work / "r.json"
        path.write_text(json.dumps(report("6.18.38")), encoding="utf-8")
        try:
            gate.check(path, image)
            check(False, "a report that names another kernel is refused")
        except ValueError as error:
            check(True, "a report that names another kernel is refused")
            check("6.18.38" in str(error) and "7.3.4" in str(error),
                  "the refusal names both versions", str(error))

        # An image with no banner at all.
        blank = work / "blank-uImage"
        blank.write_bytes(uimage(None))
        try:
            gate.check(good, blank)
            check(False, "an image without a banner is refused")
        except ValueError as error:
            check(True, "an image without a banner is refused")
            check("banner" in str(error),
                  "the refusal says the banner is missing", str(error))

        # A report that is not a report.
        broken = work / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        try:
            gate.check(broken, image)
            check(False, "a report that is not JSON is refused")
        except ValueError as error:
            check(True, "a report that is not JSON is refused")

        # A report that is not there.
        try:
            gate.check(work / "missing.json", image)
            check(False, "a missing report is refused")
        except (ValueError, OSError) as error:
            check(True, "a missing report is refused")

        # What the flash gate makes of what the report says.  The gate is
        # tools/preflash.py, and the three answers are not the same thing:
        # a version to compare against, a report that says the image has no
        # banner at all, and an older report that says nothing.
        preflash = load("preflash.py")
        for what, payload, expected in (
                ("a report that names a kernel",
                 report("7.3.4"), ("7.3.4", "version")),
                ("a report that says the image has no banner",
                 report(None, known=False), (None, "no-banner")),
                ("a report from before the version_known field",
                 {"kernel": {"version": "7.3.4"}}, ("7.3.4", "version")),
                ("a report from before the version_known field, with no version",
                 {"kernel": {}}, (None, "absent")),
                ("a report that says the version is known but names none",
                 report(None, known=True), (None, "no-banner")),
        ):
            path = work / "sysupgrade-report.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            got = preflash.release_version(str(work))
            check(got == expected,
                  "the flash gate reads %s as %s" % (what, expected), str(got))

        check(preflash.release_version(str(work / "nowhere")) == (None, "absent"),
              "the flash gate reads a missing report as absent")
        broken = work / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        check(preflash.release_version(str(broken)) == (None, "absent"),
              "the flash gate reads a report that is not JSON as absent")

        # And what the gate then says about the image.
        for state, expect_ok, what in (
                ("version", True, "a matching version passes"),
                ("version", False, "a version that does not match fails"),
                ("no-banner", False, "a report with no banner fails"),
                ("absent", None, "a report that says nothing is only a statement"),
        ):
            preflash.EXPECT_STATE = state
            preflash.EXPECT = "7.3.4" if state == "version" else None
            ver = "7.3.4" if expect_ok is True else "6.18.38"
            ok, _, _ = preflash.release_version_check(ver)
            check(ok is expect_ok, what, str(ok))
        preflash.EXPECT_STATE = "absent"
        preflash.EXPECT = None

    print("%d checks, %d failed" % (checks, len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
