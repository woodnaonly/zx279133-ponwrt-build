"""Fault-injected status reads; these tests never open a real serial port."""
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.btl import SELECTORS, inspect_boot_selectors
from host.uart import LinkLost, TrialError


class StatusConsole:
    def __init__(self, values):
        self.identified = self.prompt = True
        self.transferring = False
        self.values = values
        self.commands = []

    def inspect(self, command):
        self.commands.append(command)
        name, address = SELECTORS[len(self.commands) - 1]
        if command != f"md.b {address:x} 4":
            raise AssertionError("unexpected status command")
        data = struct.pack("<I", self.values[name])
        return f"{address:08x}: " + data.hex(" ") + "\r\n=> "


class BtlStatusTests(unittest.TestCase):
    def test_nand_direct_and_signed_status_are_not_rescue_evidence(self):
        for secure in (0, 1):
            console = StatusConsole({"boot_mode": 0xa1, "security_ready": 1,
                                     "security_selector": secure})
            report = inspect_boot_selectors(console)
            self.assertEqual(report["boot_media_bits"], 1)
            self.assertEqual(report["boot_media_interpretation"], "nand-controller")
            self.assertEqual(report["secure_container_selected"], bool(secure))
            self.assertFalse(report["serial_rescue_verified"])
            self.assertFalse(report["cold_power_timestamp_verified"])
            self.assertFalse(report["reset_time_selector_verified"])
            self.assertFalse(report["native_write_enabled"])
            self.assertEqual(len(console.commands), 3)

    def test_mode3_is_the_spi_loader_not_proof_of_nor_flash(self):
        report = inspect_boot_selectors(StatusConsole(
            {"boot_mode": 0x10013, "security_ready": 1, "security_selector": 0}))
        self.assertEqual(report["boot_media_interpretation"], "spi-loader")
        self.assertEqual(report["static_boot_dispatch_target"], "0x1f54")
        self.assertFalse(report["serial_rescue_verified"])

    def test_unready_security_status_is_unknown_not_nonsecure(self):
        console = StatusConsole({"boot_mode": 0, "security_ready": 0})
        report = inspect_boot_selectors(console)
        self.assertIsNone(report["secure_container_selected"])
        self.assertFalse(report["secure_selector_ready"])
        self.assertFalse(report["serial_rescue_verified"])
        self.assertEqual(len(console.commands), 2)

    def test_unknown_prompt_or_transfer_sends_nothing(self):
        for field, value in (("identified", False), ("prompt", False), ("transferring", True)):
            console = StatusConsole({})
            setattr(console, field, value)
            with self.assertRaises(TrialError):
                inspect_boot_selectors(console)
            self.assertEqual(console.commands, [])

    def test_short_or_disconnected_read_is_not_retried(self):
        for error in (None, LinkLost("unplugged")):
            console = StatusConsole({})
            def fail(command):
                console.commands.append(command)
                if error:
                    raise error
                return "10e10170: 01 00\r\n=> "
            console.inspect = fail
            with self.assertRaises(TrialError):
                inspect_boot_selectors(console)
            self.assertEqual(console.commands, ["md.b 10e10170 4"])


if __name__ == "__main__":
    unittest.main()
