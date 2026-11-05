"""Installed-observation gates only; synthetic fixtures are not cold-boot evidence."""
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from resident_live import installed_binding, verify_web_state, VERSION
from host.core import BASE
from host.uart import TrialError


class InstalledObservationTests(unittest.TestCase):
    def fixture(self):
        candidate = b"\xff" * 0x200000
        installed = hashlib.sha256(candidate[0x20000:0x180000]).hexdigest()
        raw = json.dumps({"installed_sha256": installed,
                          "boot_sha256": hashlib.sha256(candidate).hexdigest(),
                          "native241_service": True, "power_mode": "continuous_required"}).encode()
        actual = {"installer_actual_pass": True, "actual_write_and_readback_pass": True,
                  "protected_audit_pass": True, "candidate_sha256": installed,
                  "resident_manifest_sha256": hashlib.sha256(raw).hexdigest()}
        return raw, candidate, actual

    def test_observation_requires_exact_actual_installation_not_a_candidate(self):
        raw, candidate, actual = self.fixture()
        self.assertEqual(installed_binding(raw, candidate, actual)["installed_sha256"], actual["candidate_sha256"])
        for key in ("installer_actual_pass", "actual_write_and_readback_pass", "protected_audit_pass"):
            with self.subTest(key=key), self.assertRaises(TrialError):
                installed_binding(raw, candidate, {**actual, key: False})
        for changed in (candidate[:-1], b"X" + candidate[1:], candidate[:0x20000] + b"X" + candidate[0x20001:]):
            with self.assertRaises(TrialError):
                installed_binding(raw, changed, actual)
        with self.assertRaises(TrialError):
            installed_binding(raw + b" ", candidate, actual)

    def test_native241_state_is_bound_to_live_uart_token_and_installed_hash(self):
        token, installed = "a" * 64, "b" * 64
        state = {"board": "skyworth_sk-d840n", "vendor_abi_sha256": BASE,
                 "mode": "native-resident", "resident": True, "version": VERSION,
                 "layout": "native241", "power_mode": "continuous_required",
                 "token": token, "installed_sha256": installed, "can_reboot": False, "state": "idle"}
        self.assertEqual(verify_web_state(state, token, installed), state)
        for key, value in (("board", "other-board"), ("board", "SK-D840N"), ("vendor_abi_sha256", "e" * 64),
                           ("mode", "native-ram-readonly"), ("resident", False),
                           ("version", "bootweb-resident-native-v1"), ("layout", "current240"),
                           ("power_mode", "unchanged"), ("token", "c" * 64),
                           ("installed_sha256", "d" * 64), ("can_reboot", True), ("state", "writing")):
            with self.subTest(key=key), self.assertRaises(TrialError):
                verify_web_state({**state, key: value}, token, installed)


if __name__ == "__main__":
    unittest.main()
