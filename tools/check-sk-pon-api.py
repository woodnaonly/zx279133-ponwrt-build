#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that the ported PON / OMCI stack fits the Linux 7.3 tree.

None of the driver can be compiled on the machine that pushes this
repository: there is no aarch64 cross toolchain there, and the build only
happens in CI, seventy minutes into a job.  So the things a compiler would
say about it are checked here instead, against the tree the build will
actually use.

What is checked:

  * The five files the stack brings are in the tree, and they are the ones
    this repository carries.
  * The protocol core is closed: every function zxpon.h declares is defined
    in zxpon.c, and every function zxpon.c defines is declared in zxpon.h.
    The same file is compiled into the userspace daemon, so a name that
    exists on one side only is a link error there, not here.
  * Every kernel function the stack calls is reachable: the header that
    defines it has to be in the file's own include closure.  A header that
    defines a name is not enough - the file has to include it, directly or
    through another header, or the compiler cannot see the name.
  * The kernel-side contract holds: the tree's zx279133.h declares the
    seven functions the two kernel files define, and the tree's eth
    structure carries the fields they use.
  * The four hooks are wired: the attach and the stop in main.c, the
    carrier kick in mac.c, and the management queue's prepare in idm.c.
  * The build wiring is there: the three objects are in the NPU module.
  * The dependencies the new code creates are met: the LED class and the
    LED triggers have to be on, because the driver registers a trigger and
    publishes the loss-of-signal state through it, and the board device
    tree has to name the trigger and the optical signal detect line.

Usage:
  check-sk-pon-api.py --tree TREE [--repo REPO]
"""
import argparse
import hashlib
import re
import sys
from collections import deque
from pathlib import Path

ZTE = "drivers/net/ethernet/zte"

# The files the 7.3 line's driver tree brings into the kernel.
STACK_FILES = [
    "zxpon.h",
    "zxpon.c",
    "zx279133-omci-wire.h",
    "zx279133-omci.c",
    "zx279133-pon.c",
]

# The functions the two kernel files define and the tree has to declare.
KERNEL_FUNCTIONS = [
    "zx279133_pon_note_rxsd",
    "zx279133_pon_carrier_kick",
    "zx279133_pon_attach",
    "zx279133_pon_stop",
    "zx279133_omci_reclaim",
    "zx279133_omci_prepare",
    "zx279133_omci_send",
]

# The fields the two kernel files use in struct zx279133_eth.
ETH_FIELDS = [
    "omci_buffer",
    "omci_dma",
    "omci_done",
    "omci_producer",
    "omci_pending",
    "omci_submitted",
    "omci_completed",
    "omci_error",
    "pon_led_work",
    "opsd_gpio",
    "los_trigger",
]

# The kernel names each file calls, and the header that has to define them.
# The second element is the header the call has to be reachable through.
KERNEL_APIS = {
    "zxpon.c": [
        ("memcpy", "linux/string.h"),
        ("memset", "linux/string.h"),
        ("uint8_t", "linux/types.h"),
        ("uint16_t", "linux/types.h"),
        ("uint32_t", "linux/types.h"),
    ],
    "zx279133-omci.c": [
        ("readl", "linux/io.h"),
        ("writel", "linux/io.h"),
        ("lower_32_bits", "linux/wordpart.h"),
        ("upper_32_bits", "linux/wordpart.h"),
        ("cpu_to_le32", "linux/types.h"),
        ("dmam_alloc_coherent", "linux/dma-mapping.h"),
        ("readl_poll_timeout", "linux/iopoll.h"),
        ("dma_rmb", "linux/compiler.h"),
        ("wmb", "linux/compiler.h"),
        ("memcpy", "linux/string.h"),
        ("memset", "linux/string.h"),
        ("mutex_lock", "linux/mutex.h"),
        ("mutex_unlock", "linux/mutex.h"),
    ],
    "zx279133-pon.c": [
        ("netif_carrier_on", "linux/netdevice.h"),
        ("netif_carrier_off", "linux/netdevice.h"),
        ("netif_carrier_ok", "linux/netdevice.h"),
        ("netif_start_queue", "linux/netdevice.h"),
        ("netif_stop_queue", "linux/netdevice.h"),
        ("alloc_netdev", "linux/netdevice.h"),
        ("register_netdev", "linux/netdevice.h"),
        ("unregister_netdev", "linux/netdevice.h"),
        ("free_netdev", "linux/netdevice.h"),
        ("netdev_priv", "linux/netdevice.h"),
        ("to_net_dev", "linux/netdevice.h"),
        ("register_netdevice_notifier", "linux/notifier.h"),
        ("unregister_netdevice_notifier", "linux/notifier.h"),
        ("netdev_notifier_info_to_dev", "linux/notifier.h"),
        ("device_create_file", "linux/sysfs.h"),
        ("device_remove_file", "linux/sysfs.h"),
        ("sysfs_emit", "linux/sysfs.h"),
        ("led_trigger_register_simple", "linux/leds.h"),
        ("led_trigger_unregister_simple", "linux/leds.h"),
        ("led_trigger_event", "linux/leds.h"),
        ("genl_register_family", "net/genetlink.h"),
        ("genl_unregister_family", "net/genetlink.h"),
        ("genlmsg_new", "net/genetlink.h"),
        ("genlmsg_put_reply", "net/genetlink.h"),
        ("genlmsg_cancel", "net/genetlink.h"),
        ("genlmsg_end", "net/genetlink.h"),
        ("genlmsg_reply", "net/genetlink.h"),
        ("nla_put_string", "net/netlink.h"),
        ("nla_data", "net/netlink.h"),
        ("nla_policy", "net/netlink.h"),
        ("dev_kfree_skb", "linux/skbuff.h"),
        ("dev_warn", "linux/device.h"),
        ("schedule_delayed_work", "linux/workqueue.h"),
        ("cancel_delayed_work_sync", "linux/workqueue.h"),
        ("msecs_to_jiffies", "linux/jiffies.h"),
        ("mutex_lock", "linux/mutex.h"),
        ("mutex_unlock", "linux/mutex.h"),
        ("THIS_MODULE", "linux/module.h"),
        ("ARPHRD_NONE", "linux/if_arp.h"),
    ],
}

INCLUDE_RE = re.compile(r'^\s*#\s*include\s+([<"])([^>"]+)[>"]')

# Words that end a statement rather than start a type.
CONTROL_WORDS = {
    "return", "if", "while", "for", "switch", "goto", "do", "else", "case",
    "sizeof", "typeof", "default", "break", "continue",
}

# Words that can start a declaration.
TYPE_WORDS = {
    "int", "void", "char", "long", "short", "unsigned", "signed", "struct",
    "union", "enum", "const", "static", "extern", "inline", "bool", "size_t",
    "ssize_t", "u8", "u16", "u32", "u64", "s8", "s16", "s32", "s64", "__u8",
    "__u16", "__u32", "__u64", "__s32", "__le16", "__le32", "__le64",
    "__be16", "__be32", "__be64", "__sum16", "__sum32", "__wsum",
}


def is_declaration(line, name, previous=""):
    """Is this line a declaration of the name, rather than a call to it?

    A declaration ends in a semicolon, a brace or a comma - the last one
    because a prototype may run over several lines - and what comes before
    the name has to be a type, not the end of a statement.

    The kernel also writes the return type on the line above and lets the
    name start the next one, so that shape is accepted too when the line
    above names a type.
    """
    stripped = line.strip()
    if stripped.startswith(name) and re.match(r"^%s\s*\(" % re.escape(name),
                                              stripped):
        above = previous.strip().rstrip(";{,").strip()
        if above:
            last = above.split()[-1]
            if last.rstrip("*") in TYPE_WORDS or last.endswith("*"):
                return True
    if not stripped.endswith((";", "{", ",")):
        return False
    match = re.match(r"^(.*?)\b" + re.escape(name) + r"\s*\(", stripped)
    if not match:
        return False
    prefix = match.group(1).strip()
    if not prefix:
        return False
    last = prefix.split()[-1]
    if last in CONTROL_WORDS:
        return False
    return last.rstrip("*") in TYPE_WORDS or last.endswith("*")


# What a header has to look like for a name to count as defined by it.
DEFINE_PATTERNS = [
    r"^\s*#\s*define\s+{name}\b",
    r"^\s*static\s+(?:(?:__)?(?:always|force)_inline|inline)\b.*\b{name}\s*\(",
    r"^\s*inline\b.*\b{name}\s*\(",
    r"^\s*(?:extern\s+)?(?:struct|union|enum)\s+{name}\b",
    r"^\s*(?:struct|union|enum)\s+{name}\s*\{{",
    r"^\s*(?:const\s+)?(?:struct|union|enum)\s+{name}\s+[A-Za-z_]",
    r"^\s*typedef\s+.*\b{name}\s*;",
]


class Report:
    def __init__(self):
        self.failures = []
        self.notes = []

    def require(self, ok, message):
        print(f"  [{'OK ' if ok else 'FAIL'}] {message}")
        if not ok:
            self.failures.append(message)
        return ok

    def note(self, message):
        print(f"  [note] {message}")
        self.notes.append(message)


def read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    return text


def defines_of(text):
    """Every name a C file or header defines."""
    names = set(re.findall(r"^\s*#\s*define\s+([A-Za-z_]\w*)", text, re.M))
    names |= set(re.findall(r"^\s*(?:static\s+)?(?:const\s+)?[\w \*]*?\b([a-z_]\w+)\s*\(",
                            text, re.M))
    names |= set(re.findall(r"^\s*(?:struct|union|enum)\s+(\w+)\s*\{", text, re.M))
    names |= set(re.findall(r"^\s*typedef\b.*?\b(\w+)\s*;", text, re.M))
    names |= set(re.findall(
        r"^\s*(?:static\s+)?(?:const\s+)?[\w \*]*?\b((?:zx279133_|ZX279133_|zxpon_)\w+)"
        r"\s*(?:=|;|\[|\()", text, re.M))
    return names


def includes_of(path):
    out = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        match = INCLUDE_RE.match(line)
        if match:
            out.append((match.group(1), match.group(2)))
    return out


class Closure:
    """The headers a file reaches, and the names those headers define."""

    def __init__(self, tree):
        self.tree = Path(tree)
        self.include_dirs = [
            self.tree / "include",
            self.tree / "include" / "uapi",
            self.tree / "arch" / "arm64" / "include",
            self.tree / "arch" / "arm64" / "include" / "uapi",
        ]
        self.source_dirs = [self.tree / ZTE]
        self.texts = {}
        self.order = []

    def resolve(self, kind, name, origin):
        if name.startswith("generated/"):
            return None
        if kind == '"':
            for base in [origin.parent] + self.source_dirs + self.include_dirs:
                candidate = base / name
                if candidate.is_file():
                    return candidate
            return None
        for base in self.include_dirs:
            candidate = base / name
            if candidate.is_file():
                return candidate
        return None

    def build(self, source):
        source = Path(source)
        queue = deque([source])
        seen = set()
        while queue:
            path = queue.popleft()
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            if path not in self.texts:
                try:
                    self.texts[path] = path.read_text(encoding="utf-8",
                                                     errors="replace")
                except OSError:
                    continue
                self.order.append(path)
            for kind, name in includes_of(path):
                resolved = self.resolve(kind, name, path)
                if resolved is not None:
                    queue.append(resolved)
        return self

    def header_path(self, name):
        for base in self.include_dirs:
            candidate = base / name
            if candidate.is_file():
                return candidate
        return None

    def index(self, names):
        """Where each of the wanted names is defined inside the closure.

        A name is visible to the compiler when any header the file reaches
        defines it - the header that happens to define it does not matter,
        and demanding a particular one would be a rule of its own that the
        kernel does not have.
        """
        wanted = set(names)
        patterns = {
            name: [re.compile(p.format(name=re.escape(name)))
                   for p in DEFINE_PATTERNS]
            for name in wanted
        }
        found = {}
        token_re = re.compile(r"[A-Za-z_]\w*")
        for path, text in self.texts.items():
            previous = ""
            for line in text.splitlines():
                for token in set(token_re.findall(line)):
                    if token not in wanted or token in found:
                        continue
                    for pattern in patterns[token]:
                        if pattern.match(line):
                            found[token] = (str(path), line.strip())
                            break
                    else:
                        if is_declaration(line, token, previous):
                            found[token] = (str(path), line.strip())
                previous = line
        return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--repo", default=None,
                        help="the repository root, for the files the 7.3 line "
                             "carries and the config fragment it builds with")
    args = parser.parse_args()

    tree = Path(args.tree)
    report = Report()
    zte = tree / ZTE

    print(f"the PON / OMCI stack in {tree}")

    print("the files the stack brings")
    for name in STACK_FILES:
        report.require((zte / name).is_file(),
                       f"{ZTE}/{name} is in the tree")

    if not all((zte / name).is_file() for name in STACK_FILES):
        return finish(report)

    if args.repo:
        repo = Path(args.repo)
        overlay = repo / "kernel-overlays-73" / ZTE
        for name in STACK_FILES:
            carried = overlay / name
            if not carried.is_file():
                report.require(False, f"the 7.3 driver tree carries {name}")
                continue
            in_tree = (zte / name).read_bytes()
            carried_bytes = carried.read_bytes()
            report.require(
                hashlib.sha256(in_tree).hexdigest() ==
                hashlib.sha256(carried_bytes).hexdigest(),
                f"the tree's {name} is the one the repository carries")

    print("the protocol core is closed")
    header = strip_comments(read(zte / "zxpon.h"))
    source = strip_comments(read(zte / "zxpon.c"))
    declared = set(re.findall(r"^\s*(?:void|int|uint\d+_t|const char \*)\s*"
                               r"(zxpon_\w+)\s*\(", header, re.M))
    defined = set(re.findall(r"^\s*(?:void|int|uint\d+_t|const char \*)\s*"
                              r"(zxpon_\w+)\s*\(", source, re.M))
    # zxpon_state_name returns a pointer; catch it separately.
    defined |= set(re.findall(r"^\s*const char\s*\*\s*(zxpon_\w+)\s*\(",
                              source, re.M))
    missing_definitions = sorted(declared - defined)
    missing_declarations = sorted(defined - declared)
    report.require(not missing_definitions,
                   "every function zxpon.h declares is defined in zxpon.c"
                   + (f" (missing: {missing_definitions})"
                      if missing_definitions else ""))
    report.require(not missing_declarations,
                   "every function zxpon.c defines is declared in zxpon.h"
                   + (f" (missing: {missing_declarations})"
                      if missing_declarations else ""))

    print("the kernel names the stack calls")
    for name, apis in KERNEL_APIS.items():
        closure = Closure(tree).build(zte / name)
        wanted = [symbol for symbol, _ in apis]
        found = closure.index(wanted)
        absent = [symbol for symbol in wanted if symbol not in found]
        report.require(not absent,
                       f"{name} sees every kernel name it calls"
                       + (f" (missing: {absent})" if absent else ""))

    print("the kernel-side contract")
    eth_header = strip_comments(read(zte / "zx279133.h"))
    for function in KERNEL_FUNCTIONS:
        report.require(re.search(r"\b%s\s*\(" % re.escape(function),
                                 eth_header) is not None,
                       f"zx279133.h declares {function}")
    struct_match = re.search(r"struct zx279133_eth \{(.*?)\n\};", eth_header, re.S)
    struct_body = struct_match.group(1) if struct_match else ""
    for field in ETH_FIELDS:
        report.require(re.search(r"\b%s\b" % re.escape(field),
                                 struct_body) is not None,
                       f"struct zx279133_eth carries {field}")

    print("the four hooks")
    main_c = strip_comments(read(zte / "zx279133-main.c"))
    mac_c = strip_comments(read(zte / "zx279133-mac.c"))
    idm_c = strip_comments(read(zte / "zx279133-idm.c"))
    report.require(re.search(r"zx279133_pon_attach\s*\(\s*ndev\s*\)",
                             main_c) is not None,
                   "main.c attaches the PON stack to the WAN netdev")
    report.require(re.search(r"zx279133_pon_stop\s*\(\s*\)",
                             main_c) is not None,
                   "main.c stops the PON stack on teardown")
    report.require(re.search(r"devm_gpiod_get_optional\s*\(\s*dev\s*,\s*"
                             r"\"opsd\"", main_c) is not None,
                   "main.c reads the optical signal detect GPIO")
    report.require(re.search(r"led_trigger_register_simple\s*\(\s*"
                             r"\"zx279133-los\"", main_c) is not None,
                   "main.c registers the loss-of-signal LED trigger")
    report.require(re.search(r"zx279133_pon_carrier_kick\s*\(",
                             mac_c) is not None,
                   "mac.c kicks the carrier when the link comes up")
    report.require(re.search(r"zx279133_omci_prepare\s*\(",
                             idm_c) is not None,
                   "idm.c claims the management queue's slot")

    print("the build wiring")
    makefile = read(zte / "Makefile")
    for obj in ("zx279133-pon.o", "zx279133-omci.o", "zxpon.o"):
        report.require(obj in makefile,
                       f"the NPU module builds {obj}")

    print("the dependencies the new code creates")
    dts = tree / "arch" / "arm64" / "boot" / "dts" / "zte" / "zx279133-sk-d840n.dts"
    if dts.is_file():
        board = read(dts)
        report.require(re.search(r"opsd-gpios\s*=\s*<&\w+\s+\d+", board)
                       is not None,
                       "the board device tree names the optical signal detect")
        report.require('linux,default-trigger = "zx279133-los"' in board,
                       "the board device tree uses the loss-of-signal trigger")
    else:
        report.note("the board device tree is not in this tree")

    if args.repo:
        repo = Path(args.repo)
        fragments = sorted(repo.glob("overlays-73/target/linux/zte/zx279133/config-*"))
        for fragment in fragments:
            config = read(fragment)
            report.require(re.search(r"^CONFIG_LEDS_CLASS=y\s*$", config, re.M)
                           is not None,
                           f"{fragment.name} turns the LED class on")
            report.require(re.search(r"^CONFIG_LEDS_TRIGGERS=y\s*$", config, re.M)
                           is not None,
                           f"{fragment.name} turns the LED triggers on")

    return finish(report)


def finish(report):
    print()
    if report.failures:
        print(f"FAILED: {len(report.failures)}")
        for failure in report.failures:
            print(f"  - {failure}")
        return 1
    print("the PON / OMCI stack fits the tree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
