"""Offline instruction regressions for the two real compiler hazards."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from audit_ram_elf import audit_instructions, audit_elf, verify_payload


class RamInstructionTests(unittest.TestCase):
    def test_stack_pointer_and_reading_gd_are_allowed(self):
        # sub sp,sp,#16; mov x0,x18; add sp,sp,#16; ret
        report = audit_instructions(bytes.fromhex("ff4300d1400212aaff430091c0035fd6"), 0x89000000)
        self.assertEqual(report["instructions"], 4)
        self.assertEqual(report["x18_read_instructions"], 1)

    def test_real_faulting_simd_store_is_rejected(self):
        # stp q0,q1,[x10,#-16] -- the first native trial's fault at 0x8900c500
        with self.assertRaisesRegex(ValueError, "SIMD/FP"):
            audit_instructions(bytes.fromhex("40853fad"), 0x8900c500)

    def test_gd_clobber_and_truncated_code_are_rejected(self):
        # mov x18,x0
        with self.assertRaisesRegex(ValueError, "gd/x18 clobber"):
            audit_instructions(bytes.fromhex("f20300aa"), 0x89000000)
        for data in (b"", b"\xc0\x03\x5f"):
            with self.assertRaises(ValueError):
                audit_instructions(data, 0x89000000)
        with self.assertRaises(ValueError):
            audit_elf(b"not ELF")

    def test_payload_binding_rejects_modified_native_bytes(self):
        root = Path(__file__).resolve().parents[1]
        elf = (root / ".build/bootweb-unadapted.elf").read_bytes()
        payload = (root / ".build/bootweb-unadapted.bin").read_bytes()
        self.assertTrue(verify_payload(elf, payload)["payload_matches_elf"])
        for damaged in (payload[:-1], bytes([payload[0] ^ 1]) + payload[1:]):
            with self.assertRaisesRegex(ValueError, "flat payload"):
                verify_payload(elf, damaged)
