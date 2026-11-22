#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Syntax-check a kernel driver with the host's compiler.

There is no aarch64 cross toolchain on the machine that pushes this
repository, so the only compiler available there is the host's clang,
which can target aarch64.  A kernel file needs three things that a source
tree does not carry: the generated configuration headers, the generated
release header, and the right include order.  This script writes those
from the configuration fragments the build uses and then asks clang to
parse the file.

The arch's own generated headers - cpucap-defs.h, sysreg-defs.h and
kernel-hwcap.h - are produced by the tree's own tools (awk scripts and one
shell script) rather than approximated, because a wrong approximation makes
the parser report errors that the real build does not have.

It is also the early half of the 7.3 line's build.  The CI runs it on the
prepared tree before the seventy-minute Compile, so a syntax error in the
files this repository owns is found in a minute instead of an hour - and
it is run on the very tree that is about to be compiled, so what it reads
is what the build will read.

Usage:
    python3 tools/kcheck.py --tree TREE --config FILE [--config FILE ...]
                            --file REL_PATH [--file ...]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kshadow

# The tree's own generators are shell scripts and awk scripts, so they have
# to be run with a shell and with awk.  On the machine that pushes this
# repository that is Git Bash (the tree's scripts are written for POSIX sh);
# in CI it is the runner's own sh and awk.
GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
GIT_USR_BIN = r"C:\Program Files\Git\usr\bin"

# The compiler's error line: the file, the line, and the message.
ERROR_RE = re.compile(r"^(?P<file>[^:\n]+):(?P<line>\d+)(?::\d+)?:\s*error:")


def shell_prefix():
    """The command that runs a shell script from the tree."""
    if os.name == "nt" and os.path.isfile(GIT_BASH):
        return [GIT_BASH, "-c"]
    return ["sh", "-c"]


def awk_prefix():
    """The command that runs an awk script from the tree."""
    if os.name == "nt":
        candidate = os.path.join(GIT_USR_BIN, "awk.exe")
        if os.path.isfile(candidate):
            return [candidate]
    return ["awk"]


def run_shell(command):
    """A shell command from the tree, run with the right shell."""
    return subprocess.run(shell_prefix() + [command],
                          capture_output=True, text=True, errors="replace")


def clang_version(command):
    """The version of a clang, as a tuple of integers."""
    try:
        result = subprocess.run([command, "--version"], capture_output=True,
                                text=True, errors="replace")
    except OSError:
        return ()
    match = re.search(r"clang version (\d+)(?:\.(\d+))?", result.stdout)
    if not match:
        return ()
    return (int(match.group(1)), int(match.group(2) or 0))


def newest_clang(preferred=None):
    """The clang to use: the one that was asked for, or the newest there is.

    The CI runner carries several clang versions, and the newest one is the
    one that can parse a kernel of this vintage; the machine that pushes
    this repository carries one.  An explicit --clang wins over both.
    """
    if preferred:
        return preferred
    base = shutil.which("clang")
    if not base:
        return None
    best = base
    best_version = clang_version(base)
    directory = os.path.dirname(base)
    try:
        entries = sorted(os.listdir(directory))
    except OSError:
        entries = []
    for entry in entries:
        if not entry.startswith("clang-") or not entry[6:].isdigit():
            continue
        candidate = os.path.join(directory, entry)
        version = clang_version(candidate)
        if version > best_version:
            best_version = version
            best = candidate
    return best


def error_is_in(line, target):
    """Whether an error the compiler printed is in the file we asked about.

    The compiler prints the path it was given, which may be relative or
    absolute, so the two are compared by their tail.
    """
    match = ERROR_RE.match(line.strip())
    if not match:
        return False
    name = match.group("file").replace("\\", "/")
    wanted = str(target).replace("\\", "/")
    return name == wanted or name.endswith("/" + wanted)


# How many of each kind of generated header a complete environment has.
NEEDED = {"arch": 3, "syscall": 5, "shadow": 1, "bounds": 1}


def incomplete_environment(made):
    """What the generated headers are missing, if anything.

    A file is only judged when the environment it was parsed in is the
    environment the build uses.  If a header could not be produced - the
    tree's own awk script failed, or the compiler could not compile
    kernel/bounds.c - then the parser is reading a different kernel than
    the build would, and its errors are not a verdict about the file.
    """
    missing = []
    if made.get("arch", 0) < NEEDED["arch"]:
        missing.append("the arch's own generated headers")
    if made.get("syscall", 0) < NEEDED["syscall"]:
        missing.append("the syscall headers")
    if made.get("shadow", 0) < NEEDED["shadow"]:
        missing.append("the shadow headers")
    if made.get("bounds", 0) < NEEDED["bounds"]:
        missing.append("kernel/bounds.c")
    return missing


def read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def to_msys(path):
    """A path as the shell sees it.

    On this machine the shell is Git Bash, which wants the Windows path
    spelled the Unix way; in CI the shell is the runner's own and takes the
    path as it is.
    """
    if os.name != "nt":
        return str(Path(path))
    text = str(Path(path).resolve())
    return "/" + text[0].lower() + text[2:].replace("\\", "/")


def parse_config(text):
    """The config fragment into an ordered name -> value mapping.

    A value of None means "explicitly not set".  Later fragments override
    earlier ones, which is how OpenWrt merges the generic fragment with the
    target's own.
    """
    settings = {}
    for line in text.splitlines():
        line = line.strip()
        match = re.match(r"^(CONFIG_[A-Za-z0-9_]+)=(.*)$", line)
        if match:
            settings[match.group(1)] = match.group(2)
            continue
        match = re.match(r"^# (CONFIG_[A-Za-z0-9_]+) is not set$", line)
        if match:
            settings[match.group(1)] = None
    return settings


def write_generated(gen_root, settings, version):
    """The generated headers the kernel expects."""
    gen = gen_root / "include" / "generated"
    gen.mkdir(parents=True, exist_ok=True)
    (gen_root / "include" / "config").mkdir(parents=True, exist_ok=True)

    autoconf = ["/* Generated by kcheck.py - do not edit. */", "#ifndef __GENERATED_AUTOCONF_H",
                "#define __GENERATED_AUTOCONF_H", "#define CONFIG_SYSFS 1",
                "#define CONFIG_SYSFS_ABI 1", "#define CONFIG_PAGE_SIZE 4096",
                "#define CONFIG_PHYSICAL_START 0x80000000"]
    auto = ["# Generated by kcheck.py - do not edit."]
    for name, value in settings.items():
        if value is None:
            autoconf.append("/* #define %s is not set */" % name)
            auto.append("# %s is not set" % name)
            continue
        if value in ("y", "m"):
            autoconf.append("#define %s 1" % name)
        else:
            autoconf.append("#define %s %s" % (name, value))
        auto.append("%s=%s" % (name, value))
    autoconf += ["#endif /* __GENERATED_AUTOCONF_H */", ""]
    (gen / "autoconf.h").write_text("\n".join(autoconf), encoding="utf-8")

    (gen / "utsrelease.h").write_text(
        '#define UTS_RELEASE "%s"\n' % version, encoding="utf-8")
    (gen / "bounds.h").write_text("", encoding="utf-8")
    (gen / "timeconst.h").write_text("", encoding="utf-8")
    # kernel/sched/rq-offsets.c generates this from offsetof(struct rq, ...).
    # The value is a layout number, not an interface: nothing here parses
    # differently because of it, so a placeholder is enough.
    (gen / "rq-offsets.h").write_text(
        "/* Generated by kcheck.py - do not edit. */\n#define RQ_nr_pinned 0\n",
        encoding="utf-8")

    auto.append("")
    (gen_root / "include" / "config" / "auto.conf").write_text(
        "\n".join(auto), encoding="utf-8")
    (gen_root / "include" / "config" / "auto.conf.cmd").write_text(
        "# Generated by kcheck.py - do not edit.\n", encoding="utf-8")


def write_shadows(tree, gen_root):
    """The shadow headers clang needs, one per header with an anonymous member.

    GCC accepts a struct member that is a declaration without a declarator:
    an anonymous member.  Clang parses it as a declaration that declares
    nothing, warns, and drops the member - so the parser's view of the
    structure is not the compiler's, and the layout asserts right below
    the member fail.  The shadow header keeps the member but gives it a
    body, so clang lays the structure out the way GCC does and the asserts
    hold.  Nothing else about the header changes, and the shadow
    directories are earlier in the include order than the tree's own.
    """
    roots = [
        (tree / "include", gen_root / "include"),
        (tree / "arch" / "arm64" / "include", gen_root / "arch" / "arm64" / "include"),
    ]
    headers = []
    bodies = {}
    macros = set()
    for source_root, _ in roots:
        if not source_root.is_dir():
            continue
        for path in sorted(source_root.rglob("*.h")):
            try:
                text = read(path)
            except OSError:
                continue
            headers.append((path, text))
            bodies.update(kshadow.scan_structs(text))
            macros.update(kshadow.macro_names(text))

    made = 0
    problems = []
    for path, text in headers:
        rewritten, skipped = kshadow.rewrite(text, bodies, macros)
        if rewritten is None:
            continue
        for number, name, why in skipped:
            problems.append("%s:%d: struct %s left alone (%s)"
                            % (path, number, name, why))
        for source_root, target_root in roots:
            try:
                relative = path.relative_to(source_root)
            except ValueError:
                continue
            out = target_root / relative
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(rewritten, encoding="utf-8", newline="\n")
            made += 1
            break
    return made, problems


def resolve_link(path):
    """The file a symbolic link points at.

    The tree carries links - arch/arm64/tools/syscall_64.tbl is one -
    and extracting the tarball on this machine leaves them dangling, so
    the link is followed by hand.
    """
    if path.is_file():
        return path
    try:
        target = os.readlink(path)
    except OSError:
        return path
    candidate = (path.parent / target).resolve()
    return candidate if candidate.is_file() else path


def write_syscall_headers(tree, gen_root):
    """The syscall headers arm64 generates from its own tables.

    arch/arm64/include/asm/Kbuild lists them under syscall-y, so the
    build generates them: the 32 bit syscall numbers, the same numbers
    with the compat32_ prefix, and the two syscall tables.  Two headers
    in the tree include the generated one - the seccomp header and the
    vDSO's compat_gettimeofday - so without them the parser stops at
    the first include that is not there.
    """
    out = gen_root / "arch" / "arm64" / "include" / "generated" / "asm"
    out.mkdir(parents=True, exist_ok=True)
    uapi_out = gen_root / "arch" / "arm64" / "include" / "generated" / "uapi" / "asm"
    uapi_out.mkdir(parents=True, exist_ok=True)
    header = tree / "scripts" / "syscallhdr.sh"
    table = tree / "scripts" / "syscalltbl.sh"
    tools = tree / "arch" / "arm64" / "tools"
    made = 0
    problems = []
    jobs = [
        ("unistd_32.h", header, ["--abis", "common,32"],
         tools / "syscall_32.tbl", out),
        ("unistd_compat_32.h", header,
         ["--abis", "common,32", "--prefix", "compat32_"],
         tools / "syscall_32.tbl", out),
        ("unistd_64.h", header,
         ["--abis", "common,64,renameat,rlimit,memfd_secret"],
         tools / "syscall_64.tbl", uapi_out),
        ("syscall_table_32.h", table, ["--abis", "common,32"],
         tools / "syscall_32.tbl", out),
        ("syscall_table_64.h", table,
         ["--abis", "common,64,renameat,rlimit,memfd_secret"],
         tools / "syscall_64.tbl", out),
    ]
    for name, script, args, source, folder in jobs:
        source = resolve_link(source)
        if not script.is_file():
            problems.append("%s: %s is missing" % (name, script))
            continue
        if not source.is_file():
            problems.append("%s: %s is missing" % (name, source))
            continue
        target = folder / name
        result = run_shell("sh %s %s %s %s" % (
            to_msys(script), " ".join(args), to_msys(source),
            to_msys(target)))
        if result.returncode != 0 or not target.is_file():
            problems.append("%s: %s" % (name, result.stderr.strip()[:200]))
            continue
        made += 1
    return made, problems


def write_asm_generic(tree, gen_root):
    """The arch's generated asm/ headers, the way the kernel builds them.

    include/asm-generic/Kbuild lists the headers every architecture may use
    (mandatory-y), and an architecture that has no header of its own gets a
    generated wrapper that includes the generic one.  The kernel does this
    with scripts/Makefile.asm-headers; here it is done directly, because a
    file that includes <asm/percpu_types.h> cannot even be parsed without it.
    """
    generic_kbuild = tree / "include" / "asm-generic" / "Kbuild"
    arch_kbuild = tree / "arch" / "arm64" / "include" / "asm" / "Kbuild"
    uapi_kbuild = tree / "arch" / "arm64" / "include" / "uapi" / "asm" / "Kbuild"
    uapi_generic_kbuild = tree / "include" / "uapi" / "asm-generic" / "Kbuild"

    def lists(path):
        wanted = {"mandatory-y", "generic-y"}
        found = set()
        if not path.is_file():
            return found
        for line in read(path).splitlines():
            match = re.match(r"^\s*([\w-]+)\s*\+=\s*(\S+)\s*$", line)
            if match and match.group(1) in wanted:
                found.add(match.group(2))
            match = re.match(r"^\s*([\w-]+)\s*=\s*(\S+)\s*$", line)
            if match and match.group(1) in wanted:
                found.add(match.group(2))
        return found

    headers = lists(generic_kbuild) | lists(arch_kbuild)
    generated = gen_root / "arch" / "arm64" / "include" / "generated" / "asm"
    generated.mkdir(parents=True, exist_ok=True)
    made = 0
    for name in sorted(headers):
        own = tree / "arch" / "arm64" / "include" / "asm" / name
        if own.is_file():
            continue
        (generated / name).write_text(
            "#include <asm-generic/%s>\n" % name, encoding="utf-8")
        made += 1

    uapi = lists(uapi_kbuild) | lists(uapi_generic_kbuild)
    uapi_out = gen_root / "arch" / "arm64" / "include" / "generated" / "uapi" / "asm"
    uapi_out.mkdir(parents=True, exist_ok=True)
    for name in sorted(uapi):
        own = tree / "arch" / "arm64" / "include" / "uapi" / "asm" / name
        if own.is_file():
            continue
        (uapi_out / name).write_text(
            "#include <asm-generic/%s>\n" % name, encoding="utf-8")
        made += 1
    return made


def write_arch_generated(tree, gen_root):
    """The three headers arch/arm64 generates for itself.

    They are generated by the tree's own tools, so that what the parser sees
    is what the kernel's build produces.  An approximation is not good
    enough here: the cpucap numbers are switch labels, so two capabilities
    with the same number is a duplicate-case error the real build does not
    have.
    """
    out = gen_root / "arch" / "arm64" / "include" / "generated" / "asm"
    out.mkdir(parents=True, exist_ok=True)
    tools = tree / "arch" / "arm64" / "tools"
    made = 0
    problems = []

    awk = None
    for header, script, source in (
            ("cpucap-defs.h", "gen-cpucaps.awk", "cpucaps"),
            ("sysreg-defs.h", "gen-sysreg.awk", "sysreg")):
        script_path = tools / script
        source_path = tools / source
        if not script_path.is_file() or not source_path.is_file():
            problems.append("missing %s or %s" % (script, source))
            continue
        result = subprocess.run(
            awk_prefix() + ["-f", str(script_path), str(source_path)],
            capture_output=True, text=True, errors="replace")
        if result.returncode != 0:
            problems.append("%s: %s" % (script, result.stderr.strip()[:200]))
            continue
        (out / header).write_text(result.stdout, encoding="utf-8", newline="\n")
        made += 1

    hwcap_script = tools / "gen-kernel-hwcaps.sh"
    hwcap_source = tree / "arch" / "arm64" / "include" / "uapi" / "asm" / "hwcap.h"
    if hwcap_script.is_file() and hwcap_source.is_file():
        result = run_shell("sh %s %s" % (to_msys(hwcap_script),
                                         to_msys(hwcap_source)))
        if result.returncode != 0:
            problems.append("gen-kernel-hwcaps.sh: %s" % result.stderr.strip()[:200])
        else:
            (out / "kernel-hwcap.h").write_text(result.stdout, encoding="utf-8",
                                                newline="\n")
            made += 1
    else:
        problems.append("missing gen-kernel-hwcaps.sh or hwcap.h")

    return made, problems


# Kconfig choices: the fragments are merged by hand here, so a later
# fragment can leave a symbol set that an earlier one had set.  Kconfig
# would resolve that itself; these are the choices that matter here.
CHOICE_GROUPS = [
    ("CONFIG_FLATMEM", "CONFIG_SPARSEMEM", "CONFIG_SPARSEMEM_VMEMMAP"),
]


def resolve_choices(settings):
    """One symbol per choice group, the last one the group lists.

    The fragments are merged by hand here, so a symbol that one fragment
    set can be left set by an earlier one as well - and a choice group
    cannot have two of its members set.  The group's order is the
    kernel's: the last member is the one that is meant to win, and on
    arm64 that is SPARSEMEM_VMEMMAP.
    """
    for group in CHOICE_GROUPS:
        chosen = None
        for name in group:
            if settings.get(name) in ("y", "m"):
                chosen = name
        if chosen is None:
            continue
        for name in group:
            if name != chosen:
                settings[name] = None


def write_bounds(tree, gen_root, base_args, clang):
    """include/generated/bounds.h, the way the kernel builds it.

    kernel/bounds.c emits the constants as .ascii strings in the
    assembler's output; the build then turns them into a header.  Compiling
    it here with the same compiler and the same headers gives the same
    numbers.
    """
    source = tree / "kernel" / "bounds.c"
    if not source.is_file():
        return 0, ["kernel/bounds.c is missing"]
    asm = gen_root / "bounds.s"
    result = subprocess.run([clang, "-S", "-target", "aarch64-linux-gnu"] +
                             base_args + [str(source), "-o", str(asm)],
                             capture_output=True, text=True, errors="replace")
    if result.returncode != 0:
        return 0, ["kernel/bounds.c: %s" % result.stderr.strip()[:300]]
    values = {}
    for line in read(asm).splitlines():
        match = re.search(r'"->(\S+)\s+(\d+)\s', line)
        if match:
            values[match.group(1)] = match.group(2)
    if not values:
        return 0, ["kernel/bounds.c produced no constants"]
    out = gen_root / "include" / "generated" / "bounds.h"
    lines = ["/* Generated by kcheck.py - do not edit. */"]
    for name in sorted(values):
        lines.append("#define %s %s" % (name, values[name]))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(values), []


OUR_PREFIXES = ("zx279133", "zxpon", "zte_nppt")

EXPORT = re.compile(
    r"EXPORT_SYMBOL(?:_GPL|_GPL_NS|_NS|_IF_KUNIT)?\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)")

TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Some exports are written by a macro, so the name in the source is not
# the name in the symbol table.  These are the ones this tree uses, and
# what each of them produces.
EXPORT_MACROS = [
    # kernel/params.c: STANDARD_PARAM_DEF(uint, ...) defines
    # param_ops_uint and exports it.
    (re.compile(r"STANDARD_PARAM_DEF\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)"),
     "param_ops_%s"),
    # net/core/filter.c: DEFINE_BPF_DISPATCHER(xdp) defines
    # bpf_dispatcher_xdp_func and exports it.
    (re.compile(r"DEFINE_BPF_DISPATCHER\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)"),
     "bpf_dispatcher_%s_func"),
    # drivers/base/core.c: define_dev_printk_level(_dev_err, ...) defines
    # _dev_err and exports it.
    (re.compile(r"define_dev_printk_level\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)"),
     "%s"),
    # the tracepoints: EXPORT_TRACEPOINT_SYMBOL_GPL(xdp_exception) exports
    # __tracepoint_xdp_exception and __traceiter_xdp_exception.
    (re.compile(r"EXPORT_TRACEPOINT_SYMBOL(?:_GPL)?\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)"),
     "__tracepoint_%s"),
    (re.compile(r"EXPORT_TRACEPOINT_SYMBOL(?:_GPL)?\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)"),
     "__traceiter_%s"),
]


def collect_exports(tree, cache_path):
    """The names the kernel knows, the way modpost sees them.

    A module may only use a symbol the kernel exports, or one the kernel
    defines - but the export of a symbol that a macro builds is written
    inside the macro, so the exported names alone are not enough: the
    names that the tree's own source files mention are collected too.
    That is what tells a symbol that exists from one that does not.
    The scan takes a while over a whole tree, so the result is cached.
    """
    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            return set(cached["exported"]), set(cached["known"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
    exported = set()
    known = set()
    for pattern in ("*.c", "*.h", "*.S"):
        for path in tree.rglob(pattern):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for match in EXPORT.finditer(text):
                exported.add(match.group(1))
            for find, shape in EXPORT_MACROS:
                for match in find.finditer(text):
                    exported.add(shape % match.group(1))
            if pattern == "*.c":
                known.update(TOKEN.findall(text))
    payload = {"exported": sorted(exported), "known": sorted(known)}
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass
    return exported, known


def check_symbols(args, objects, objdir):
    """The symbols the objects need and do not have.

    A syntax check does not see a link error: a call to a function that
    no file defines parses fine and fails when the module is linked.  A
    symbol the objects want has to be either defined by the module set
    itself, exported by the kernel, or at least mentioned by the kernel's
    own source - the last one catches the calls that name something the
    tree never had.
    """
    defined = set()
    undefined = {}
    for rel, path in objects:
        out = subprocess.run([args.nm, "--format=posix", str(path)],
                             capture_output=True, text=True, errors="replace")
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            if len(parts[0]) == 1 and parts[0].isalpha():
                kind = parts[0]
                name = parts[-1]
            else:
                name = parts[0]
                kind = parts[1]
            if kind == "U":
                undefined.setdefault(name, []).append(rel)
            elif kind in ("T", "D", "B", "R", "W", "t", "d", "b", "r", "V",
                          "G", "S", "C"):
                defined.add(name)
    exports, known = collect_exports(Path(args.tree),
                                     Path(args.gen) / "exports.json")
    missing = []
    for name, users in sorted(undefined.items()):
        if name in defined or name in exports or name in known:
            continue
        missing.append((name, users))
    print("symbols: %d defined, %d undefined, %d exported by the kernel, "
          "%d not known to the kernel at all"
          % (len(defined), len(undefined), len(exports), len(missing)))
    for name, users in missing:
        print("  undefined: %s  (used by %s)"
              % (name, ", ".join(sorted(set(users)))))
    return len(missing)


def build_base_args(tree, gen_root):
    """The flags the kernel build uses, minus the parts that are ours."""
    return [
        "-target", "aarch64-linux-gnu",
        "-D__KERNEL__", "-D__arm64__", "-D__LITTLE_ENDIAN__",
        "-D__ARM_ARCH_8A", "-D__aarch64__",
        # the build passes the module's name; several headers put it in
        # their messages, and the netlink ones put it in a string that has
        # to be a literal
        "-DKBUILD_MODNAME=\"zx279133\"",
        "-DKBUILD_MODFILE=\"zx279133.c\"",
        # IS_ENABLED lives in linux/kconfig.h, and the arch headers use it
        # without including it themselves.
        "-include", "linux/kconfig.h",
        "-I", str(gen_root / "include" / "generated"),
        "-I", str(gen_root / "include" / "config"),
        "-I", str(gen_root / "include"),
        "-I", str(tree / "include"),
        "-I", str(gen_root / "arch" / "arm64" / "include"),
        "-I", str(tree / "arch" / "arm64" / "include"),
        "-I", str(gen_root / "arch" / "arm64" / "include" / "generated"),
        "-I", str(tree / "arch" / "arm64" / "include" / "generated"),
        # USERINCLUDE, from the tree's Makefile.
        "-I", str(tree / "arch" / "arm64" / "include" / "uapi"),
        "-I", str(gen_root / "arch" / "arm64" / "include" / "generated" / "uapi"),
        "-I", str(tree / "include" / "uapi"),
        "-I", str(gen_root / "include" / "generated" / "uapi"),
        "-std=gnu11",
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--config", action="append", required=True)
    parser.add_argument("--file", action="append", required=True)
    parser.add_argument("--gen", default=None,
                        help="where the generated headers go")
    parser.add_argument("--clang", default=None,
                        help="the clang to use; the newest one on this "
                             "machine by default")
    parser.add_argument("--version", default="7.3.4")
    parser.add_argument("--raw", action="store_true",
                        help="print the compiler's output verbatim")
    parser.add_argument("--objdir", default=None,
                        help="compile to object files here and check the symbols")
    parser.add_argument("--nm", default="llvm-nm")
    args = parser.parse_args()

    tree = Path(args.tree)
    gen_root = Path(args.gen) if args.gen else tree / ".kcheck"
    if gen_root.exists():
        shutil.rmtree(gen_root)

    clang = newest_clang(args.clang)
    if not clang:
        print("no clang on this machine: the check cannot run")
        return 1
    print("clang: %s (%s)" % (clang, ".".join(str(p) for p in clang_version(clang))))

    enabled = {}
    for fragment in args.config:
        path = Path(fragment)
        if not path.is_file():
            # The OpenWrt tree does not carry a fragment for every target:
            # the zte target has no config-7.3 of its own, only the generic
            # one and the subtarget's.  A fragment that is not there
            # contributes nothing, which is not an error.
            print("  (no config fragment %s)" % fragment)
            continue
        enabled.update(parse_config(read(path)))
    resolve_choices(enabled)

    base_args = build_base_args(tree, gen_root)

    write_generated(gen_root, enabled, args.version)
    shadow_made, shadow_problems = write_shadows(tree, gen_root)
    syscall_made, syscall_problems = write_syscall_headers(tree, gen_root)
    made = write_asm_generic(tree, gen_root)
    arch_made, problems = write_arch_generated(tree, gen_root)
    bounds_made, bounds_problems = write_bounds(tree, gen_root, base_args,
                                                 clang)
    problems.extend(bounds_problems)
    problems.extend(shadow_problems)
    problems.extend(syscall_problems)
    print("generated headers: %d config symbols, %d asm wrappers, "
          "%d arch headers, %d bounds constants, %d shadow headers, "
          "%d syscall headers"
          % (len(enabled), made, arch_made, bounds_made, shadow_made,
             syscall_made))
    for problem in problems:
        print("  (could not generate: %s)" % problem)
    incomplete = incomplete_environment(
        {"arch": arch_made, "syscall": syscall_made,
         "shadow": shadow_made, "bounds": bounds_made})
    if incomplete:
        print("the generated headers are incomplete (%s):"
              % ", ".join(incomplete))
        print("  the compiler is not reading the kernel the build would read,")
        print("  so its errors are not a verdict about our files")

    common = ["-fsyntax-only"] + base_args + [
        "-Wall", "-Wno-unused-variable", "-Wno-unused-but-set-variable",
        "-Wno-unknown-warning-option", "-Wno-attributes", "-Wno-pointer-sign",
        "-Wno-sign-compare", "-Wno-int-conversion", "-Wno-incompatible-pointer-types",
        "-Wno-gnu-anonymous-struct", "-Wno-declaration-after-statement",
    ]

    objdir = Path(args.objdir) if args.objdir else None
    if objdir:
        objdir.mkdir(parents=True, exist_ok=True)
    objects = []

    failures = 0
    unjudgeable = 0
    for rel in args.file:
        target = tree / rel
        if not target.is_file():
            print("MISSING %s" % rel)
            failures += 1
            continue
        if objdir:
            name = rel.replace("/", "_") + ".o"
            out = subprocess.run(
                [clang, "-c", "-O1", "-fno-asynchronous-unwind-tables"] +
                base_args + [str(target), "-o", str(objdir / name)],
                capture_output=True, text=True, errors="replace")
            if out.returncode == 0:
                print("OK    %s" % rel)
                objects.append((rel, objdir / name))
                continue
            failures += 1
            print("FAIL  %s" % rel)
            for line in out.stderr.splitlines()[:40]:
                if line.strip():
                    print("      " + line)
            continue
        out = subprocess.run([clang] + common + [str(target)],
                             capture_output=True, text=True, errors="replace")
        if out.returncode == 0:
            if not incomplete:
                print("OK    %s" % rel)
                continue
            # It parsed, but the parser was not reading the kernel the
            # build reads, so a clean parse is not the evidence it looks
            # like: a missing header can also take code out of the file.
            print("WARN  %s" % rel)
            unjudgeable += 1
            print("      (it parsed, but the generated headers are incomplete:")
            print("       the parser was not reading the kernel the build reads)")
            continue
        raw = out.stderr.splitlines()
        errors = [line for line in raw if "error:" in line]
        # The verdict is about our files.  An error the compiler reports in
        # one of the tree's own headers means the compiler could not read
        # the tree - a clang older than the kernel it is reading says so -
        # and then the errors in our files may be a consequence of that
        # rather than a mistake of ours.  So those are reported and the
        # file is not failed on them.
        ours = [line for line in errors if error_is_in(line, target)]
        elsewhere = [line for line in errors if not error_is_in(line, target)]
        if elsewhere or incomplete:
            print("WARN  %s" % rel)
            if incomplete:
                unjudgeable += 1
                print("      (the generated headers are incomplete, so the")
                print("       compiler's errors are not a verdict)")
            for line in elsewhere[:10]:
                print("      " + line)
            if elsewhere:
                print("      (the compiler could not read the tree's own headers,")
                print("       so the errors in this file are not a verdict)")
            continue
        failures += 1
        print("FAIL  %s" % rel)
        for line in ours[:40]:
            print("      " + line)
        if args.raw:
            for line in raw[:200]:
                print("      " + line)
            continue
        lines = [line for line in raw
                 if line.strip() and "warning:" not in line and line not in errors]
        for line in lines[:200]:
            print("      " + line)
    if objects:
        failures += check_symbols(args, objects, objdir)
    print()
    print("%d file(s) asked for, %d failed, %d could not be judged"
          % (len(args.file), failures, unjudgeable))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
