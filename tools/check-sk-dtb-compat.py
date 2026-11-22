#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that every compatible the board device tree names has a driver in
the kernel that was actually built.

A device tree node whose "compatible" names a driver that was not built is
SKIPPED SILENTLY by the kernel: the board boots, logs nothing wrong, and
simply has no network port.  This is the failure shape the 7.3 line is most
exposed to, because the switch driver, the PON stack and the 9132
programming are all code this repository ports by hand, and because a
module that nothing loads is exactly the mistake this repository already
made once - the NPU driver was "=m", so the image had no NPU in it at all
and no way to load one.

The test is against the built kernel image, not against the sources.  A
compatible string is in the image only when the driver that registers it was
compiled in; a driver built as a module puts its string into the .ko, not
into vmlinux.  So this asks the question the sources cannot answer.

The board's own device tree is read as source, with its includes resolved,
because that is what the kernel compiles the dtb from and it needs no dtc.
A dtb can be passed instead with --dtb, and then it is decompiled first.

Usage:
  check-sk-dtb-compat.py --dts DTS --image IMAGE [--dtb DTB]
"""
import argparse
import gzip
import re
import subprocess
import sys
from pathlib import Path

# The compatibles the board's device tree names that have no driver in the
# 7.3 tree, with the reason each one is allowed to be there.
#
# A driver-less compatible is not harmless in general - the node is probed,
# matches nothing and binds to nothing - so an entry here is a decision, not
# an accident, and the reason says which decision it is.
EXCEPTIONS = {
    "zte,zx279133-rgmii":
        "the read-only probe of the RGMII MAC's register block exists only "
        "in the 6.18 tree; on the 7.3 line this node is deliberately left "
        "without a driver (docs/101 八之五.9: a trade-off, not a gap). The "
        "node is only a register window, so nothing binds to it and nothing "
        "is probed.",
}

# The compatibles this gate is about: this board's own driver family.
#
# Only these are checked, and that is a decision, not a shortcut.  A device
# tree names plenty of strings that are not device compatibles at all -
# "arm,cortex-a53" names a CPU type, which the cpu driver matches with
# cpu_type, and no of_match_table anywhere; requiring a driver string for it
# would be asking for the impossible.  The release line's own gate draws the
# same line, and the family is exactly where the risk is: every driver in it
# is either one this repository ports by hand or one the port depends on.
SUBJECT_RE = re.compile(r"^zte,zx279133-[a-z0-9-]+$")

# A node whose only compatible is a fallback of "syscon" is served by the
# tree's own syscon core, which registers on demand: it has no of_device_id
# table, so neither a node-to-driver table nor a string search can see it.
SYSCON = "syscon"
SYSCON_MARKERS = ("regmap init failed", "__regmap_init_mmio_clk")

COMPAT_RE = re.compile(r'^\s*compatible\s*=\s*((?:"[^"]*"\s*,?\s*)+);')
STATUS_RE = re.compile(r'^\s*status\s*=\s*"([^"]*)"\s*;')
# A node may carry a label ("nppt: ethernet@19000000 {") and may be a
# reference to a node defined elsewhere ("&uart0 {").  Both open a brace, so
# both have to be recognised - and a reference has to be recognised even
# though it has no compatible of its own, or its closing brace pops the
# wrong node off the stack.
NODE_RE = re.compile(
    r'^\s*(?:([A-Za-z0-9_][A-Za-z0-9_-]*):\s*)?'
    r'([A-Za-z0-9_,@.+\-/]+)\s*(?:@[0-9a-fA-F,]+)?\s*\{')
REF_RE = re.compile(r'^\s*&([A-Za-z0-9_]+)\s*\{')
INCLUDE_RE = re.compile(r'^\s*(?:#include\s+|"$|/include/\s*")')
QUOTED_INCLUDE_RE = re.compile(r'^\s*(?:#include\s+|/include/\s*)"([^"]+)"')


def read(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def strip_comments(text):
    """Drop the comments, keep the line structure.

    The board's device tree quotes a piece of the vendor's own tree inside a
    comment block, and one of those quoted lines is a "compatible" - so a
    parser that does not strip comments sees a node that does not exist.
    """
    out = []
    depth = 0
    for line in text.splitlines():
        result = []
        index = 0
        while index < len(line):
            if depth:
                end = line.find("*/", index)
                if end < 0:
                    index = len(line)
                    break
                depth = 0
                index = end + 2
                continue
            start = line.find("/*", index)
            slash = line.find("//", index)
            if slash >= 0 and (start < 0 or slash < start):
                result.append(line[index:slash])
                index = len(line)
                break
            if start >= 0:
                result.append(line[index:start])
                depth = 1
                index = start + 2
                continue
            result.append(line[index:])
            break
        out.append("".join(result))
    return "\n".join(out)


def resolve_includes(path, text, seen=None):
    """The text of a device tree with its quoted includes pulled in."""
    seen = seen if seen is not None else set()
    path = Path(path)
    key = str(path.resolve())
    if key in seen:
        return ""
    seen.add(key)
    pieces = []
    for line in text.splitlines():
        match = QUOTED_INCLUDE_RE.match(line)
        if match:
            name = match.group(1)
            for candidate in (path.parent / name,
                              path.parent.parent / name,
                              path.parent.parent.parent / name):
                if candidate.is_file():
                    pieces.append(resolve_includes(
                        candidate, strip_comments(read(candidate)), seen))
                    break
            else:
                print("    (include %s not found)" % name)
            continue
        pieces.append(line)
    return "\n".join(pieces)


def collect(text):
    """Every node in a device tree that carries a compatible.

    Three things about device tree source are handled here, and each of them
    would have made the gate lie:

      * a node may carry a label ("nppt: ethernet@19000000 {"), and a node
        without its label recognised is not opened at all - so its
        compatible would be attributed to whatever node happened to be open,
        which is a different device;
      * a board tree enables a node with a reference ("&nppt { status =
        "okay"; }"), and that override has to land on the node it names -
        otherwise the node keeps the dtsi's "disabled" and the gate skips it,
        which is a false pass on the one node that matters most;
      * a compatible list can be spread over several lines, and the line
        that carries the last entry is the one that ends the statement.
    """
    lines = text.splitlines()
    nodes = []
    stack = []
    current = None
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        index += 1
        if stripped.startswith("compatible"):
            statement = stripped
            while not statement.endswith(";") and index < len(lines):
                statement += " " + lines[index].strip()
                index += 1
            match = COMPAT_RE.match(statement)
            if match and current is not None:
                current["compatibles"] = [
                    n for n in re.findall(r'"([^"]*)"', match.group(1)) if n]
            continue
        if stripped.startswith("status"):
            match = STATUS_RE.match(stripped)
            if match and current is not None:
                current["status"] = match.group(1)
            continue
        label = None
        name = None
        reference = None
        match = REF_RE.match(line)
        if match:
            reference = match.group(1)
            name = "&" + reference
        else:
            match = NODE_RE.match(line)
            if match and stripped.endswith("{"):
                label = match.group(1)
                name = match.group(2)
        if name is not None:
            current = {"node": name, "label": label, "reference": reference,
                       "compatibles": [], "status": None}
            stack.append(current)
        closed = line.count("}") - line.count("{")
        while closed > 0 and stack:
            nodes.append(stack.pop())
            closed -= 1
        if name is not None:
            current = stack[-1] if stack else None
    while stack:
        nodes.append(stack.pop())

    # The board tree's "&label { ... }" overrides land on the node they name.
    by_label = {}
    for node in nodes:
        if node["label"]:
            by_label.setdefault(node["label"], node)
    for node in nodes:
        if not node["reference"]:
            continue
        target = by_label.get(node["reference"])
        if target is None:
            continue
        # "status" is None when the block did not say anything about it; a
        # block that says "okay" has to overwrite the dtsi's "disabled",
        # which is the whole point of the reference.
        if node["status"] is not None:
            target["status"] = node["status"]
        if node["compatibles"]:
            target["compatibles"] = node["compatibles"]
    return nodes


def image_strings(path):
    """The printable runs of the built kernel, as a set of whole lines."""
    data = Path(path).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    found = set()
    for run in re.findall(rb"[\x20-\x7e]{3,}", data):
        found.add(run.decode("ascii", "replace"))
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dts", type=Path)
    parser.add_argument("--dtb", type=Path)
    parser.add_argument("--image", type=Path, required=True)
    args = parser.parse_args()

    failures = []
    notes = []

    if args.dtb:
        result = subprocess.run(["dtc", "-I", "dtb", "-O", "dts", str(args.dtb)],
                                capture_output=True, text=True)
        if result.returncode != 0:
            print("::error::dtc could not read %s: %s"
                  % (args.dtb, result.stderr.strip()))
            return 1
        text = strip_comments(result.stdout)
        print("device tree: %s (decompiled)" % args.dtb)
    elif args.dts:
        text = resolve_includes(args.dts, strip_comments(read(args.dts)))
        print("device tree: %s" % args.dts)
    else:
        print("::error::either --dts or --dtb is required")
        return 1

    nodes = collect(text)
    if not nodes:
        print("::error::the scan found no node with a compatible - it is "
              "broken, not clean")
        return 1

    strings = image_strings(args.image)
    print("kernel image: %s (%d distinct printable runs)" % (args.image, len(strings)))

    print()
    checked = 0
    for node in nodes:
        compatibles = node["compatibles"]
        if not compatibles:
            continue
        name = node["node"]
        status = node["status"] or "okay"
        primary = compatibles[0]
        if not SUBJECT_RE.match(primary):
            continue
        checked += 1
        fallback = compatibles[1] if len(compatibles) > 1 else None
        if status == "disabled":
            notes.append("%s is disabled - never probed, no driver needed" % name)
            continue
        if fallback is not None:
            if fallback == SYSCON:
                if all(marker in strings for marker in SYSCON_MARKERS):
                    notes.append("%s falls back to 'syscon' and this tree's "
                                 "syscon core is built in" % name)
                else:
                    notes.append("%s falls back to 'syscon', but the syscon "
                                 "core does not look built in - the lookup "
                                 "would fail at probe time" % name)
                continue
            if fallback not in strings:
                failures.append("%s falls back to '%s', which has no driver "
                                "in this kernel - the node is enabled, so it "
                                "will be probed and bind to nothing"
                                % (name, fallback))
            else:
                notes.append("%s falls back to '%s', which does have a driver"
                             % (name, fallback))
            continue
        if primary in strings:
            notes.append("%s: %s has a driver in the built kernel"
                         % (name, primary))
            continue
        if primary in EXCEPTIONS:
            notes.append("%s: %s is excepted - %s" % (name, primary,
                                                       EXCEPTIONS[primary]))
            continue
        failures.append("%s is named by the device tree but no driver for it "
                        "is in the built kernel - the kernel skips such a "
                        "node silently, and the board would boot without it"
                        % primary)

    if not checked:
        print("::error::no zte,zx279133-* compatible was found in the device "
              "tree - the scan is broken, not clean")
        return 1

    for note in notes:
        print("ok   %s" % note)
    for failure in failures:
        print("::error::%s" % failure)

    print()
    print("gate verdict: %d" % (1 if failures else 0))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
