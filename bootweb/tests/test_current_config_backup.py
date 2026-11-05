"""Configuration-export admission is offline; no serial/network is opened."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from current_config_backup import clean_backup
from host.uart import TrialError


class CurrentConfigBackupTests(unittest.TestCase):
    def evidence(self):
        native = {key: True for key in ("backup_pass", "native_return_pass", "alias_restored",
                                       "cache_restored", "full_flash_backup")}
        native.update(native_write_enabled=False, backup_sha256="a" * 64, installed_sha256="b" * 64)
        audit = {"snapshot_audit_pass": True, "sha256": "a" * 64, "installed_sha256": "b" * 64}
        return native, audit

    def test_complete_clean_return_and_identity_binding_are_required(self):
        native, audit = self.evidence()
        clean_backup(native, audit)
        for key in ("backup_pass", "native_return_pass", "alias_restored", "cache_restored", "full_flash_backup"):
            with self.subTest(key=key), self.assertRaises(TrialError):
                clean_backup({**native, key: False}, audit)
        for key, value in (("native_write_enabled", True), ("backup_sha256", "c" * 64),
                           ("installed_sha256", "c" * 64)):
            with self.subTest(key=key), self.assertRaises(TrialError):
                clean_backup({**native, key: value}, audit)
        with self.assertRaises(TrialError):
            clean_backup(native, {**audit, "snapshot_audit_pass": False})


if __name__ == "__main__":
    unittest.main()
