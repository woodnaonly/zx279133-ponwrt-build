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
    # The host compiler check and the shadow headers it needs: the build
    # runs them, so a change to either of them changes what the build
    # checks and has to start a build.
    "tools/kcheck.py",
    "tools/kshadow.py",
    "tests/test_sk_kcheck.py",
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


def drivers_we_touch():
    """The .c files this repository puts into the 7.3 tree.

    Two sources: the patch series, which changes files that are already in
    the tree, and the board layer, which adds new ones.  Both are read
    from the repository rather than written down, so a new driver cannot
    appear without the list following it.
    """
    touched = set()
    patches = ROOT / "kernel-patches" / "7.3-local"
    for patch in sorted(patches.glob("*.patch")):
        for line in patch.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r"^\+\+\+ b/(\S+\.c)$", line)
            if match:
                touched.add(match.group(1))
    overlay = ROOT / "kernel-overlays-73"
    for path in sorted(overlay.rglob("*.c")):
        relative = path.relative_to(overlay).as_posix()
        touched.add(relative)
    return touched


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

    # The Compile step has to say which package failed.  It used to look for
    # the package's own log under logs/ and for the words a failed download
    # uses, and both of those were wrong: OpenWrt writes logs/<pkg>.txt only
    # when CONFIG_BUILD_LOG=y, which this build does not set, and the
    # compiler's own words are not in the top level log at all - OpenWrt
    # throws them away unless V=s is set.
    print("\nthe 7.3 line's Compile step says which package failed")
    compile_text = step_run_text(bringup, "build", "Compile")
    failures.check(compile_text != "", "the Compile step has a body")
    failures.check("failed to build" in compile_text,
                   "the Compile step names the package that failed")
    failures.check("logs/$(basename" not in compile_text,
                   "the Compile step does not read a log that is never written")
    failures.check("could not resolve" not in compile_text,
                   "the Compile step does not look for download words in a log that has none")

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

    # The images are fetched by hand: the artifact's listing is public but
    # its content needs a session, so the file that comes down has to be
    # verifiable before it is written to a board.  The manifest has to be
    # built from the exported files and checked there and then.
    print("\nthe exported images carry a manifest of their own")
    failures.check("sha256sum" in export_text,
                   "the export step writes a sha256 manifest")
    failures.check("sha256sum -c" in export_text,
                   "the export step checks the manifest it wrote")
    failures.check("images/sha256sums" in export_text or
                   "cd images" in export_text,
                   "the manifest ends up in the artifact")

    # The host compiler checks the drivers before the build spends an hour
    # on them.  The list of files it checks has to be the list of the
    # files this repository touches - the .c files the patch series
    # changes and the .c files the board layer adds - so a new driver
    # cannot slip past the check without anybody noticing.
    print("\nthe host compiler checks every driver this repository touches")
    failures.check((ROOT / "tools" / "kcheck.py").is_file(),
                   "the compiler check is in the repository")
    failures.check((ROOT / "tools" / "kshadow.py").is_file(),
                   "the shadow headers the check needs are in the repository")
    kcheck_steps = [step for step in steps_of(bringup, "build")
                    if "tools/kcheck.py" in str(step.get("run") or "")]
    failures.check(bool(kcheck_steps),
                   "the 7.3 line runs the compiler check")
    if kcheck_steps:
        names = step_names(bringup, "build")
        failures.check(names.index(str(kcheck_steps[0].get("name") or "")) <
                       names.index("Compile"),
                       "the compiler check runs before the Compile step")
        body = str(kcheck_steps[0].get("run") or "")
        listed = set(re.findall(r"--file\s+(\S+)", body))
        failures.check(bool(listed),
                       "the check names the files it checks")
        failures.check(listed == drivers_we_touch(),
                       "the check names exactly the drivers this repository touches")
        failures.check('--tree "$KERNEL_TREE"' in body,
                       "the check runs on the tree the build uses")
        for fragment in ("openwrt/target/linux/generic/config-7.3",
                         "openwrt/target/linux/zte/config-7.3",
                         "openwrt/target/linux/zte/zx279133/config-7.3"):
            failures.check(fragment in body,
                           f"the check reads the configuration {fragment}")

    # A failed build has to say why.  OpenWrt throws every sub-make's
    # stdout and stderr away unless V=s is set (include/verbose.mk), so the
    # parallel pass alone cannot: the diagnostic is a second pass, serial
    # and verbose, over the target that failed.  The 7.3 line's run #25
    # (45040260008) is the run that made this necessary - it died in
    # package/libs/libbpf and the only thing that could be said about it
    # was "exit code 2".
    print("\na failed build says why it failed")
    compile_steps = [step for step in steps_of(bringup, "build")
                     if str(step.get("name") or "") == "Compile"]
    failures.check(bool(compile_steps), "the 7.3 line still has a Compile step")
    if compile_steps:
        body = str(compile_steps[0].get("run") or "")
        # The shell script is read twice: once whole, for the sentences it
        # makes, and once without its comment lines, for the commands it
        # runs.  A comment may say what a command says, but it is not the
        # command.  The commands are also joined first, because the
        # condition that decides whether the package pass found the reason
        # is written across two lines with a shell continuation.
        flat = body.replace("\\\n", " ")
        commands = "\n".join(ln for ln in flat.splitlines()
                             if not ln.strip().startswith("#"))
        failures.check("make -j1 V=s" in body,
                       "the Compile step runs a serial verbose pass")
        # The verbose pass is aimed at the package that failed, and the
        # target is package/<path>/compile.  The "package/" is not
        # optional: the top level has no rule for <path>/compile, it falls
        # into toplevel.mk's catch-all (%::), and the pass says "No rule to
        # make target" instead of the compiler's words.  Run 33's pass
        # asked for the bare path, got exactly that message, and reported
        # the absence of a rule instead of the reason.  The check reads
        # the target out of the command and compares it, so the target
        # written in a comment cannot pass for the target of the command.
        #
        # It is aimed at the kernel as well, and the kernel is not a
        # package: it is built by target/linux/compile, and when it fails
        # the top level says "ERROR: target/linux failed to build." - a
        # line the package pattern does not match.  Without the kernel
        # branch the pass falls through to the whole tree, which walks the
        # package order again from the top and whose ceiling expires long
        # before the kernel - the 6.18 line's runs 88, 89, 91 and 92 are
        # exactly that failure.  There are exactly two targets, and the
        # check compares the whole list, so a third one turns it red too.
        raw = flat.splitlines()
        targets = re.findall(r"make[ \t]+(?:[^\s\"]+[ \t]+)*?([^\s\"]+)/compile",
                             commands)
        failures.check(sorted(targets) == ["package/${FAILED}", "target/linux"],
                       "the verbose passes are aimed at the failed package and at the kernel")
        # The line that says the kernel failed is matched as a fixed string,
        # and the check runs that string against the line OpenWrt actually
        # prints: a pattern that mentions the kernel but does not match the
        # line would pass a string comparison and still never fire.
        kernel_line = "   ERROR: target/linux failed to build."
        fixed = re.findall(r"grep[ \t]+-qaF[ \t]+'([^']*)'", commands)
        failures.check(any(k in kernel_line and "target/linux" in k for k in fixed),
                       "the Compile step recognises the kernel's own failure line")
        # The kernel is asked for first.  If the package branch came first
        # it would spend the whole ceiling on a consequence of the kernel
        # failing, and the kernel would never be diagnosed.  This says
        # something only when both branches are there; whether the kernel
        # branch is there at all is the list check above.
        kernel_branch = [i for i, ln in enumerate(raw)
                         if '"$KERNEL_FAILED" = yes' in ln]
        package_branch = [i for i, ln in enumerate(raw)
                          if '-n "$FAILED"' in ln]
        failures.check(
            not (kernel_branch and package_branch)
            or kernel_branch[0] < package_branch[0],
            "the kernel is diagnosed before the package")
        # The patterns that name an error are in two places and they are
        # not the same list: the condition that decides whether the
        # package pass found the reason, and the pattern that picks the
        # lines to re-emit as annotations.  The condition is the one that
        # decides whether the whole tree gets the same treatment, so it
        # is read on its own.
        conditions = re.findall(r"\bif[ \t]+(.*?);[ \t]*then", commands)
        fallback = [c for c in conditions if "No rule to make target" in c]
        failures.check(bool(fallback),
                       "a pass that never reached the package counts as no error named")
        named = " ".join(re.findall(r"grep[ \t]+-[a-zA-Z]*E[a-zA-Z]*[ \t]+\"([^\"]+)\"",
                                    " ".join(fallback)))
        failures.check("Error [0-9]+" not in named,
                       "make's own 'Error 2' does not count as an error being named")
        # The compiler says "error:" and make says "Error 1", and OpenWrt
        # says "build failed"; a pattern that knows only the lower case
        # misses the other two.
        patterns = " ".join(re.findall(r'grep[ \t]+-[a-zA-Z]*E[a-zA-Z]*[ \t]+"([^"]+)"',
                                       commands))
        failures.check("Error [0-9]+" in patterns,
                       "the error pattern knows make's own wording")
        failures.check("build failed" in patterns,
                       "the error pattern knows OpenWrt's own wording")
        failures.check("::error::" in body,
                       "the failure is re-emitted as an annotation")
        failures.check("GITHUB_STEP_SUMMARY" in body,
                       "the diagnostic also goes into the step summary")
        # The old step said "the package has no log of its own: it never got
        # as far as being compiled".  That was our own sentence and it was
        # wrong: OpenWrt writes logs/<pkg>.txt only when
        # CONFIG_BUILD_LOG=y, and this build does not set it, so the
        # absence of that file says nothing about the package.
        failures.check("never got as far as being compiled" not in body,
                       "the step does not claim a package was never compiled")
        failures.check("logs/$(basename" not in body,
                       "the step does not read a log that is never written")

    # The two lines clone the same repository but not the same commit, so the
    # shared dl/ cache is a cache and not a copy of one build's downloads.
    # The check is on the two commits themselves and not on a phrase in a
    # comment.
    print("\nthe two lines build different trees")
    release_env = (release or {}).get("env") or {}
    bringup_env = (bringup or {}).get("env") or {}
    failures.check(bool(release_env.get("IW_URL")) and
                   release_env.get("IW_URL") == bringup_env.get("IW_URL"),
                   "both lines clone the same repository")
    failures.check(bool(release_env.get("IW_SHA")) and
                   release_env.get("IW_SHA") != bringup_env.get("IW_SHA"),
                   "the two lines build different trees")
    failures.check("the same IW_URL and IW_SHA" not in
                   (WORKFLOWS / BRINGUP).read_text(encoding="utf-8"),
                   "the 7.3 workflow does not claim the two lines share a commit")

    # The libbpf fix for the 7.3 kernel's arm64 uapi headers.  Run 45107547379
    # (job 137472302996) died in package/libs/libbpf with
    #
    #   staging_dir/toolchain-.../include/asm/ptrace.h:96:9:
    #       error: unknown type name '__u128'; did you mean '__u32'?
    #
    # The 7.3.4 arm64 asm/ptrace.h uses __u128, and the copy of linux/types.h
    # that libbpf compiles against - its own, because its -I../include comes
    # first - does not define it.  The fix lives in overlays-73/ and nowhere
    # else, so the 6.18 release line is not touched.
    #
    # The checks read the patch, not a phrase about it: the file it patches,
    # and the line it adds.  A patch that carries the words __u128 in its
    # description and changes nothing else would pass a substring check and
    # still leave the build broken.
    print("\nthe libbpf fix for the 7.3 line is a real patch")
    libbpf_patch = (ROOT / "overlays-73" / "package" / "libs" / "libbpf" / "patches" /
                    "110-arm64-u128-types.patch")
    failures.check(libbpf_patch.is_file(),
                   "the libbpf fix is in overlays-73/")
    if libbpf_patch.is_file():
        patch_lines = libbpf_patch.read_text(encoding="utf-8").splitlines()
        targets = [ln for ln in patch_lines if ln.startswith("+++ b/")]
        failures.check(targets == ["+++ b/include/linux/types.h"],
                       "the libbpf fix patches libbpf's own include/linux/types.h")
        added = [ln for ln in patch_lines
                 if ln.startswith("+") and not ln.startswith("+++")]
        removed = [ln for ln in patch_lines
                   if ln.startswith("-") and not ln.startswith("---")]
        failures.check(
            "+typedef unsigned __int128 __u128 __attribute__((aligned(16)));" in added,
            "the libbpf fix defines __u128 the way the uapi linux/types.h does")
        failures.check(
            "+#ifdef __SIZEOF_INT128__" in added and "+#endif" in added,
            "the libbpf fix guards the 128-bit types the way the uapi header does")
        # The same shadowing hides __attribute_const__, which the exported uapi
        # linux/swab.h uses and the kernel defines in a header it does not
        # export.  Without it the build moves one error further down the same
        # file.
        failures.check(
            "+#define __attribute_const__ __attribute__((__const__))" in added,
            "the libbpf fix defines __attribute_const__ the way the kernel does")
        failures.check(not removed,
                       "the libbpf fix only adds lines")
    # The release line must not get the fix: it builds libbpf against the
    # 6.18 headers, where the same fields are spelled __uint128_t.
    release_patches = sorted((ROOT / "overlays" / "package" / "libs" / "libbpf").rglob("*.patch"))
    failures.check(not release_patches,
                   "the 6.18 release line carries no libbpf patch")
    # The workflow has to check the patch the same way, or the two drift and
    # the build dies in libbpf again while the gate says the tree is fine.
    overlay_steps = [step for step in steps_of(bringup, "build")
                     if str(step.get("name") or "") == "Apply the OpenWrt-side overlays"]
    failures.check(bool(overlay_steps),
                   "the 7.3 line still applies the OpenWrt-side overlays")
    if overlay_steps:
        flat = str(overlay_steps[0].get("run") or "").replace("\\\n", " ")
        commands = "\n".join(ln for ln in flat.splitlines()
                             if not ln.strip().startswith("#"))
        assigned = re.findall(r"^\s*LIBBPF_PATCH=(\S+)", commands, re.M)
        failures.check(
            assigned == ["openwrt/package/libs/libbpf/patches/110-arm64-u128-types.patch"],
            "the workflow checks the libbpf patch at the path the overlay puts it")
        failures.check('test -f "$LIBBPF_PATCH"' in commands,
                       "the workflow checks that the libbpf patch is in the tree")
        patterns = re.findall(r"grep[ \t]+-qx[ \t]+'([^']*)'[ \t]+\"\$LIBBPF_PATCH\"",
                               commands)
        failures.check(
            patterns == ["+++ b/include/linux/types.h",
                         "+typedef unsigned __int128 __u128 __attribute__((aligned(16)));",
                         "+#define __attribute_const__ __attribute__((__const__))"],
            "the workflow checks the patched file and the added lines, not a phrase")

    # The partition table inside the dtb is what decides where root is:
    # U-Boot's bootargs hard-code root=/dev/mtdblock5, and the board's
    # table puts root at 0xf00000 with the final 241 MiB.  A table that is
    # off by one erase block writes the jffs2 where the new kernel will not
    # look for it - a panic with no serial to read, and the one failure
    # mode a flashed image cannot explain afterwards.
    #
    # The 6.18 line has this gate; the 7.3 line did not, and the 7.3
    # line's image is the one that is going to be flashed next.  The check
    # reads the blob and compares the arguments it asks for with the ones
    # the board needs - not whether the word "native241" appears somewhere.
    print("\nthe dtb that is going to be flashed carries the board's partition table")
    dtb_step = [step for step in steps_of(bringup, "build")
                if str(step.get("name") or "") ==
                "Gate: the board device tree compiles against the 7.3 tree"]
    export_step = [step for step in steps_of(bringup, "build")
                   if str(step.get("name") or "") == "Validate and export the partitions"]
    failures.check(bool(dtb_step) and bool(export_step),
                   "the 7.3 line still has the dtb gate and the export step")
    wanted = [("layout=native241", "root=0xf00000,0xf100000")]
    if dtb_step:
        body = str(dtb_step[0].get("run") or "")
        flat = body.replace("\\\n", " ")
        commands = "\n".join(ln for ln in flat.splitlines()
                             if not ln.strip().startswith("#"))
        calls = re.findall(r"tools/dtbparts\.py[ \t]+(\S+)[ \t]+(\S+)[ \t]+(\S+)",
                           commands)
        failures.check(
            calls == [("/tmp/sk-d840n.dtb", "layout=native241",
                       "root=0xf00000,0xf100000")],
            "the dtb gate checks the board's partition layout")
    if export_step:
        body = str(export_step[0].get("run") or "")
        flat = body.replace("\\\n", " ")
        commands = "\n".join(ln for ln in flat.splitlines()
                             if not ln.strip().startswith("#"))
        calls = re.findall(r"tools/dtbparts\.py[ \t]+(\S+)[ \t]+(\S+)[ \t]+(\S+)",
                           commands)
        failures.check(
            calls == [("flash-dtb.bin", "layout=native241",
                       "root=0xf00000,0xf100000")],
            "the export step checks the board's partition layout")

    # The migration note's increment table is the measured one, and the
    # workflow contract test parses that table and adds the numbers up.
    # It does not look for the number 171 in the text, because that is
    # what the table used to say.
    print("\nthe migration note's increment table adds up")
    note = ROOT / "docs" / "101-the-7-3-4-baseline-and-the-migration-surface-20261122.md"
    failures.check(note.exists(), "the 7.3 migration note is in the repository")
    failures.check((ROOT / "tools" / "func-port-coverage.py").exists(),
                   "the tool that accounts for every function is in the repository")
    if note.exists():
        note_text = note.read_text(encoding="utf-8")
        parts = note_text.split("### 3.5 ")
        failures.check(len(parts) > 1, "the note has the increment section")
        if len(parts) > 1:
            section = parts[1].split("### 3.6 ")[0]
            counts = []
            for line in section.splitlines():
                row = re.match(r"^\|\s*`([^`]+)`\s*\|\s*(\d+)\s*\|", line)
                if row:
                    counts.append((row.group(1), int(row.group(2))))
            failures.check(len(counts) >= 10,
                           "the table lists every file, not only the big ones")
            failures.check(sum(count for _, count in counts) == 171,
                           "the table adds up to the measured total of 171")
            failures.check("tools/func-port-coverage.py" in note_text,
                           "the note names the tool that accounts for the functions")

    return failures.report("workflows")


if __name__ == "__main__":
    sys.exit(main())
