#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check the name the ZX279133 LAN conduit netdev is published under.

The driver seeds the name with a bounded copy:

    strscpy(ndev->name, "lan-cpu%d", IFNAMSIZ);

strscpy is a copy, not a formatter, so on its own it would leave the literal
text "lan-cpu%d" in ndev->name.  The name that reaches the userland is decided
later, by the kernel, when the netdev is registered:

    net/core/dev.c:  register_netdevice()
                        -> dev_get_valid_name(net, dev, dev->name)
                        -> dev_prep_valid_name()
                             if (strchr(want_name, '%'))
                                 return __dev_alloc_name(net, want_name, out_name);
                        -> __dev_alloc_name()
                             snprintf(res, IFNAMSIZ, buf, i);

So a name that carries exactly one "%d" is a format string: the kernel scans
the registered netdevs, takes the first free unit id and publishes the
formatted name.  For this board that is "lan-cpu0", which is what the bench
scripts and the runbook read.

This tool resolves the name the way the kernel does and compares the result
with the name the rest of the project expects.  It does not grep for the
format string: the string is parsed and then run through the kernel's own
allocation rules, so a name that would be published differently - or would
make the probe fail outright - turns the gate red.

    python3 tools/check-conduit-netdev-name.py \\
        --source kernel-patches/7.3/390-net-zx279133-lan-conduit.patch
"""
import argparse
import re
from pathlib import Path
import sys

IFNAMSIZ = 16
PAGE_SIZE = 4096
MAX_NETDEVICES = 8 * PAGE_SIZE

# strscpy(ndev->name, "<format>", IFNAMSIZ) - the seed, resolved by the kernel.
COPY_RE = re.compile(
    r"\bstrscpy\s*\(\s*[A-Za-z_][A-Za-z0-9_]*(?:->|\.)name\s*,\s*"
    r'"((?:[^"\\]|\\.)*)"')

# snprintf(ndev->name, IFNAMSIZ, "<format>", <arg>) - the name is spelled out.
FORMAT_RE = re.compile(
    r"\bsnprintf\s*\(\s*[A-Za-z_][A-Za-z0-9_]*(?:->|\.)name\s*,\s*IFNAMSIZ\s*,"
    r'\s*"((?:[^"\\]|\\.)*)"([^)]*)\)')

REGISTER_RE = re.compile(r"\b(?:devm_register_netdev|register_netdevice)\s*\(")


class NameRejected(Exception):
    """The kernel would not publish a name built this way."""


def require(condition, message):
    if not condition:
        raise NameRejected(message)


def dev_valid_name(name):
    """Mirror of net/core/dev.c: dev_valid_name()."""
    if not name:
        return False
    if len(name) >= IFNAMSIZ:
        return False
    if name in (".", ".."):
        return False
    return all(ch not in "/:" and not ch.isspace() for ch in name)


def unit_ids(pattern, taken):
    """The unit ids the kernel would consider in use for this format."""
    parts = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("%d", index):
            parts.append(r"(-?\d+)")
            index += 2
        else:
            parts.append(re.escape(pattern[index]))
            index += 1
    compiled = re.compile("^" + "".join(parts) + "$")
    used = set()
    for name in taken:
        match = compiled.match(name)
        if not match:
            continue
        unit = int(match.group(1))
        if 0 <= unit < MAX_NETDEVICES:
            used.add(unit)
    return used


def format_name(pattern, unit):
    """Mirror of the kernel's snprintf(res, IFNAMSIZ, buf, i)."""
    return (pattern.replace("%d", str(unit))[:IFNAMSIZ - 1])


def resolve_published_name(seed, taken=()):
    """The name the kernel publishes for a netdev seeded with this string.

    Mirrors dev_prep_valid_name() and __dev_alloc_name() from
    net/core/dev.c.  Raises NameRejected when the kernel would refuse the
    name instead of publishing it.
    """
    require(dev_valid_name(seed),
            "dev_valid_name() rejects this name, so registration fails")
    if "%" not in seed:
        require(seed not in taken,
                "the name is already in use, so registration fails with -EEXIST")
        return seed
    marker = seed.find("%")
    require(marker >= 0 and seed[marker + 1:marker + 2] == "d"
            and "%" not in seed[marker + 2:],
            "the kernel only accepts exactly one %d, so registration fails "
            "with -EINVAL")
    used = unit_ids(seed, taken)
    for unit in range(MAX_NETDEVICES):
        if unit not in used:
            return format_name(seed, unit)
    raise NameRejected("no free unit id, so registration fails with -ENFILE")


def split_arguments(text):
    """Split a C argument list on the top level commas."""
    parts = []
    depth = 0
    quoted = False
    current = ""
    for ch in text:
        if quoted:
            current += ch
            if ch == '"':
                quoted = False
            continue
        if ch == '"':
            quoted = True
            current += ch
        elif ch in "([{":
            depth += 1
            current += ch
        elif ch in ")]}":
            depth -= 1
            current += ch
        elif ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current.strip())
    return parts


def unquote(literal):
    """The bytes of a C string literal, with the escapes the driver uses."""
    body = literal[1:-1] if literal.startswith('"') and literal.endswith('"') \
        else literal
    out = []
    index = 0
    while index < len(body):
        ch = body[index]
        if ch == "\\" and index + 1 < len(body):
            index += 1
            nxt = body[index]
            out.append({"n": "\n", "t": "\t", "0": "\0"}.get(nxt, nxt))
        else:
            out.append(ch)
        index += 1
    return "".join(out)


def source_text(path):
    """The driver source, whether it is given as a file or as a patch."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    if not re.search(r"^@@ ", raw, re.MULTILINE):
        return raw, "source"
    lines = []
    for line in raw.splitlines():
        if line.startswith(("---", "+++", "diff ", "index ")):
            continue
        if line.startswith("+"):
            lines.append(line[1:])
        elif line.startswith("-"):
            continue
        elif line.startswith(" "):
            lines.append(line[1:])
    return "\n".join(lines), "patch"


def name_seeds(text):
    """Every way this source gives a netdev its name."""
    seeds = []
    for match in COPY_RE.finditer(text):
        seeds.append(("strscpy", unquote(match.group(1)), None))
    for match in FORMAT_RE.finditer(text):
        pattern = unquote(match.group(1))
        rest = match.group(2).lstrip()
        arguments = split_arguments(rest[1:] if rest.startswith(",") else rest)
        if len(arguments) != 1 or not re.fullmatch(r"-?\d+", arguments[0]):
            raise NameRejected(
                "snprintf() builds the name from arguments this gate cannot "
                "evaluate: " + ", ".join(arguments))
        seeds.append(("snprintf", pattern, int(arguments[0])))
    return seeds


def published_name(text, taken=()):
    """The single name this source publishes, resolved by the kernel's rules."""
    seeds = name_seeds(text)
    require(len(seeds) == 1,
            "expected exactly one place that names the netdev, found "
            + str(len(seeds)))
    kind, pattern, unit = seeds[0]
    require(REGISTER_RE.search(text) is not None,
            "this source never registers a netdev, so no name is published")
    if kind == "snprintf":
        require(pattern.count("%d") == 1,
                "snprintf() must format exactly one %d")
        name = format_name(pattern, unit)
        require(dev_valid_name(name),
                "dev_valid_name() rejects this name, so registration fails")
        require(name not in taken,
                "the name is already in use, so registration fails with -EEXIST")
        return name, pattern
    return resolve_published_name(pattern, taken), pattern


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True,
                        help="the driver source, or the patch that creates it")
    parser.add_argument("--expected", default="lan-cpu0",
                        help="the name userland has to find (default: lan-cpu0)")
    parser.add_argument("--taken", action="append", default=[],
                        help="a name already registered, repeatable")
    args = parser.parse_args(argv)

    if not args.source.is_file():
        print(f"::error::{args.source} does not exist")
        return 1
    text, kind = source_text(args.source)
    try:
        name, pattern = published_name(text, args.taken)
    except NameRejected as exc:
        print(f"::error::the conduit netdev would not be published as "
              f"{args.expected}: {exc}")
        return 1
    print(f"read the driver from the {kind}: {args.source}")
    print(f"the driver seeds the name with {pattern!r}")
    print(f"the kernel publishes it as {name!r}")
    if name != args.expected:
        print(f"::error::userland looks for {args.expected!r} but the conduit "
              f"netdev is published as {name!r}")
        return 1
    print(f"OK: the conduit netdev is published as {name!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
