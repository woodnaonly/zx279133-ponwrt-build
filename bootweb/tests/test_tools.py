"""Offline tool tests use synthetic images only; never reference device paths."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.core import Core, ValidationError
from host.image import analyze, regions
from fixtures import nand_seed


def module(name):
    sys.path.insert(0, str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location("bootweb_tool_" + name, ROOT / "tools" / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class ToolTests(unittest.TestCase):
    def test_package_generator_protected_regions_and_determinism(self):
        generator = module("package")
        core, seed = Core(testing=True), nand_seed()
        candidate = seed[:0x20000] + b"C" * 0x160000 + seed[0x180000:]
        result = generator.make_uboot(seed, candidate, core=core)
        self.assertEqual(result, generator.make_uboot(seed, candidate, core=core))
        checked = core.validate(result)
        self.assertEqual(checked.writes[0].sha256.decode(), hashlib.sha256(b"C" * 0x160000).hexdigest())
        for at, name in ((0, "stage1"), (0x180000, "environment"), (0x1a0000, "tail")):
            changed = bytearray(candidate)
            changed[at] ^= 1
            with self.assertRaisesRegex(ValidationError, name):
                generator.make_uboot(seed, bytes(changed), core=core)
        with self.assertRaisesRegex(ValidationError, "adapted community"):
            generator.make_uboot(candidate, candidate, core=core)
        # CLI/default path cannot smuggle a synthetic fingerprint into production.
        with self.assertRaisesRegex(ValidationError, "adapted community"):
            generator.make_uboot(seed, candidate)
        with self.assertRaisesRegex(ValueError, "exactly 2 MiB"):
            regions(seed[:-1])

    def test_package_cli_rejects_existing_output_and_fake_base(self):
        # An inspect/make error must not truncate a pre-existing output file.
        with tempfile.TemporaryDirectory(prefix="bootweb-cli-") as directory:
            directory = Path(directory)
            seed, output = directory / "boot.bin", directory / "candidate.tar"
            seed.write_bytes(nand_seed())
            output.write_bytes(b"KEEP-ME")
            process = subprocess.run([sys.executable, str(ROOT / "tools/package.py"), "make-uboot",
                                      "--current-boot", str(seed), "--candidate-boot", str(seed),
                                      "--output", str(output)], capture_output=True, text=True, timeout=20)
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(output.read_bytes(), b"KEEP-ME")
            self.assertIn("adapted community", process.stderr)
            with self.assertRaises(FileExistsError):
                module("package").write_candidate(output, b"MUST-NOT-OVERWRITE")
            self.assertEqual(output.read_bytes(), b"KEEP-ME")

    def test_recovery_size_hard_limit_does_not_authorize_partition(self):
        checker = module("check_recovery_size")
        self.assertEqual(checker.PARTITION, 0x400000)
        self.assertEqual(checker.MAX_IMAGE, 0x3c0000)
        with tempfile.TemporaryDirectory(prefix="bootweb-size-") as directory:
            path = Path(directory) / "candidate.itb"
            for size, expected in ((0x3bffff, True), (0x3c0000, True), (0x3c0001, False), (0x400000, False)):
                path.write_bytes(bytes.fromhex("d00dfeed00000028") + bytes(size - 8))
                result = checker.check(path)
                self.assertEqual(result["size_pass"], expected)
                self.assertFalse(result["partition_creation_allowed"])
                self.assertFalse(result["flashable_release"])
            path.write_bytes(bytes.fromhex("d00dfeed00400000") + bytes(32))
            with self.assertRaisesRegex(ValueError, "truncated"):
                checker.check(path)

    def test_profile_or_static_report_never_unlocks_hardware(self):
        profile = json.loads((ROOT / "profiles/svn308776.json").read_text(encoding="utf-8"))
        profile["stage2_sha256"] = hashlib.sha256(b"B" * 0x160000).hexdigest()
        profile["native_write_enabled"] = profile["runtime_verified"] = True
        result = analyze(nand_seed(), profile)
        self.assertTrue(result["recognized_code"])
        self.assertFalse(result["native_write_enabled"])
        self.assertFalse(result["runtime_verified"])

    def test_elf_manifest_checks_machine_and_entry_but_never_certifies_ram(self):
        verifier = module("verify")
        ident = b"\x7fELF\x02\x01\x01" + bytes(9)
        header = struct.pack("<16sHHIQQQIHHHHHH", ident, 2, 183, 1, 0x89000000,
                             64, 0, 0, 64, 56, 1, 0, 0, 0)
        program = struct.pack("<IIQQQQQQ", 1, 5, 120, 0x89000000, 0x89000000, 4, 4, 4)
        image = header + program + bytes.fromhex("c0035fd6")
        result = verifier.elf_info(image)
        self.assertEqual(result["entry"], "0x89000000")
        self.assertFalse(result["ram_placement_verified"])
        self.assertFalse(result["vendor_container"])
        for at, fmt, value in ((18, "<H", 62), (24, "<Q", 0x80000000),
                               (32, "<Q", 0), (54, "<H", 0xffff), (56, "<H", 65)):
            changed = bytearray(image)
            struct.pack_into(fmt, changed, at, value)
            with self.assertRaises(ValueError):
                verifier.elf_info(changed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
