#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that the tree has a way back from a datapath that could not be
brought up.

A datapath that fails is marked failed, and the board has to have a way
back from that.  The 6.18 generation had one: the conduit's open brought
the datapath back.  The tree without it has two, and both are worse -
the WAN and the LAN service restore a failed datapath only when a
second user is already there, so a board with one interface up has no
way back at all.

What makes this worth a gate is the order.  A datapath that failed
leaves the hardware unprepared: the suspend unprepares it, and a
failed resume unprepares it again.  So a check for the hardware that
sits *before* the restore refuses the open before any restore could
run, and the restore is never reached at all.  The gate therefore asks
about the order of the two, not about their presence.

It also asks where the restore is reached from, because a restore that
nothing calls is the same as no restore at all: DSA opens the conduit
when a user port comes up, and that is the path an operator uses.

Usage:
  check-sk-datapath-restore.py --tree TREE
"""
import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

ZTE = "drivers/net/ethernet/zte"
NETDEV = "zx279133-netdev.c"
DSA_USER = "net/dsa/user.c"

OPEN = "zx279133_lan_netdev_open"
RESTORE = "zx279133_datapath_restore_failed_locked"
OPS = "zx279133_lan_service_ops"


class Report:
    def __init__(self):
        self.failures = []
        self.checks = 0
        self.results = []

    def require(self, ok, message, detail=""):
        self.checks += 1
        self.results.append((ok, message, detail))
        print(f"  [{'OK ' if ok else 'FAIL'}] {message}"
              + (f" - {detail}" if detail and not ok else ""))
        if not ok:
            self.failures.append(message)
        return ok


def tree_fingerprint(tree, names):
    """What the files this gate read amount to."""
    out = []
    for name in names:
        path = Path(tree) / name
        if not path.is_file():
            out.append((name, "missing", 0, 0))
            continue
        data = path.read_bytes()
        out.append((name, hashlib.sha256(data).hexdigest(),
                    len(data), data.count(b"\n")))
    return out


def write_summary(report, title, files=None):
    """Put the verdict where the run page shows it."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [f"### {title}", ""]
    for ok, message, detail in report.results:
        mark = "PASS" if ok else "FAIL"
        lines.append(f"- **{mark}** {message}")
        if detail and not ok:
            lines.append(f"  - {detail}")
    lines.append("")
    lines.append(f"{report.checks} check(s), {len(report.failures)} failed.")
    lines.append("")
    if files:
        lines.append("The files this gate read:")
        lines.append("")
        for name, digest, size, lines_in_file in files:
            lines.append(f"- `{name}`: {digest} ({size} bytes, {lines_in_file} lines)")
        lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def function_body(text, name):
    """The body of a function definition, braces balanced."""
    for match in re.finditer(r"\b" + re.escape(name) + r"\s*\([^;]*?\)\s*\{",
                             text, re.S):
        prefix = text[max(0, match.start() - 300):match.start()]
        cut = max(prefix.rfind(";"), prefix.rfind("{"), prefix.rfind("}"))
        head = prefix[cut + 1:][-80:]
        if not re.search(r"(?:static|inline|void|bool|int|unsigned|signed|u\d+|"
                         r"s\d+|char|long|short|size_t|const\s+struct\s+\w+|"
                         r"struct\s+\w+|enum\s+\w+)\s*\*?\s*$", head):
            continue
        start = match.end() - 1
        depth = 0
        for index in range(start, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    return text[start + 1:index]
    return None


def check(tree, report):
    netdev = Path(tree) / ZTE / NETDEV
    dsa_user = Path(tree) / DSA_USER
    if not report.require(netdev.is_file(), f"the tree has {NETDEV}"):
        return
    if not report.require(dsa_user.is_file(), f"the tree has {DSA_USER}"):
        return
    text = netdev.read_text(encoding="utf-8", errors="replace")
    dsa = dsa_user.read_text(encoding="utf-8", errors="replace")

    body = function_body(text, OPEN)
    if not report.require(body is not None,
                           f"the tree has {OPEN}()"):
        return

    # 1. The restore is called at all.
    call = re.search(r"\b" + RESTORE + r"\s*\(", body)
    report.require(call is not None,
                   f"{OPEN}() restores a failed datapath")
    if call is None:
        return

    # 2. It is called under the datapath lock, which the helper requires.
    lock = body.find("mutex_lock(&eth->datapath_lock)")
    unlock = body.find("mutex_unlock(&eth->datapath_lock)")
    report.require(lock != -1 and unlock != -1 and lock < call.start() < unlock,
                   f"{OPEN}() restores under the datapath lock",
                   f"lock at {lock}, the call at {call.start()}, "
                   f"unlock at {unlock}")

    # 3. The order that matters: the restore has to come before the check
    #    for the hardware, because a datapath that failed leaves the
    #    hardware unprepared.
    hardware = body.find("hardware_prepared")
    report.require(hardware != -1,
                   f"{OPEN}() asks about the hardware",
                   "the tree's check for it is gone")
    if hardware != -1:
        report.require(call.start() < hardware,
                       "the restore comes before the hardware check",
                       f"the call is at {call.start()}, the check at {hardware}")

    # 4. A failed restore is not swallowed: the open fails.
    after = body[call.end():]
    report.require(re.search(r"if\s*\(\s*ret\s*\)\s*\n\s*goto\s+out_unlock;",
                             after) is not None,
                   "a failed restore fails the open",
                   "the open would go on as if nothing had failed")

    # 5. The restore is a no-op while the datapath is healthy.
    helper = function_body(text, RESTORE)
    if not report.require(helper is not None,
                          f"the tree has {RESTORE}()"):
        return
    report.require(
        re.search(r"if\s*\(\s*!\w*->datapath_failed\s*\|\|\s*!\w*->datapath_users\s*\)"
                  r"\s*\n?\s*return 0;", helper) is not None,
        "the restore returns at once while the datapath is healthy",
        "a working board would pay for the restore")

    # 6. The restore is the service's netdev_open, and the conduit is the
    #    netdev that op belongs to.
    ops = re.search(r"const struct " + OPS + r"\s+\w+\s*=\s*\{", text)
    report.require(ops is not None, f"the tree has the {OPS} table")
    if ops is not None:
        report.require(re.search(r"\.netdev_open\s*=\s*" + OPEN, text) is not None,
                       f"the service's netdev_open is {OPEN}()")
    report.require(re.search(r"static int\s*\n?" + OPEN, text) is not None,
                   f"{OPEN}() is a static function of the driver")

    # 7. Something calls it: the conduit's own open.
    conduit = Path(tree) / ZTE / "zx279133-lan-conduit.c"
    if report.require(conduit.is_file(),
                      "the tree has the LAN conduit driver"):
        ctext = conduit.read_text(encoding="utf-8", errors="replace")
        report.require("zx279133_lan_service_netdev_open" in ctext,
                       "the conduit's open goes through the service",
                       "nothing reaches the restore")

    # 8. The path an operator uses: DSA opens the conduit when a user port
    #    comes up.  The check is on dsa_user_open() itself, not on the
    #    file: the tree opens a conduit in more than one place, and only
    #    the one the user port's open does is the path an operator takes.
    dsa_open = function_body(dsa, "dsa_user_open")
    if not report.require(dsa_open is not None,
                          "the tree has dsa_user_open()"):
        return
    report.require(re.search(r"dev_open\(\s*conduit\s*,", dsa_open) is not None,
                   "DSA opens the conduit when a user port comes up",
                   "the restore is not on the path an operator takes")
    report.require(re.search(r"ndo_open\s*=\s*dsa_user_open", dsa) is not None,
                   "the DSA user port's open is dsa_user_open()")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, required=True,
                        help="the prepared kernel tree to check")
    args = parser.parse_args()

    report = Report()
    check(args.tree, report)
    files = tree_fingerprint(args.tree, [f"{ZTE}/{NETDEV}", DSA_USER])
    write_summary(report, "The way back from a datapath that failed",
                  files)
    if report.failures:
        print(f"\nFAILED: {len(report.failures)} of {report.checks} check(s)")
        for item in report.failures:
            print(f"  - {item}")
        return 1
    print(f"\nOK: {report.checks} check(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
