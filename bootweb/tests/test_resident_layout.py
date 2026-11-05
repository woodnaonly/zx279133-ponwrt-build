"""Pure static bounds tests. No real flash image is created or written."""
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.resident import LINK, _address_pair, _slot, audit_resident_slot
from fixtures import nand_seed


class ResidentLayoutTests(unittest.TestCase):
    def test_exact_copy_pair_is_decoded_instead_of_using_whole_boot_load(self):
        instructions = struct.pack("<II", 0x900004c2, 0x91336042)
        self.assertEqual(_address_pair(instructions, 0, 2), LINK + 0x98cd8)
        for invalid in (instructions[:4], bytes(8), instructions[:4] + struct.pack("<I", 0x91736042)):
            with self.assertRaises(ValueError):
                _address_pair(invalid, 0, 2)

    def test_slot_must_leave_vendor_copy_bss_and_protected_environment_untouched(self):
        stage2 = b"\xff" * 0x160000
        slot = _slot(stage2, 0x100000, 0x20000, 0x98ce0, 0xb3d78)
        self.assertEqual(slot["physical_start"], "0x120000")
        self.assertEqual(slot["physical_end"], "0x140000")
        self.assertFalse(slot["automatically_relocated"])
        self.assertTrue(slot["explicit_copy_required"])
        for offset, size in ((0xb3d40, 64), (0x100001, 64), (0x100000, 0),
                             (0x15ffc0, 65), (0x160000, 64), (-64, 64)):
            with self.subTest(offset=offset, size=size), self.assertRaises(ValueError):
                _slot(stage2, offset, size, 0x98ce0, 0xb3d78)

    def test_non_erased_padding_and_unknown_baseline_fail(self):
        stage2 = bytearray(b"\xff" * 0x160000)
        stage2[0x100010] = 0
        with self.assertRaisesRegex(ValueError, "erased"):
            _slot(stage2, 0x100000, 64, 0x98ce0, 0xb3d78)
        with self.assertRaisesRegex(ValueError, "unknown"):
            audit_resident_slot(nand_seed(), 0x100000, 64)


if __name__ == "__main__":
    unittest.main()
