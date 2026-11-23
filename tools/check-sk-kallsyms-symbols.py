#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that the four symbols the trial's judge looks for really exist.

The trial's judge answers "are these four drivers in this kernel" by looking
four symbols up in the kernel's symbol table (/proc/kallsyms).  A symbol that
is not in the table means the driver is not in the kernel - but only if the
symbol would have been in the table if it were.  A static function the
compiler decides to inline into its one caller has no symbol at all, and a
judge that looks for that one reports a healthy driver as missing.

So this checks the four symbols against the tree, and it checks the two
things that make them trustworthy:

  - the symbol is defined in a source file that the driver builds in, and it
    belongs to the object that the matching CONFIG symbol builds;
  - the symbol's address is taken somewhere, so the compiler cannot inline
    the function away and the symbol cannot vanish from the table.

The second one is the whole point.  A symbol that is only ever called can be
inlined; a symbol whose address is in a table cannot.  All four below are
assigned into function-pointer tables.

Usage:
  python3 tools/check-sk-kallsyms-symbols.py --tree <kernel tree>
"""

import argparse
import re
import sys
from pathlib import Path

# (the CONFIG symbol that builds the driver, the symbol the judge looks for)
#
# The CONFIG symbol names the object; the kernel symbol is one function in
# it.  The judge looks for the function, and this gate is what says the
# function is one that will be there if the object is.
CRITERIA = (
    ("ZTE_ZX279133_ETH", "zx279133_lan_netdev_open"),
    ("ZTE_ZX279133_LAN_CONDUIT", "zx279133_lan_conduit_get_stats64"),
    ("ZTE_ZX279133_SWITCH", "zx279133_switch_get_tag_protocol"),
    ("ZTE_ZX279133_FNOFFLOAD", "zx279133_offload_attach"),
)

# Where the drivers live, relative to the kernel tree.
DRIVER_DIR = Path("drivers/net/ethernet/zte")


def read_makefile(path):
    """Read a makefile with its line continuations joined."""
    text = path.read_text(encoding="utf-8", errors="replace")
    text = text.replace("\\\n", " ")
    return text


def objects_by_config(makefile_text):
    """Map each CONFIG symbol to the object files it builds.

    Handles both shapes the zte Makefile uses:

        obj-$(CONFIG_FOO) += foo.o          -> one object
        obj-$(CONFIG_FOO) += bar.o          -> a composite, named below
        bar-y := baz.o qux.o

    """
    out = {}
    # name-y := ...   (the composite's members)
    members = {}
    for m in re.finditer(r"^([\w.-]+)-y\s*:?=\s*(.*)$", makefile_text, re.M):
        members[m.group(1)] = m.group(2).split()

    for m in re.finditer(r"^obj-\$\(CONFIG_([\w]+)\)\s*\+=\s*(.*)$",
                         makefile_text, re.M):
        config = m.group(1)
        objs = m.group(2).split()
        files = []
        for obj in objs:
            stem = obj[:-2] if obj.endswith(".o") else obj
            if stem in members:
                files.extend(members[stem])
            else:
                files.append(obj)
        out[config] = files
    return out


def source_files(config_files):
    """Turn the object list into the source files it is built from."""
    return {f[:-2] + ".c" if f.endswith(".o") else f for f in config_files}


def find_definition(text, symbol):
    """The line number where the symbol is defined, or None.

    A definition is a line that names the symbol and opens its argument
    list.  The type is on the same line or on the one above it.
    """
    pattern = re.compile(r"\b%s\s*\(" % re.escape(symbol))
    for lineno, line in enumerate(text.splitlines(), 1):
        if pattern.search(line):
            return lineno
    return None


def find_address_taken(text, symbol):
    """The line numbers where the symbol's address is taken.

    "=". A function pointer table entry is what makes the function
    impossible to inline away.
    """
    pattern = re.compile(r"=\s*%s\b" % re.escape(symbol))
    return [i for i, line in enumerate(text.splitlines(), 1)
            if pattern.search(line)]


def check(tree):
    """Check the four symbols against the tree.  Returns a list of findings."""
    findings = []
    driver_dir = tree / DRIVER_DIR
    makefile = driver_dir / "Makefile"
    if not makefile.is_file():
        return [("FATAL", "no driver Makefile at %s" % makefile)]

    objects = objects_by_config(read_makefile(makefile))

    for config, symbol in CRITERIA:
        files = objects.get(config)
        if not files:
            findings.append((config, symbol,
                             "the driver Makefile builds nothing for "
                             "CONFIG_%s" % config))
            continue
        sources = source_files(files)

        where = None
        for name in sorted(sources):
            path = driver_dir / name
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if find_definition(text, symbol) is not None:
                where = (name, text)
                break

        if where is None:
            findings.append((config, symbol,
                             "not defined in any of %s"
                             % ", ".join(sorted(sources))))
            continue

        name, text = where
        taken = find_address_taken(text, symbol)
        if not taken:
            findings.append((config, symbol,
                             "defined in %s but never assigned to anything: "
                             "the compiler may inline it away and then it "
                             "is not in the symbol table at all" % name))
            continue

        findings.append((config, symbol,
                         "OK: %s, address taken at line %d"
                         % (name, taken[0])))
    return findings


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tree", type=Path, required=True,
                    help="the kernel tree to check")
    args = ap.parse_args(argv)

    findings = check(args.tree)
    failed = False
    for entry in findings:
        if len(entry) == 2:
            print("FAIL  %s: %s" % entry)
            failed = True
            continue
        config, symbol, detail = entry
        if detail.startswith("OK:"):
            print("PASS  %-28s %-38s %s" % (config, symbol, detail))
        else:
            print("FAIL  %-28s %-38s %s" % (config, symbol, detail))
            failed = True

    if failed:
        print("kallsyms symbols: FAILED")
        return 1
    print("kallsyms symbols: %d symbols, all four are there"
          % len(CRITERIA))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
