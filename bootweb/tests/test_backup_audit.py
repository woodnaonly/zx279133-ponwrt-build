"""Saved snapshot integrity gates, with no device, file repair or flash writes."""
import hashlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.backup import CHUNK, ERASE
from host.backup_audit import audit_capture, audit_stream, REGIONS


class SavedBackupTests(unittest.TestCase):
    def fixture(self):
        data = b"A"*CHUNK + b"\xff"*CHUNK
        chunks = [{"offset": offset, "bytes": CHUNK,
                   "sha256": hashlib.sha256(data[offset:offset+CHUNK]).hexdigest(),
                   "bad_blocks": [CHUNK+ERASE] if offset else []}
                  for offset in (0, CHUNK)]
        return data, chunks

    def test_file_hash_and_partial_boundary_regions_are_recomputed(self):
        data, chunks = self.fixture()
        ranges = {"head": (0, ERASE), "crossing": (CHUNK-ERASE, CHUNK+ERASE), "tail": (CHUNK, 2*CHUNK)}
        report = audit_stream(io.BytesIO(data), chunks, len(data), ranges)
        self.assertEqual(report["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(report["bad_blocks"], [CHUNK+ERASE])
        for name, (start, end) in ranges.items():
            self.assertEqual(report["regions"][name]["sha256"], hashlib.sha256(data[start:end]).hexdigest())

    def test_short_extra_or_corrupt_saved_data_fail(self):
        data, chunks = self.fixture()
        for changed in (data[:-1], data+b"X", b"B"+data[1:]):
            with self.subTest(length=len(changed)), self.assertRaises(ValueError):
                audit_stream(io.BytesIO(changed), chunks, len(data), {})

    def test_gaps_duplicates_and_badblock_metadata_fail(self):
        data, chunks = self.fixture()
        for invalid in (chunks[:-1], [chunks[1], chunks[0]], [chunks[0], chunks[0]], [chunks[0], None]):
            with self.assertRaises(ValueError):
                audit_stream(io.BytesIO(data), invalid, len(data), {})
        for blocks in ([CHUNK+1], [0], [CHUNK, CHUNK], ["1048576"], None):
            invalid = [chunks[0], {**chunks[1], "bad_blocks": blocks}]
            with self.assertRaises(ValueError):
                audit_stream(io.BytesIO(data), invalid, len(data), {})
        chunks[0]["bad_blocks"] = [0]
        with self.assertRaisesRegex(ValueError, "not FF"):
            audit_stream(io.BytesIO(data), chunks, len(data), {})

    def test_partial_or_flags_only_report_cannot_be_a_full_live_snapshot(self):
        for manifest in ({}, {"capture_complete": True, "full_flash_backup": True, "bytes": 2*CHUNK}):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                audit_capture(io.BytesIO(), manifest, {"full_flash_backup": True})
        manifest = {"schema": "sk-d840n-physical-ecc-backup-v1", "capture_complete": True,
                    "full_flash_backup": True, "protected_repeat_pass": True, "bytes": 0x10000000,
                    "expected_bytes": 0x10000000, "nand_written": False, "oob_included": False,
                    "badblock_policy": "physical-offsets-FF-placeholder-with-explicit-bitmap"}
        with self.assertRaisesRegex(ValueError, "clean native"):
            audit_capture(io.BytesIO(), manifest, {"full_flash_backup": True})

    def bound_fixture(self):
        digest = "a"*64
        streamed = {"bytes": 0x10000000, "sha256": digest, "bad_blocks": [],
                    "regions": {name: {"sha256": digest} for name in REGIONS}}
        manifest = {"schema": "sk-d840n-physical-ecc-backup-v1", "capture_complete": True,
                    "full_flash_backup": True, "protected_repeat_pass": True, "bytes": 0x10000000,
                    "expected_bytes": 0x10000000, "nand_written": False, "oob_included": False,
                    "badblock_policy": "physical-offsets-FF-placeholder-with-explicit-bitmap",
                    "sha256": digest, "bad_blocks": [], "chunks": [],
                    "protected_repeat_sha256": {"boot": digest, "tags": digest}}
        native = {"schema": "sk-d840n-native-ram-web-v1", "full_flash_backup": True,
                  "nand_readonly_verified": True, "native_return_pass": True, "watchdog_verified": True,
                  "alias_restored": True, "cache_restored": True, "native_write_enabled": False,
                  "linux_userspace_used": False, "backup_sha256": digest,
                  "nand_hashes": {phase+"/"+name: digest for phase in ("before", "repeat", "after")
                                  for name in ("boot", "tags")}}
        return streamed, manifest, native

    def test_completed_binding_still_cannot_claim_configuration_rescue_or_writes(self):
        streamed, manifest, native = self.bound_fixture()
        source = io.BytesIO()
        with patch("host.backup_audit.audit_stream", return_value=streamed) as audit:
            report = audit_capture(source, manifest, native)
        audit.assert_called_once_with(source, manifest["chunks"], 0x10000000, REGIONS)
        self.assertTrue(report["snapshot_audit_pass"])
        for key in ("configuration_exported", "serial_rescue_verified", "nand_written", "flashable_release",
                    "vendor_abi_matches_known_baseline", "btl_matches_known_baseline"):
            self.assertIs(report[key], False)

    def test_changed_session_whole_repeat_or_protected_hashes_fail(self):
        for changed in ("session", "file", "badblocks", "repeat", "native-before", "native-repeat", "native-after"):
            streamed, manifest, native = self.bound_fixture()
            if changed == "session":
                native["backup_sha256"] = "b"*64
            elif changed == "file":
                streamed["sha256"] = "b"*64
            elif changed == "badblocks":
                manifest["bad_blocks"] = [0]
            elif changed == "repeat":
                manifest["protected_repeat_sha256"]["tags"] = "b"*64
            else:
                native["nand_hashes"][changed.split("-")[1]+"/boot"] = "b"*64
            with self.subTest(changed=changed), patch("host.backup_audit.audit_stream", return_value=streamed), \
                    self.assertRaises(ValueError):
                audit_capture(io.BytesIO(), manifest, native)

    def test_unclean_native_return_is_never_replaced_by_full_capture_flags(self):
        for key in ("nand_readonly_verified", "native_return_pass", "watchdog_verified", "alias_restored", "cache_restored"):
            _, manifest, native = self.bound_fixture()
            native[key] = False
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "clean native"):
                audit_capture(io.BytesIO(), manifest, native)


if __name__ == "__main__":
    unittest.main()
