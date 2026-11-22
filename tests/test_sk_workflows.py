#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that the workflows themselves are sound before a build is spent on them.

A workflow file is parsed by GitHub before any job starts.  When that parse
fails, the run is created, it is reported as failed, and it has no jobs at all
- so the only evidence is the run's conclusion.  That is exactly what happened
to the 7.3 line in run #5 (2026-11-22, head 94335616): the step

    - name: Gate: the resolved config carries exactly one theme

carried a colon and a space in an unquoted YAML scalar, which made the whole
file unparseable.  The build never started, and the CI slot was spent.

So the parse is checked here, on the machine that pushes the repository, in
the gate job that runs before every build.  The checks below are about the
shape of the two kernel lines:

  * every workflow file parses,
  * every job and step the lines depend on is still there,
  * every test file a gate job runs exists in tests/,
  * the 7.3 line still rebuilds when the inputs of that line change.

The last one matters as much as the parse: the 7.3 line is driven by a path
filter, so a patch series that lands outside the filter builds nothing and the
green run says nothing about it.
"""
import importlib.util
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:                                    # pragma: no cover
    print("python3-yaml is not installed", file=sys.stderr)
    sys.exit(2)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
TESTS = ROOT / "tests"

# The workflow of the release line and the one of the 7.3 bring-up line.
RELEASE = "build-sk-d840n.yml"
BRINGUP = "build-sk-d840n-73.yml"

# The steps the release line cannot lose: the gates, the build and the
# bootweb job that produces the offline candidate.
RELEASE_JOBS = {"check-led-gates", "build", "bootweb"}
BRINGUP_JOBS = {"check-gates", "build"}

# The steps of the 7.3 line that carry the whole point of the line.
BRINGUP_STEPS = [
    "The firmware tools",
    "Rust 1.96.1 for the OMCI engine",
    "Prepare the Linux 7.3.4 tree",
    "Check the board layer against the 7.3 tree",
    "Apply the board layer",
    "Check the applied board layer",
    "Check the 9132 switch driver against the 7.3 tree",
    "Check the PON / OMCI stack against the 7.3 tree",
    "Check the 9132 switch programming against the 7.3 tree",
    "Apply the OpenWrt-side overlays",
    "Enable /dev/mem in the 7.3 fragments",
    "Build the zte drivers into the kernel",
    "Install feeds",
    "Configure",
    "Download packages",
    "Compile",
    "Gate: the zte drivers are built into the kernel",
    "Gate: the merged kernel config carries the board requirements",
    "Gate: every compatible the device tree names has a driver",
    "Gate: the built kernel is 7.3.4",
    "Gate: the board device tree compiles against the 7.3 tree",
    "Collect the images",
    "Gate: the image carries the packages the board needs",
    "Validate and export the partitions",
    "Upload the images",
]

# The paths that decide whether a change to the 7.3 line starts a build.
BRINGUP_TRIGGERS = [
    "kernel-patches/**",
    "kernel-overlays-73/**",
    "overlays-73/**",
    "tools/prepare-sk-kernel-73.py",
    "tools/apply-sk-overlay-73.sh",
    "tools/check-sk-board-api.py",
    "tools/check-sk-switch-api.py",
    "tools/check-sk-pon-api.py",
    "tools/check-sk-9132-api.py",
    "scripts/configure-sk-d840n.sh",
    "configs/sk-d840n-73.json",
]

# The gates both lines run before they build anything.
SHARED_GATES = [
    "tests/test_sk_kernel.py",
    "tests/test_sk_led_dts_gate.py",
    "tests/test_sk_layout.py",
    "tests/test_sk_uboot.py",
]

# The zte drivers that have to end up inside the kernel image on the 7.3
# line: the NPPT/IDM controller itself, the XMAC0 LAN conduit it owns, the
# 9132 switch behind it and the flow offload engine.  The board's device
# tree names the switch node, so all four have to be built in.
ZTE_DRIVER_SYMBOLS = [
    "ZTE_ZX279133_ETH",
    "ZTE_ZX279133_LAN_CONDUIT",
    "ZTE_ZX279133_SWITCH",
    "ZTE_ZX279133_FNOFFLOAD",
]


class Failures:
    def __init__(self):
        self.items = []

    def check(self, ok, message):
        print(f"  [{'OK ' if ok else 'FAIL'}] {message}")
        if not ok:
            self.items.append(message)
        return ok

    def report(self, what):
        if self.items:
            print(f"\nFAILED: {what} ({len(self.items)} problem(s))")
            for item in self.items:
                print(f"  - {item}")
            return 1
        print(f"\n{what}: OK")
        return 0


def load(path, failures):
    """Parse one workflow file and report the parse error in its own words."""
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        failures.check(False, f"{path.name} has a UTF-8 BOM")
        return None
    try:
        doc = yaml.safe_load(raw.decode("utf-8"))
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        where = f"line {mark.line + 1}" if mark is not None else "unknown line"
        failures.check(False, f"{path.name} does not parse ({where}): {error}")
        return None
    if not isinstance(doc, dict):
        failures.check(False, f"{path.name} does not parse into a mapping")
        return None
    failures.check(True, f"{path.name} parses")
    return doc


def triggers_of(doc):
    """The push path filter.  YAML 1.1 reads the key "on" as the boolean True."""
    node = doc.get("on", doc.get(True))
    if not isinstance(node, dict):
        return []
    push = node.get("push") or {}
    paths = push.get("paths") or []
    return [str(path) for path in paths]


def steps_of(doc, job_name):
    job = (doc.get("jobs") or {}).get(job_name) or {}
    return job.get("steps") or []


def step_names(doc, job_name):
    return [str(step.get("name") or step.get("uses") or "?")
            for step in steps_of(doc, job_name)]


def run_text(doc, job_name):
    """Everything the job's steps execute, concatenated."""
    parts = []
    for step in steps_of(doc, job_name):
        body = step.get("run")
        if body:
            parts.append(str(body))
    return "\n".join(parts)


def step_run_text(doc, job_name, step_name):
    """The 'run:' body of one named step, empty when there is no such step."""
    for step in steps_of(doc, job_name):
        if str(step.get("name") or "") == step_name:
            return str(step.get("run") or "")
    return ""


def referenced_tests(text):
    return sorted(set(re.findall(r"tests/[\w./-]+\.(?:py|sh)", text)))


def shell_list(text, name):
    """The words of a shell variable assignment: name="A B C" -> ["A", "B", "C"].

    The two workflows keep their symbol lists in a shell variable, and the
    point of comparing them is to compare what the shell would actually use -
    so the value is read the way the shell reads it, not with a regexp that
    happens to match today.
    """
    match = re.search(r'^\s*%s="([^"]*)"' % re.escape(name), text, re.MULTILINE)
    if not match:
        return []
    return match.group(1).split()


def main():
    failures = Failures()

    print("the workflow files parse")
    docs = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = load(path, failures)
        if doc is not None:
            docs[path.name] = doc
    if failures.items:
        return failures.report("workflows")

    release = docs.get(RELEASE)
    bringup = docs.get(BRINGUP)
    failures.check(release is not None, f"{RELEASE} is present")
    failures.check(bringup is not None, f"{BRINGUP} is present")
    if release is None or bringup is None:
        return failures.report("workflows")

    print("\nthe release line")
    release_jobs = set((release.get("jobs") or {}).keys())
    for job in sorted(RELEASE_JOBS):
        failures.check(job in release_jobs, f"{RELEASE} has the job {job}")
    failures.check(
        str(((release.get("jobs") or {}).get("check-led-gates") or {}).get("name", ""))
        == "LED CI regression",
        f"{RELEASE} names its gate job")
    release_gate = run_text(release, "check-led-gates")
    for gate in SHARED_GATES:
        failures.check(gate in release_gate,
                       f"the release line runs {gate}")

    print("\nthe 7.3 bring-up line")
    bringup_jobs = set((bringup.get("jobs") or {}).keys())
    for job in sorted(BRINGUP_JOBS):
        failures.check(job in bringup_jobs, f"{BRINGUP} has the job {job}")
    names = step_names(bringup, "build")
    for step in BRINGUP_STEPS:
        failures.check(step in names, f"{BRINGUP} has the step {step!r}")

    bringup_gate = run_text(bringup, "check-gates")
    for gate in SHARED_GATES:
        failures.check(gate in bringup_gate,
                       f"the 7.3 line runs {gate}")

    print("\nthe path filter of the 7.3 line")
    triggers = triggers_of(bringup)
    for path in BRINGUP_TRIGGERS:
        failures.check(path in triggers,
                       f"the 7.3 line rebuilds on {path}")

    # The OMCI engine is a Rust package, and the package's Makefile takes
    # the toolchain from ZX_CARGO - an environment variable, not a default
    # that can be found on the machine.  The 7.3 line had no step that
    # installed it, so every one of its builds died at the end of the
    # Compile step with "ERROR: package/libs/libzxpon-omci failed to
    # build" and nothing that said why.  The check is here so that a line
    # cannot lose the toolchain again without a gate noticing.
    print("\nthe OMCI engine's toolchain is installed on both lines")
    for workflow, doc in ((RELEASE, release), (BRINGUP, bringup)):
        text = run_text(doc, "build")
        failures.check("rustup toolchain install" in text,
                       f"{workflow} installs the Rust toolchain")
        failures.check("aarch64-unknown-linux-musl" in text,
                       f"{workflow} adds the aarch64 musl target")
        failures.check("ZX_CARGO=" in text,
                       f"{workflow} hands ZX_CARGO to the build")

    # The image's kernel is compressed by zopfli, not gzip: mkgzip.py does
    # "import zopfli.gzip" with no fallback, because gzip -9 cannot fit the
    # fixed 8 MiB kernel partition.  Without the binding the image stage
    # dies with a ModuleNotFoundError - after the kernel has been compiled,
    # which is the most expensive place to find out.
    print("\nthe 7.3 line has the tools the image stage needs")
    tools_text = step_run_text(bringup, "build", "The firmware tools")
    failures.check(tools_text != "", "the firmware tools step has a body")
    failures.check("zopfli==0.4.3" in tools_text,
                   "the 7.3 line installs zopfli")
    failures.check('SKD840N_HOST_PYTHON=$PY" >> "$GITHUB_ENV"' in tools_text,
                   "the 7.3 line tells the image profile which python to use")
    failures.check("spec_from_file_location" in tools_text,
                   "the 7.3 line checks that the image's own script imports")

    print("\nthe gate jobs only name tests that exist")
    for workflow, job in ((RELEASE, "check-led-gates"), (BRINGUP, "check-gates")):
        for test in referenced_tests(run_text(docs[workflow], job)):
            failures.check((ROOT / test).is_file(),
                           f"{workflow} runs {test}, which exists")

    # The zte drivers have to be built into the kernel.  cnjn's config-7.3
    # has CONFIG_ZTE_ZX279133_ETH=m because the reference firmware loads
    # npu.ko from /etc/modules-boot.d; nothing here has that loader, and
    # OpenWrt does not put an in-tree module into the rootfs unless a
    # kmod-* package declares it.  With =m the 7.3 image would come up with
    # no NPU in it at all - and the board's device tree names the 9132
    # switch node, which a module can never bind.
    #
    # The pin has to reach the fragment that merges last, and the gate has
    # to read the merged kernel .config rather than the fragment: a symbol
    # that does not exist in the tree, and a tristate symbol whose
    # dependency is modular, are both dropped in silence, so the fragment
    # can say =y while the kernel says =m.
    print("\nthe 7.3 line builds the zte drivers into the kernel")
    pin_text = step_run_text(bringup, "build", "Build the zte drivers into the kernel")
    failures.check(pin_text != "", "the pin step has a body")
    failures.check("target/linux/zte/zx279133/config-7.3" in pin_text,
                   "the pins go into the fragment that merges last")
    for symbol in ZTE_DRIVER_SYMBOLS:
        failures.check(symbol in pin_text,
                       f"the 7.3 line pins {symbol}=y")
    gate_text = step_run_text(bringup, "build",
                               "Gate: the zte drivers are built into the kernel")
    failures.check(gate_text != "", "the gate step has a body")
    failures.check(".config" in gate_text,
                   "the gate reads a merged kernel .config")
    failures.check("config-7.3" not in gate_text,
                   "the gate does not read the fragment it pinned")
    failures.check('grep -qx "CONFIG_$s=y"' in gate_text,
                   "the gate asserts the value is =y, not merely present")
    for symbol in ZTE_DRIVER_SYMBOLS:
        failures.check(symbol in gate_text,
                       f"the gate checks {symbol}")

    # The board's other requirements have to be in the merged kernel config
    # too, and the release line already knows how to say that.  Without this
    # the 7.3 line would build an image that has no Realtek PHY, no
    # netfilter and the wrong built-in command line - and every one of those
    # failures looks like "the kernel does not boot" on the board.
    #
    # The contract is that the 7.3 line asserts the *same* symbols the
    # release line asserts, not a copy of them: a copy drifts, and a drift
    # here is silent.  So the two lists are read out of the two workflows
    # and compared.
    print("\nthe 7.3 line asserts the release line's kernel requirements")
    board_gate = step_run_text(bringup, "build",
                               "Gate: the merged kernel config carries the board requirements")
    failures.check(board_gate != "", "the board requirements gate has a body")
    failures.check(".config" in board_gate,
                   "the board requirements gate reads a merged kernel .config")
    failures.check("config-7.3" not in board_gate,
                   "the board requirements gate does not read the fragment that was pinned")
    failures.check('grep -qx "CONFIG_$s=y"' in board_gate,
                   "the board requirements gate asserts the value is =y, not merely present")
    release_gate = run_text(release, "build")
    release_required = shell_list(release_gate, "required")
    bringup_required = shell_list(board_gate, "required")
    failures.check(bool(release_required),
                   "the release line's required list was found")
    failures.check(release_required == bringup_required,
                   "the 7.3 line asserts exactly the symbols the release line asserts"
                   f" ({len(release_required)} symbols)")
    if release_required and release_required != bringup_required:
        missing = sorted(set(release_required) - set(bringup_required))
        extra = sorted(set(bringup_required) - set(release_required))
        if missing:
            print(f"        missing on the 7.3 line: {' '.join(missing)}")
        if extra:
            print(f"        extra on the 7.3 line: {' '.join(extra)}")
    for symbol in ("REALTEK_PHY", "NF_FLOW_TABLE", "NFT_FLOW_OFFLOAD",
                   "CC_OPTIMIZE_FOR_SIZE"):
        failures.check(symbol in bringup_required,
                       f"the 7.3 line requires {symbol} to be built in")
    failures.check("# CONFIG_CC_OPTIMIZE_FOR_PERFORMANCE is not set" in board_gate,
                   "the board requirements gate refuses an -O2 kernel")
    failures.check("CONFIG_PANIC_TIMEOUT=0" in board_gate,
                   "the board requirements gate refuses a panicking-and-rebooting kernel")
    failures.check("root=/dev/mtdblock5" in board_gate,
                   "the board requirements gate checks the built-in command line")
    failures.check("mtdblock7" in board_gate,
                   "the board requirements gate rejects the SR1010 root device")

    # A device tree node whose "compatible" names a driver that was not
    # built is skipped silently by the kernel: the board boots, logs nothing
    # wrong, and simply has no network port.  The 7.3 line had no gate for
    # that, and it is the line where the switch driver, the PON stack and
    # the 9132 programming are our own code.
    #
    # What is asked is not "is the driver in the tree" but "is the driver in
    # the kernel that was built": a compatible string is in the image only
    # when the driver that registers it was compiled in.  So this gate is
    # also the one that would catch a driver of ours going back to being a
    # module - the mistake this line already made once.
    print("\nthe 7.3 line checks that the device tree's compatibles have drivers")
    compat_gate = step_run_text(
        bringup, "build", "Gate: every compatible the device tree names has a driver")
    failures.check(compat_gate != "", "the compatible gate has a body")
    failures.check("tools/check-sk-dtb-compat.py" in compat_gate,
                   "the compatible gate runs the repository's own tool")
    failures.check("arch/arm64/boot/Image" in compat_gate,
                   "the compatible gate reads the kernel that was built")
    failures.check("zx279133-sk-d840n.dts" in compat_gate,
                   "the compatible gate reads the board device tree")
    failures.check("--image" in compat_gate and "--dts" in compat_gate,
                   "the compatible gate hands the tool both the image and the tree")

    # The exception list is a decision, not an accident, so it is pinned
    # here: one entry, and it has to say why.  An exception without a
    # reason is how a gap becomes invisible.
    tool = ROOT / "tools" / "check-sk-dtb-compat.py"
    failures.check(tool.is_file(), "the compatible tool exists")
    exceptions = {}
    if tool.is_file():
        # The list is read out of the tool itself rather than parsed out of
        # its source: the tool is the thing that decides what is allowed to
        # have no driver, so a second copy of the list here could drift from
        # the one that actually runs.
        spec = importlib.util.spec_from_file_location("sk_dtb_compat", tool)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        exceptions = dict(getattr(module, "EXCEPTIONS", {}))
        failures.check(sorted(exceptions) == ["zte,zx279133-rgmii"],
                       "the exception list is exactly the RGMII node")
        for name, reason in sorted(exceptions.items()):
            failures.check(len(reason) > 80,
                           f"the exception for {name} says why")

    # The bring-up line's whole purpose is an image to put on the board.
    # Until the images are collected there is nothing to flash, so the line
    # that compiles the kernel has to hand the images out as well - and the
    # kernel has to fit the fixed 8 MiB partition the device tree names.
    print("\nthe 7.3 line produces an image that fits the board")
    collect_text = step_run_text(bringup, "build", "Collect the images")
    failures.check(collect_text != "", "the collect step has a body")
    failures.check("bin/targets/zte/zx279133" in collect_text,
                   "the collect step takes the images OpenWrt built")
    failures.check("-le 8388608" in collect_text,
                   "the collect step checks the kernel against the 8 MiB partition")
    upload = [step for step in steps_of(bringup, "build")
              if str(step.get("name") or "") == "Upload the images"]
    failures.check(bool(upload), "the 7.3 line has an upload step")
    failures.check(any(str(step.get("uses") or "").startswith("actions/upload-artifact")
                       for step in upload),
                   "the upload step uses actions/upload-artifact")

    # The packages the board needs have to be in the image, not merely
    # selected: a package that defconfig keeps can still fail to install,
    # and an image without the protocol daemon is an image the board comes
    # up with nothing to talk to.
    print("\nthe 7.3 line checks the image carries the packages the board needs")
    package_gate = step_run_text(
        bringup, "build", "Gate: the image carries the packages the board needs")
    failures.check(package_gate != "", "the package gate has a body")
    failures.check(".manifest" in package_gate,
                   "the package gate reads the image manifest")
    # The list is read out of the loop, not searched for as a string: the
    # package names also appear in the gate's own error message, so a plain
    # substring search would pass even after the list had lost them - which
    # is exactly what the first version of this check did, and the reverse
    # check caught it.
    listed = []
    loop = re.search(r"for package in ([^;]+);", package_gate)
    if loop:
        listed = loop.group(1).split()
    failures.check(bool(listed), "the package gate has a package list")
    for package in ("zxpond", "libzxpon-omci", "firewall4", "nftables"):
        failures.check(package in listed,
                       f"the package gate requires {package} in the image")

    # The 7.3 line had no download cache, and that is not only a speed
    # question: one of the packages it needs is fetched with git, and run
    # #14 (head 0af2270) failed on exactly that while the release line's
    # run #364 at the same commit succeeded.  The two lines clone the same
    # OpenWrt tree, so the same cache serves both - which only works if
    # the key is the same.
    print("\nthe 7.3 line caches the downloads the way the release line does")
    bringup_cache = [step for step in steps_of(bringup, "build")
                      if str(step.get("name") or "") == "Cache dl/"]
    release_cache = [step for step in steps_of(release, "build")
                     if str(step.get("name") or "") == "Cache dl/"]
    failures.check(bool(bringup_cache), "the 7.3 line has a download cache")
    failures.check(bool(release_cache), "the release line has a download cache")
    if bringup_cache and release_cache:
        bringup_with = bringup_cache[0].get("with") or {}
        release_with = release_cache[0].get("with") or {}
        failures.check(bringup_with.get("path") == release_with.get("path"),
                       "both lines cache the same directory")
        failures.check(bringup_with.get("key") == release_with.get("key"),
                       "both lines use the same cache key, so they share it")

    # The Compile step has to say which package failed and has to look for
    # a failed download: a package that never got as far as being compiled
    # has no log of its own, and the words it uses are not the compiler's.
    print("\nthe 7.3 line's Compile step says which package failed and why")
    compile_text = step_run_text(bringup, "build", "Compile")
    failures.check(compile_text != "", "the Compile step has a body")
    failures.check('logs/$(basename' in compile_text,
                   "the Compile step reads the failing package's own log")
    failures.check("could not resolve" in compile_text,
                   "the Compile step looks for a failed download or clone")
    failures.check("failed to build" in compile_text,
                   "the Compile step names the package that failed")

    # The board trial needs the three partition files, and the only honest
    # way to get them is to take them out of the image the build produced:
    # the release line's own tool validates the sysupgrade (fwtool metadata,
    # CRC, board compatibility, member sizes, JFFS2 magic, and that the
    # uImage decompresses to exactly the Image that was built) and exports
    # the members verbatim.
    print("\nthe 7.3 line validates the image and exports the partitions")
    export_text = step_run_text(bringup, "build", "Validate and export the partitions")
    failures.check(export_text != "", "the export step has a body")
    failures.check("check_sysupgrade.py" in export_text,
                   "the export step validates the sysupgrade image")
    failures.check("arch/arm64/boot/Image" in export_text,
                   "the export step compares against the kernel that was built")
    for member in ("flash-uImage", "flash-dtb.bin", "flash-rootfs.jffs2"):
        failures.check(member in export_text,
                       f"the export step writes {member}")

    return failures.report("workflows")


if __name__ == "__main__":
    sys.exit(main())
