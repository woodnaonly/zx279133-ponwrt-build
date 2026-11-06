"""Synthetic CI packaging fixtures never provide hardware/installation evidence."""
import copy
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import ci
import ci_profile
from host.core import BASE
from host.image import regions
from package import make_uboot


class CIProfileTests(unittest.TestCase):
    def test_static_profile_never_claims_a_probe_reset_test_or_authorization(self):
        with patch.object(ci_profile, "public_boot"):
            policy = ci_profile.load_ci_profile(ci_profile.PROFILE, b"synthetic-reference")
        self.assertTrue(policy["ci_offline_build"])
        self.assertFalse(policy["hardware_acceptance_complete"])
        self.assertFalse(policy["installation_authorized"])
        self.assertFalse(policy["reset_static_configuration"]["physical_reset_pass"])
        self.assertNotIn("ram_probe_pass", policy)
        self.assertNotIn("physical_acceptance_waived", policy)

    def test_changed_abi_layout_reset_mapping_or_acceptance_is_rejected(self):
        original = ci.read_json(ci_profile.PROFILE)
        faults = ({"layout": "current240"}, {"vendor_abi_sha256": "0" * 64},
                  {"control_dtb_sha256": "0" * 64}, {"installation_authorized": True},
                  {"hardware_acceptance_complete": True}, {"web_uboot_enabled": True},
                  {"reset_pinctrl_configuration": {"register": "0x0"}}, {"extra": "not-allowed"})
        with tempfile.TemporaryDirectory(prefix="bootweb-ci-profile-") as directory:
            path = Path(directory) / "profile.json"
            for fault in faults:
                with self.subTest(fault=fault), patch.object(ci_profile, "public_boot"):
                    path.write_text(json.dumps({**original, **fault}), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        ci_profile.load_ci_profile(path, b"synthetic-reference")

    def test_full_public_reference_and_stage2_hash_are_both_mandatory(self):
        data, stage2 = b"synthetic-reference", b"synthetic-stage2"
        base = ci.sha(stage2)
        lock = {"public_boot": {"bytes": len(data), "sha256": ci.sha(data), "stage2_sha256": base}}
        with patch.object(ci_profile, "BASE", base), patch.object(ci_profile, "regions", return_value={"stage2": stage2}):
            self.assertEqual(ci_profile.public_boot(data, lock), lock["public_boot"])
            for changed in (data[:-1], data[:-1] + b"x"):
                with self.assertRaises(ValueError):
                    ci_profile.public_boot(changed, lock)
            changed_lock = copy.deepcopy(lock)
            changed_lock["public_boot"]["stage2_sha256"] = "0" * 64
            with self.assertRaises(ValueError):
                ci_profile.public_boot(data, changed_lock)

    def test_hardware_probe_and_ci_entry_are_explicitly_mutually_exclusive(self):
        with tempfile.TemporaryDirectory(prefix="bootweb-ci-cli-") as directory:
            output = Path(directory) / "must-not-exist"
            command = [sys.executable, "-X", "utf8", str(ROOT / "tools/build_native_resident.py"),
                       "--build-dir", str(output), "--reference-boot", "missing.bin", "--native241-continuous"]
            for arguments in ([], ["--probe-report", "missing.json", "--ci-offline-profile", str(ci_profile.PROFILE)]):
                with self.subTest(arguments=arguments):
                    result = subprocess.run(command + arguments, capture_output=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(output.exists())
            result = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "tools/build_resident.py"),
                "--payload-dir", "missing", "--build-dir", str(output), "--ci-offline-profile", str(ci_profile.PROFILE),
                "--reset-evidence", "missing.json"], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())

    def test_ci_mode_rejects_device_backups_before_reading_them(self):
        with tempfile.TemporaryDirectory(prefix="bootweb-ci-no-backup-") as directory:
            output = Path(directory) / "must-not-exist"
            result = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "tools/build_native_resident.py"),
                "--build-dir", str(output), "--current-backup", "missing-private-backup.bin", "--native241-continuous",
                "--ci-offline-profile", str(ci_profile.PROFILE)], capture_output=True, text=True, encoding="utf-8")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no device backups", result.stderr)
            self.assertNotIn("FileNotFoundError", result.stderr)
            self.assertFalse(output.exists())

    def test_download_and_lwip_hash_mismatches_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="bootweb-ci-download-") as directory:
            path = Path(directory) / "dependency.zip"
            path.write_bytes(b"damaged-cache")
            with patch.object(ci.urllib.request, "urlopen") as network, self.assertRaisesRegex(ValueError, "SHA256"):
                ci.download("https://invalid.example/never-requested", path, "0" * 64)
            network.assert_not_called()
        expected = {"version": "2.2.0", "source_and_headers_sha256": "a" * 64,
                    "license_sha256": "b" * 64, "hashed_file_count": 181}
        for key in expected:
            actual = {**expected, key: "wrong"}
            with self.subTest(key=key), patch.object(ci, "dependency", return_value=actual), self.assertRaises(ValueError):
                ci.check_lwip(Path(directory), {"lwip": expected})


class CICandidateTests(unittest.TestCase):
    def fixture(self):
        reference = b"B" * 0x20000 + b"S" * 0x160000 + b"E" * 0x20000 + b"T" * 0x60000
        candidate = reference[:0x20000] + b"C" * 0x160000 + reference[0x180000:]
        digest = ci.sha(regions(candidate)["stage2"])
        manifest = {"source_installed_sha256": BASE, "installed_sha256": digest, "boot_sha256": ci.sha(candidate),
            "ci_offline_build": True, "hardware_acceptance_complete": False, "installation_authorized": False,
            "flashable_release": False, "native241_service": True, "reset_cold_entry_enabled": True,
            "native_image_format_offline_pass": True, "reset_verified": False,
            "payload_build_policy": {"ci_offline_build": True, "web_uboot_enabled": False,
                "periodic_budget_verified": False, "timing_ledger": {"TIMING_PROVEN": 0},
                "layout": "native241", "power_mode": "continuous_required"}}
        result = SimpleNamespace(kind=2, count=1,
            writes=[SimpleNamespace(offset=0x20000, capacity=0x160000, sha256=digest.encode())],
            report=lambda: {"hardware_verified": False, "flashable_release": False})
        core = SimpleNamespace(resident_identity=lambda data: SimpleNamespace(installed_sha256=BASE.encode()),
                               validate_identity=lambda data, identity: result)
        return reference, candidate, manifest, core, result

    def test_only_stage2_is_exported_and_protection_changes_are_rejected(self):
        reference, candidate, manifest, core, result = self.fixture()
        package = make_uboot(reference, candidate, core=core)
        with patch.object(ci, "public_boot"):
            self.assertFalse(ci.check_candidate(package, reference, candidate, manifest, core)["hardware_verified"])
            for offset in (0, 0x180000, 0x1a0000):
                damaged = bytearray(candidate)
                damaged[offset] ^= 1
                with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, "protected"):
                    ci.check_candidate(package, reference, bytes(damaged), manifest, core)
            result.writes[0].offset = 0
            with self.assertRaisesRegex(ValueError, "write window"):
                ci.check_candidate(package, reference, candidate, manifest, core)

    def test_ci_metadata_cannot_be_converted_into_hardware_acceptance(self):
        reference, candidate, manifest, core, _ = self.fixture()
        package = make_uboot(reference, candidate, core=core)
        for key, value in (("hardware_acceptance_complete", True), ("installation_authorized", True),
                           ("flashable_release", True), ("reset_verified", True),
                           ("reset_physical_evidence", {"physical_reset_pass": True}),
                           ("user_directed_unverified_reset", {"physical_acceptance_waived": True})):
            with self.subTest(key=key), patch.object(ci, "public_boot"), self.assertRaises(ValueError):
                ci.check_candidate(package, reference, candidate, {**manifest, key: value}, core)


class CIDistTests(unittest.TestCase):
    def fixture(self, directory):
        dist = Path(directory)
        reference, candidate, manifest, core, _ = CICandidateTests().fixture()
        package = make_uboot(reference, candidate, core=core)
        verification = {"offline_pass": True, "tests": {"run": 1, "failed": 0, "errors": 0, "skipped": 0}}
        files = {"README.md": b"synthetic offline fixture", "manifest.json": json.dumps(manifest).encode(),
                 "offline-verification.json": json.dumps(verification).encode(),
                 "resident-native-policy.json": json.dumps(manifest["payload_build_policy"]).encode(),
                 "instruction-audits.json": b"{}", "source-provenance.json": b"{}"}
        for name in ci.TOOLKIT_REQUIRED:
            if name != "toolkit-sha256sums":
                files.setdefault(name, b"synthetic offline fixture")
        (dist / ci.CANDIDATE).write_bytes(package)
        self.write_toolkit(dist, files)
        return dist, files

    def write_toolkit(self, dist, files):
        contents = dict(files)
        contents["toolkit-sha256sums"] = "".join(ci.sha(data) + "  " + name + "\n" for name, data in sorted(files.items())).encode()
        (dist / ci.TOOLKIT).write_bytes(gzip.compress(ci.archive_bytes({ci.PREFIX + n: d for n, d in contents.items()}), mtime=0))
        self.checksums(dist)

    def checksums(self, dist):
        (dist / ci.CHECKSUMS).write_text("".join(ci.sha((dist / name).read_bytes()) + "  " + name + "\n"
                                                for name in (ci.CANDIDATE, ci.TOOLKIT)), encoding="utf-8", newline="\n")

    def test_expected_release_files_validate_but_missing_or_extra_outputs_fail(self):
        with tempfile.TemporaryDirectory(prefix="bootweb-ci-dist-") as directory:
            dist, _ = self.fixture(directory)
            self.assertEqual(set(ci.validate_dist(dist)), set(ci.RELEASE_FILES))
            for name in ci.RELEASE_FILES:
                saved = (dist / name).read_bytes()
                (dist / name).unlink()
                with self.subTest(missing=name), self.assertRaises(ValueError):
                    ci.validate_dist(dist)
                (dist / name).write_bytes(saved)
            (dist / "resident-boot-development.bin").write_bytes(b"not-an-allowed-output")
            with self.assertRaises(ValueError):
                ci.validate_dist(dist)

    def test_corrupt_candidate_or_toolkit_and_rewritten_outer_checksums_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="bootweb-ci-corrupt-") as directory:
            dist, _ = self.fixture(directory)
            for name in (ci.CANDIDATE, ci.TOOLKIT):
                saved = (dist / name).read_bytes()
                damaged = bytearray(saved)
                damaged[len(damaged) // 2] ^= 1
                (dist / name).write_bytes(damaged)
                with self.subTest(name=name), self.assertRaises(ValueError):
                    ci.validate_dist(dist)
                (dist / name).write_bytes(saved)
            with tarfile.open(dist / ci.CANDIDATE, "r:") as archive:
                files = {m.name: archive.extractfile(m).read() for m in archive.getmembers()}
            files["uboot.bin"] = b"x" + files["uboot.bin"][1:]
            (dist / ci.CANDIDATE).write_bytes(ci.archive_bytes(files))
            self.checksums(dist)
            with self.assertRaises(ValueError):
                ci.validate_dist(dist)

    def test_failed_skipped_or_missing_reports_never_allow_publication(self):
        with tempfile.TemporaryDirectory(prefix="bootweb-ci-report-") as directory:
            dist, files = self.fixture(directory)
            for key in ("failed", "errors", "skipped"):
                changed = copy.deepcopy(files)
                report = json.loads(changed["offline-verification.json"])
                report["tests"][key] = 1
                changed["offline-verification.json"] = json.dumps(report).encode()
                self.write_toolkit(dist, changed)
                with self.subTest(key=key), self.assertRaises(ValueError):
                    ci.validate_dist(dist)
            changed = dict(files)
            del changed["instruction-audits.json"]
            self.write_toolkit(dist, changed)
            with self.assertRaises(ValueError):
                ci.validate_dist(dist)

    def test_archive_paths_and_toolkit_integrity_are_checked(self):
        with tempfile.TemporaryDirectory(prefix="bootweb-ci-paths-") as directory:
            dist, files = self.fixture(directory)
            for name in ("../outside", ".build/private-backup.bin", "debug\\bad.bin"):
                self.write_toolkit(dist, {**files, name: b"must not publish"})
                with self.subTest(name=name), self.assertRaises(ValueError):
                    ci.validate_dist(dist)
            self.write_toolkit(dist, files)
            with tarfile.open(dist / ci.TOOLKIT, "r:gz") as archive:
                changed = {m.name: archive.extractfile(m).read() for m in archive.getmembers()}
            changed[ci.PREFIX + "README.md"] = b"corrupt after checksums"
            (dist / ci.TOOLKIT).write_bytes(gzip.compress(ci.archive_bytes(changed), mtime=0))
            self.checksums(dist)
            with self.assertRaisesRegex(ValueError, "toolkit checksum"):
                ci.validate_dist(dist)

    def test_missing_build_artifact_cannot_be_silently_omitted_from_toolkit(self):
        with tempfile.TemporaryDirectory(prefix="bootweb-ci-missing-") as directory:
            path = Path(directory)
            with self.assertRaises(FileNotFoundError):
                ci.toolkit_files(path, path, path, path)

    def test_full_boot_image_or_changed_runtime_policy_never_leaves_the_toolkit(self):
        with tempfile.TemporaryDirectory(prefix="bootweb-ci-private-") as directory:
            dist, files = self.fixture(directory)
            self.write_toolkit(dist, {**files, "debug/resident-boot-development.bin": b"do not publish"})
            with self.assertRaisesRegex(ValueError, "unexpected toolkit"):
                ci.validate_dist(dist)
            changed = dict(files)
            policy = json.loads(changed["resident-native-policy.json"])
            policy["web_uboot_enabled"] = True
            changed["resident-native-policy.json"] = json.dumps(policy).encode()
            self.write_toolkit(dist, changed)
            with self.assertRaises(ValueError):
                ci.validate_dist(dist)


if __name__ == "__main__":
    unittest.main()
