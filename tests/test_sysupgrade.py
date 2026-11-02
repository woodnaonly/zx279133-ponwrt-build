#!/usr/bin/env python3
"""Self-contained firmware/upgrade tests; no device, build tree or old fixtures.

Requires zopfli==0.4.3, PyYAML and a POSIX shell (Git for Windows is supported).
All files and simulated MTD writes stay in a TemporaryDirectory.
"""
import contextlib
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import zlib

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import check_sysupgrade as firmware
import mkgzip

PLATFORM = REPO / "overlays/target/linux/zte/zx279133/base-files/lib/upgrade/platform.sh"
WORKFLOW = REPO / ".github/workflows/build-sk-d840n.yml"
SHELL = shutil.which("sh")
if not SHELL and Path("C:/Program Files/Git/bin/sh.exe").is_file():
    SHELL = "C:/Program Files/Git/bin/sh.exe"


def shell_path(path):
    path = Path(path).as_posix()
    # GNU tar treats C:/... as a remote archive, unlike cat/cp. Use MSYS paths.
    if os.name == "nt" and len(path) > 2 and path[1] == ":":
        return "/" + path[0].lower() + path[2:]
    return path


def trailer(data, payload, kind):
    body = data + payload
    return body + struct.pack(">IIB3xI", 0x46577830,
                              zlib.crc32(body) ^ 0xFFFFFFFF, kind, len(payload) + 16)


def metadata():
    return {"metadata_version": "1.1", "compat_version": "1.0",
            "supported_devices": ["skyworth,sk-d840n", "zte,133"],
            "version": {"dist": "ImmortalWrt", "version": "test",
                        "board": firmware.BOARD, "target": "zte/zx279133"}}


def uimage(raw=b"self-contained ARM64 kernel" * 32):
    payload = gzip.compress(raw, mtime=0)
    header = struct.pack(">7I4B32s", 0x27051956, 0, 0, len(payload),
                         0x80200000, 0x80200000, zlib.crc32(payload),
                         5, 22, 2, 1, b"test")
    return header[:4] + struct.pack(">I", zlib.crc32(header)) + header[8:] + payload


def members():
    return {"CONTROL": b"BOARD=skyworth_sk-d840n\n", "kernel": uimage(),
            "dtb": bytes.fromhex("d00dfeed") + struct.pack(">I", 40) + bytes(32),
            "root": bytes.fromhex("8519") + bytes(126)}


def make_image(path, parts=None, *, compressed=False, signed=False,
               extra=None, meta=None):
    parts = members() if parts is None else parts
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as archive:
        for name, value in [*parts.items(), *(extra or [])]:
            if isinstance(value, tarfile.TarInfo):
                archive.addfile(value)
            else:
                info = tarfile.TarInfo(firmware.PREFIX + "/" + name)
                info.size = len(value)
                archive.addfile(info, io.BytesIO(value))
    data = trailer(out.getvalue(), bytes(8) + json.dumps(meta or metadata()).encode(), 1)
    if signed:
        data = trailer(data, b"test certificate, not a real signature", 0)
    if compressed:
        # Legacy callers gzip the whole native image, metadata included.
        data = gzip.compress(data, mtime=0)
    path.write_bytes(data)
    return path


class TemporaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="skd840n-upgrade-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.image = self.directory / "image.bin"


class ArtifactTests(TemporaryTest):
    def test_export_is_byte_identical_and_preserves_native_trailers(self):
        for signed in (False, True):
            with self.subTest(signed=signed):
                make_image(self.image, signed=signed)
                before = self.image.read_bytes()
                blobs, report = firmware.inspect_image(self.image)
                output = self.directory / "export"
                firmware.export_image(self.image, output, blobs)
                for name, filename in firmware.EXPORTS.items():
                    self.assertEqual((output / filename).read_bytes(), blobs[name])
                for filename in (firmware.MAIN_IMAGE, "flash-sysupgrade.bin"):
                    self.assertEqual((output / filename).read_bytes(), before)
                self.assertEqual(self.image.read_bytes(), before)
                self.assertEqual(report["native_signature_present"], signed)
                self.assertEqual(report["metadata"], metadata())

    def test_member_size_boundaries(self):
        parts = members()
        make_image(self.image, parts)
        for part in firmware.LIMITS:
            for spare in (1, 0, -1):
                with self.subTest(part=part, spare=spare), mock.patch.dict(
                        firmware.LIMITS, {part: len(parts[part]) + spare}):
                    if spare < 0:
                        with self.assertRaisesRegex(ValueError, part + ": size=.*capacity="):
                            firmware.inspect_image(self.image)
                    else:
                        firmware.inspect_image(self.image)

    def test_real_eight_megabyte_limit_rejects_oversized_kernel(self):
        parts = members()
        parts["kernel"] = bytes(8388609)
        make_image(self.image, parts)
        with self.assertRaisesRegex(ValueError, "kernel: size=8388609 capacity=8388608"):
            firmware.inspect_image(self.image)

    def test_missing_empty_duplicate_and_link_members(self):
        for part in firmware.LIMITS:
            for defect in ("missing", "empty", "duplicate", "symlink", "directory"):
                with self.subTest(part=part, defect=defect):
                    parts = members()
                    extra = []
                    if defect == "missing":
                        del parts[part]
                    elif defect == "empty":
                        parts[part] = b""
                    elif defect == "duplicate":
                        extra = [(part, parts[part])]
                    else:
                        info = tarfile.TarInfo(firmware.PREFIX + "/" + part)
                        info.type = tarfile.SYMTYPE if defect == "symlink" else tarfile.DIRTYPE
                        info.linkname = "/etc/passwd"
                        parts[part] = info
                    make_image(self.image, parts, extra=extra)
                    with self.assertRaises(ValueError):
                        firmware.inspect_image(self.image)

    def test_crc_and_payload_validation(self):
        for offset, message in ((4, "header CRC"), (64, "data CRC")):
            parts = members()
            broken = bytearray(parts["kernel"])
            broken[offset] ^= 1
            parts["kernel"] = bytes(broken)
            make_image(self.image, parts)
            with self.assertRaisesRegex(ValueError, message):
                firmware.inspect_image(self.image)
        make_image(self.image)
        raw = self.directory / "Image"
        raw.write_bytes(b"wrong kernel")
        with self.assertRaisesRegex(ValueError, "built raw Image"):
            firmware.inspect_image(self.image, raw)
        raw.write_bytes(gzip.decompress(members()["kernel"][64:]))
        firmware.inspect_image(self.image, raw)

    def test_bad_fwtool_metadata_or_crc(self):
        make_image(self.image)
        data = bytearray(self.image.read_bytes())
        data[1000] ^= 1
        self.image.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "fwtool CRC"):
            firmware.inspect_image(self.image)
        for key, value in (("supported_devices", ["other-board"]),
                           ("compat_version", "2.0")):
            meta = metadata()
            meta[key] = value
            make_image(self.image, meta=meta)
            with self.assertRaises(ValueError):
                firmware.inspect_image(self.image)

    def test_wrong_blob_magic_and_board(self):
        for part, value in (("CONTROL", b"BOARD=other\n"),
                            ("dtb", bytes(40)), ("root", bytes(128))):
            parts = members()
            parts[part] = value
            make_image(self.image, parts)
            with self.assertRaises(ValueError):
                firmware.inspect_image(self.image)

    def test_cli_selects_exact_board_and_refuses_ambiguity(self):
        image = self.directory / "test-skyworth_sk-d840n-jffs2-128k-sysupgrade.bin"
        make_image(image)
        command = [sys.executable, str(REPO / "tools/check_sysupgrade.py"),
                   "--source-dir", str(self.directory), "--export-dir",
                   str(self.directory / "out")]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        make_image(self.directory / ("second-" + image.name))
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("found 2", result.stderr)


class CompressionTests(TemporaryTest):
    def test_actual_zopfli_round_trip(self):
        source, output = self.directory / "Image", self.directory / "out.gz"
        raw = b"the same raw kernel\n" * 100
        source.write_bytes(raw)
        with mock.patch.object(sys, "argv", ["mkgzip", str(source), str(output), "1"]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mkgzip.main(), 0)
        self.assertEqual(gzip.decompress(output.read_bytes()), raw)

    def test_header_is_included_and_oversize_output_is_not_written(self):
        source, output = self.directory / "Image", self.directory / "out.gz"
        raw = b"kernel"
        source.write_bytes(raw)
        payload = gzip.compress(raw)
        for spare in (1, 0, -1):
            target = output.with_name("out" + str(spare))
            with self.subTest(spare=spare), \
                    mock.patch.object(mkgzip.zopfli.gzip, "compress", return_value=payload), \
                    mock.patch.object(mkgzip, "PARTITION", len(payload) + 64 + spare), \
                    mock.patch.object(sys, "argv", ["mkgzip", str(source), str(target)]), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(mkgzip.main(), 1 if spare < 0 else 0)
                self.assertEqual(target.exists(), spare >= 0)


@unittest.skipUnless(SHELL, "a POSIX shell is required")
class KernelSizeTests(TemporaryTest):
    @classmethod
    def setUpClass(cls):
        configure = (REPO / "scripts/configure-sk-d840n.sh").read_text(encoding="utf-8")
        # Exercise the implementation configure uses, not a copy in the test.
        signature = "skd840n_pin_kernel_size() ("
        cls.helper = signature + configure.split(signature, 1)[1].split("\n)", 1)[0] + "\n)\n"

    def pin(self, config, prefix=""):
        return subprocess.run(
            [SHELL, "-c", self.helper + 'skd840n_pin_kernel_size "$1" "$2"',
             "pin-kernel-size", shell_path(config), prefix],
            capture_output=True, text=True, timeout=10)

    def test_both_layers_replace_conflicts_and_preserve_other_options(self):
        for prefix in ("", "KERNEL_"):
            with self.subTest(prefix=prefix):
                config = self.directory / "kernel.config"
                other_prefix = "" if prefix else "KERNEL_"
                kept = ("# preserve drivers and diagnostics\n"
                        f"CONFIG_{prefix}REALTEK_PHY=y\n"
                        f"CONFIG_{prefix}DYNAMIC_DEBUG=y\n"
                        f"CONFIG_{other_prefix}CC_OPTIMIZE_FOR_PERFORMANCE=y\n"
                        f"CONFIG_{prefix}CC_OPTIMIZE_FOR_SIZE_HINT=y\n")
                conflicts = ""
                for symbol in ("SIZE", "PERFORMANCE"):
                    name = f"CONFIG_{prefix}CC_OPTIMIZE_FOR_{symbol}"
                    conflicts += "".join(f"{name}={value}\n" for value in ("y", "m", "n", "y"))
                    conflicts += f"# {name} is not set\n"
                config.write_text(kept + conflicts, encoding="utf-8", newline="\n")
                result = self.pin(config, prefix)
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = (kept + f"CONFIG_{prefix}CC_OPTIMIZE_FOR_SIZE=y\n"
                            f"# CONFIG_{prefix}CC_OPTIMIZE_FOR_PERFORMANCE is not set\n")
                self.assertEqual(config.read_text(encoding="utf-8"), expected)
                before = config.read_bytes()
                result = self.pin(config, prefix)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(config.read_bytes(), before, "pin must be idempotent")

    def test_build_root_override_keeps_size_optimization(self):
        fragment, build = self.directory / "fragment", self.directory / ".config"
        for config, prefix in ((fragment, ""), (build, "KERNEL_")):
            config.write_text(f"CONFIG_{prefix}CC_OPTIMIZE_FOR_PERFORMANCE=y\n",
                              encoding="utf-8")
            result = self.pin(config, prefix)
            self.assertEqual(result.returncode, 0, result.stderr)
        # Match kernel-defaults.mk: build-root KERNEL_ lines follow the fragments.
        merged = (fragment.read_text(encoding="utf-8") +
                  build.read_text(encoding="utf-8").replace("CONFIG_KERNEL_", "CONFIG_"))
        states = {}
        for line in merged.splitlines():
            if line.startswith("CONFIG_"):
                symbol, value = line.split("=", 1)
                states[symbol] = value
            elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
                states[line.split()[1]] = "n"
        self.assertEqual(states, {"CONFIG_CC_OPTIMIZE_FOR_SIZE": "y",
                                  "CONFIG_CC_OPTIMIZE_FOR_PERFORMANCE": "n"})

    def test_missing_choices_are_added_and_missing_file_is_rejected(self):
        config = self.directory / "kernel.config"
        result = self.pin(config)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing kernel config", result.stderr)
        self.assertFalse(config.exists())
        config.write_text("CONFIG_REALTEK_PHY=y\n", encoding="utf-8")
        result = self.pin(config)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(config.read_text(encoding="utf-8"),
                         "CONFIG_REALTEK_PHY=y\nCONFIG_CC_OPTIMIZE_FOR_SIZE=y\n"
                         "# CONFIG_CC_OPTIMIZE_FOR_PERFORMANCE is not set\n")
        before = config.read_bytes()
        result = self.pin(config, "WRONG_")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(config.read_bytes(), before)


@unittest.skipUnless(SHELL, "a POSIX shell is required")
class PlatformTests(TemporaryTest):
    def test_partition_sizes_come_from_live_mtd_aliases(self):
        proc = self.directory / "proc-mtd"
        sysfs = self.directory / "sysfs"
        records = []
        for index, (name, size) in zip((2, 3, 5), firmware.LIMITS.items()):
            records.append(f'mtd{index}: {size:08x} 00020000 "{name}"')
            target = sysfs / ("mtd" + str(index))
            target.mkdir(parents=True)
            (target / "size").write_text(str(size))
        proc.write_text("\n".join(records) + "\n")
        text = PLATFORM.read_text(encoding="utf-8").replace(". /lib/functions.sh", ":")
        self.assertIn('RAMFS_COPY_BIN="${RAMFS_COPY_BIN:-} mktemp"', text)
        text = text.replace("/proc/mtd", shlex.quote(shell_path(proc)))
        text = text.replace("/sys/class/mtd", shell_path(sysfs))
        script = self.directory / "lookup.sh"
        script.write_text(text, encoding="utf-8", newline="\n")
        command = ". " + shlex.quote(shell_path(script)) + "; "
        command += "skd840n_part_size kernel; skd840n_part_size dtb; skd840n_part_size root"
        result = subprocess.run([SHELL, "-c", command], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([int(s) for s in result.stdout.split()], list(firmware.LIMITS.values()))

    def run_platform(self, action, *, capacities=None, backup=None, default_backup=False):
        capacities = capacities or {"kernel": 1024, "dtb": 512, "root": 2048}
        script = self.directory / "platform.sh"
        config = self.directory / "sysupgrade.tgz"
        text = PLATFORM.read_text(encoding="utf-8").replace(". /lib/functions.sh", ":")
        script.write_text(text.replace("/tmp/sysupgrade.tgz", shell_path(config)),
                          encoding="utf-8", newline="\n")
        if default_backup:
            config.write_bytes(b"saved default configuration")
        log = self.directory / "mtd.log"
        log.write_bytes(b"")
        written = self.directory / "written"
        written.mkdir(exist_ok=True)
        quote = lambda p: shlex.quote(shell_path(p))
        cases = "\n".join(f"{name}) echo {size} ;;" for name, size in capacities.items())
        command = f"""
set -u
. {quote(script)}
v() {{ printf '%s\\n' "$*" >&2; }}
command -v hexdump >/dev/null 2>&1 || hexdump() {{ od -An -tx1 -N2 | tr -d ' \\n'; }}
skd840n_part_size() {{ case "$1" in
{cases}
*) return 1 ;; esac; }}
mtd() {{
    printf '%s\\n' "$*" >> {quote(log)}
    prev=; src=; label=
    for arg in "$@"; do
        [ "$prev" != write ] || src=$arg
        prev=$arg; label=$arg
    done
    cp "$src" {quote(written)}/"$label"
}}
sync() {{ :; }}
UPGRADE_BACKUP={quote(backup) if backup else "''"}
{action} {quote(self.image)}
"""
        env = dict(os.environ, TMPDIR=shell_path(self.directory))
        result = subprocess.run([SHELL, "-c", command], env=env,
                                capture_output=True, text=True, timeout=30)
        return result, log.read_text().splitlines()

    def assert_rejected_before_write(self, parts, **image_options):
        make_image(self.image, parts, **image_options)
        for action in ("platform_check_image", "platform_do_upgrade"):
            result, writes = self.run_platform(action)
            self.assertNotEqual(result.returncode, 0, (action, result.stdout, result.stderr))
            self.assertEqual(writes, [], result.stderr)

    def test_plain_and_gzip_tars_and_write_order(self):
        for compressed in (False, True):
            with self.subTest(compressed=compressed):
                make_image(self.image, compressed=compressed)
                result, writes = self.run_platform("platform_check_image")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(writes, [])
                result, writes = self.run_platform("platform_do_upgrade")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([w.split()[-1] for w in writes], ["root", "dtb", "kernel"])
                for part, blob in members().items():
                    if part != "CONTROL":
                        self.assertEqual((self.directory / "written" / part).read_bytes(), blob)

    def test_all_member_size_boundaries(self):
        for part in firmware.LIMITS:
            for spare in (1, 0, -1):
                with self.subTest(part=part, spare=spare):
                    parts = members()
                    capacities = {"kernel": 1024, "dtb": 512, "root": 2048}
                    capacities[part] = len(parts[part]) + spare
                    make_image(self.image, parts)
                    for action in ("platform_check_image", "platform_do_upgrade"):
                        result, writes = self.run_platform(action, capacities=capacities)
                        if spare < 0:
                            self.assertNotEqual(result.returncode, 0)
                            self.assertEqual(writes, [])
                            self.assertIn(f"{part} size={len(parts[part])} capacity=", result.stderr)
                        else:
                            self.assertEqual(result.returncode, 0, result.stderr)

    def test_real_eight_megabyte_boundary(self):
        for size in (8388607, 8388608, 8388609):
            parts = members()
            parts["kernel"] = b"K" * size
            make_image(self.image, parts)
            result, writes = self.run_platform("platform_do_upgrade", capacities=firmware.LIMITS)
            self.assertEqual(result.returncode == 0, size <= 8388608, result.stderr)
            if size > 8388608:
                self.assertEqual(writes, [])

    def test_missing_empty_duplicates_and_special_files(self):
        for part in firmware.LIMITS:
            for defect in ("missing", "empty", "duplicate", "symlink", "hardlink", "directory", "fifo"):
                with self.subTest(part=part, defect=defect):
                    parts, extra = members(), []
                    if defect == "missing":
                        del parts[part]
                    elif defect == "empty":
                        parts[part] = b""
                    elif defect == "duplicate":
                        extra = [(part, parts[part])]
                    else:
                        info = tarfile.TarInfo(firmware.PREFIX + "/" + part)
                        info.type = {"symlink": tarfile.SYMTYPE, "hardlink": tarfile.LNKTYPE,
                                     "directory": tarfile.DIRTYPE, "fifo": tarfile.FIFOTYPE}[defect]
                        info.linkname = firmware.PREFIX + "/CONTROL"
                        parts[part] = info
                    self.assert_rejected_before_write(parts, extra=extra)

    def test_wrong_board_path_and_unavailable_partition(self):
        parts = members()
        parts["CONTROL"] = b"BOARD=other\n"
        self.assert_rejected_before_write(parts)
        for path in ("../outside", "/tmp/outside", "unexpected"):
            info = tarfile.TarInfo(path)
            info.size = 0
            self.assert_rejected_before_write(members(), extra=[("ignored", info)])
        make_image(self.image)
        result, writes = self.run_platform("platform_do_upgrade", capacities={"kernel": 1024})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(writes, [])

    def test_config_archive_is_passed_to_mtd(self):
        make_image(self.image)
        backup = self.directory / "custom-backup.tgz"
        backup.write_bytes(b"saved configuration")
        for options in ({"backup": backup}, {"default_backup": True}):
            result, writes = self.run_platform("platform_do_upgrade", **options)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(writes[0].startswith("-j "), writes)
            self.assertEqual([w.split()[-1] for w in writes], ["root", "dtb", "kernel"])


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        cls.steps = {s.get("name", ""): s for s in cls.workflow["jobs"]["build"]["steps"]}

    def test_kernel_size_choice_is_pinned_at_both_layers_and_checked_after_merge(self):
        configure = (REPO / "scripts/configure-sk-d840n.sh").read_text(encoding="utf-8")
        defconfig = configure.index("\nmake defconfig\n")
        self.assertLess(configure.index('skd840n_pin_kernel_size "$KCFG"'), defconfig)
        self.assertGreater(configure.index("skd840n_pin_kernel_size .config KERNEL_"), defconfig)
        gate = self.steps["Assert the kernel really has the PHY driver"]["run"]
        required = next(line for line in gate.splitlines() if line.startswith('required="'))
        self.assertIn("CC_OPTIMIZE_FOR_SIZE", shlex.split(required.split("=", 1)[1])[0].split())
        self.assertIn("grep -qx '# CONFIG_CC_OPTIMIZE_FOR_PERFORMANCE is not set' \"$KC\"", gate)

    def test_native_kernel_is_the_only_flash_pipeline(self):
        recipe = (REPO / "overlays/target/linux/zte/image/Makefile").read_text()
        self.assertIn("KERNEL = kernel-bin | skd840n-gzip | uImage gzip", recipe)
        self.assertIn("KERNEL_SIZE := 8192k", recipe)
        self.assertIn("$(SKD840N_HOST_PYTHON) -u", recipe)
        self.assertIn("KERNEL_INITRAMFS = kernel-bin | gzip |", recipe)
        package = self.steps["Package flash images"]["run"]
        self.assertIn("tools/check_sysupgrade.py", package)
        self.assertNotIn("tools/mkgzip.py", package)
        self.assertNotIn('"$MKIMAGE"', package)
        prepare = self.steps["Prepare firmware tools and upgrade regression tests"]["run"]
        self.assertIn("zopfli==0.4.3", prepare)
        self.assertIn("SKD840N_HOST_PYTHON=$PY", prepare)
        self.assertIn("tests/test_sysupgrade.py", prepare)

    def test_success_selection_and_validation_gate_all_firmware_uploads(self):
        for name in ("Upload outputs", "Upload direct sysupgrade image", "Publish release",
                     "Direct firmware download summary"):
            condition = self.steps[name]["if"]
            for wanted in ("flash", "initramfs", "both"):
                for mode in ("flash", "initramfs"):
                    for success in (False, True):
                        for validated in (False, True):
                            selected = wanted in (mode, "both")
                            expression = condition.replace("success()", repr(success))
                            values = {"steps.pick.outputs.run": "true" if selected else "false",
                                      "matrix.mode": mode,
                                      "steps.package_flash.outputs.validated": "true" if validated and mode == "flash" else "",
                                      "steps.package_initramfs.outputs.validated": "true" if validated and mode == "initramfs" else ""}
                            for key, value in values.items():
                                expression = expression.replace(key, repr(value))
                            expression = expression.replace("&&", " and ").replace("||", " or ")
                            actual = eval(expression, {"__builtins__": {}})
                            expected = success and selected and validated
                            if name in ("Upload direct sysupgrade image", "Direct firmware download summary"):
                                expected = expected and mode == "flash"
                            self.assertEqual(actual, expected, (name, wanted, mode, success, validated))

    def test_direct_image_and_maintenance_delivery(self):
        upload = self.steps["Upload direct sysupgrade image"]["with"]
        self.assertIn(firmware.MAIN_IMAGE, upload["path"].splitlines())
        self.assertEqual(upload["compression-level"], 0)
        release = self.steps["Publish release"]["with"]
        for filename in (firmware.MAIN_IMAGE, firmware.MAIN_IMAGE + ".sha256", "flash-set.tgz",
                         "flash-sysupgrade.bin", "sk-d840n-initramfs.itb"):
            self.assertIn(filename, release["files"].splitlines())
        self.assertIn("无需解压", release["body"])
        self.assertIn("ZIP", release["body"])
        self.assertIn("releases/download/skd840n_flash_", self.steps["Direct firmware download summary"]["run"])
        triggers = self.workflow.get("on", self.workflow.get(True))
        self.assertIn("tests/test_sysupgrade.py", triggers["push"]["paths"])

    def test_release_manifest_and_native_metadata_pipeline(self):
        import prepush
        with mock.patch.object(prepush, "REPO", str(REPO)), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(prepush.release_manifest_check(), 0)
        recipe = (REPO / "overlays/target/linux/zte/image/Makefile").read_text()
        self.assertIn("| append-metadata", recipe)
        configure = (REPO / "scripts/configure-sk-d840n.sh").read_text()
        self.assertIn('cp "$SCRIPT_DIR/../tools/mkgzip.py" scripts/skd840n-mkgzip.py', configure)

    @unittest.skipUnless(SHELL, "a POSIX shell is required")
    def test_workflow_shell_syntax_and_github_script_length(self):
        for name, step in self.steps.items():
            if "run" not in step:
                continue
            with self.subTest(step=name):
                self.assertLessEqual(len(step["run"]), 21000)
                result = subprocess.run([SHELL, "-n"], input=step["run"],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
