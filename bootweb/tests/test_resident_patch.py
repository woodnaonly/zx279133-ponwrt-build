"""Development patch bounds: these tests NEVER authorize a flash operation."""
from pathlib import Path
import struct
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from host.resident import (LINK, HOOK_OFFSET, SLOT_OFFSET, ORIGINAL_HOOK,
                          DEVELOPMENT_HOOK, _patch_hook_and_slot)


class ResidentPatchTests(unittest.TestCase):
    def baseline(self):
        data=bytearray(b"\xff"*0x160000)
        data[HOOK_OFFSET:HOOK_OFFSET+16]=ORIGINAL_HOOK
        struct.pack_into("<QQQ",data,0x98cd8,LINK+0x80000,1027,LINK+0x80010)
        return data

    def patch(self,data,tail=b"D"*128):
        return _patch_hook_and_slot(data,tail,0x98ce0,0xb3d78,0x98cd8,0x98cf0)

    def test_only_hook_and_erased_tail_are_changed(self):
        before=self.baseline();after=self.patch(before)
        self.assertEqual(len(before),len(after))
        self.assertEqual(after[HOOK_OFFSET:HOOK_OFFSET+16],DEVELOPMENT_HOOK)
        self.assertEqual(after[SLOT_OFFSET:SLOT_OFFSET+128],b"D"*128)
        for a,b in ((0,HOOK_OFFSET),(HOOK_OFFSET+16,SLOT_OFFSET),(SLOT_OFFSET+128,len(before))):
            self.assertEqual(after[a:b],before[a:b])
        self.assertEqual(before[HOOK_OFFSET:HOOK_OFFSET+16],ORIGINAL_HOOK)

    def test_hook_literal_is_original_load_address_not_relocated_code(self):
        self.assertEqual(struct.unpack("<IIQ",DEVELOPMENT_HOOK),(0x58000050,0xd61f0200,0x88100000))

    def test_changed_hook_or_non_erased_tail_fails(self):
        for at in (HOOK_OFFSET,SLOT_OFFSET+4):
            data=self.baseline();data[at]^=1
            with self.subTest(at=at),self.assertRaises(ValueError):self.patch(data)

    def test_empty_unaligned_and_oversized_tail_fail(self):
        for tail in (b"",b"D"*63,b"D"*(0x60000+64)):
            with self.subTest(size=len(tail)),self.assertRaises(ValueError):self.patch(self.baseline(),tail)

    def test_any_overlapping_relative_relocation_fails(self):
        for off in (HOOK_OFFSET-4,HOOK_OFFSET,HOOK_OFFSET+8,HOOK_OFFSET+12):
            data=self.baseline();struct.pack_into("<Q",data,0x98cd8,LINK+off)
            with self.subTest(off=off),self.assertRaisesRegex(ValueError,"relocation"):self.patch(data)

    def test_invalid_relocation_bounds_fail(self):
        for start,end in ((0,0),(0x98cd8,0xb3d80),(0x98cd8,0x98cf1)):
            with self.subTest(start=start,end=end),self.assertRaises(ValueError):
                _patch_hook_and_slot(self.baseline(),b"D"*64,0x98ce0,0xb3d78,start,end)


if __name__=="__main__":unittest.main()
