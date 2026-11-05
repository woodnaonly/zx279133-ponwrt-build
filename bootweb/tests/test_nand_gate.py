"""Pure physical-row/write-order guards; never opens a device or emulates ECC."""
import ctypes as C
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from host.core import Core
from test_bounded_spi import Op
class Gate(C.Structure):
    _fields_=[("offset",C.c_uint32),("state",C.c_uint32),("kind",C.c_uint32),("failed",C.c_uint32),
              ("opcode",C.c_uint8),("sha256",C.c_char*65)]
class NandGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib=Core(testing=True).lib
        cls.lib.bw_nand_gate_begin.argtypes=[C.POINTER(Gate),C.c_uint32,C.c_void_p,C.c_uint8]
        cls.lib.bw_nand_gate_accept.argtypes=[C.POINTER(Gate),C.POINTER(Op)]
        cls.lib.bw_nand_gate_finish.argtypes=[C.POINTER(Gate)]
        cls.lib.bw_nand_gate_begin_target.argtypes=[C.POINTER(Gate),C.c_uint,C.c_uint32,C.c_void_p,C.c_uint8]
        cls.lib.bw_nand_target_contains.argtypes=[C.c_uint,C.c_uint32,C.c_size_t]
        cls.lib.bw_nand_target_contains_layout.argtypes=[C.c_uint,C.c_uint,C.c_uint32,C.c_size_t]
        cls.lib.bw_nand_gate_begin_layout.argtypes=[C.POINTER(Gate),C.c_uint,C.c_uint,C.c_uint32,C.c_void_p,C.c_uint8]
    def test_native241_last_mib_is_explicit_and_never_expands_installer(self):
        for layout,end in ((0,0xff00000),(1,0x10000000),(2,0xfc00000)):
            self.assertEqual(self.lib.bw_nand_target_contains_layout(layout,3,end-2048,2048),1)
            self.assertEqual(self.lib.bw_nand_target_contains_layout(layout,3,end-2048,2049),0)
            self.assertEqual(self.lib.bw_nand_target_contains_layout(layout,0,0x180000,2048),0)
        g=Gate()
        self.assertEqual(self.lib.bw_nand_gate_begin_layout(C.byref(g),1,3,0xffe0000,None,2),0)
        self.assertEqual(self.accept(g,self.op(6)),0)
        self.assertEqual(self.accept(g,self.op(0xd8,address=0xffe0000//2048,ab=3)),0)
        self.assertEqual(self.lib.bw_nand_gate_finish(C.byref(g)),0)
        self.assertNotEqual(self.lib.bw_nand_gate_begin_target(C.byref(g),3,0xffe0000,None,2),0)
        self.assertNotEqual(self.begin(0xffe0000)[1],0)
        for invalid in (3,0xffffffff):
            self.assertEqual(self.lib.bw_nand_target_contains_layout(invalid,3,0xf00000,2048),0)
    def op(self,opcode,*,address=0,ab=0,direction=0,data=None,width=1):
        o=Op();o.opcode=opcode;o.command_width=1;o.address=address;o.address_bytes=ab;o.address_width=1
        o.direction=direction;o.data_width=width;o.data=C.addressof(data) if data is not None else None;o.length=len(data) if data is not None else 0
        return o
    def begin(self,off=0x20000,page=None,opcode=2):
        g=Gate();rc=self.lib.bw_nand_gate_begin(C.byref(g),off,page,opcode);return g,rc
    def accept(self,g,op):return self.lib.bw_nand_gate_accept(C.byref(g),C.byref(op))
    def test_exact_erase_at_both_window_edges_and_duplicate_finish(self):
        for off in (0x20000,0x160000):
            g,rc=self.begin(off);self.assertEqual(rc,0)
            self.assertEqual(self.accept(g,self.op(6)),0)
            self.assertEqual(self.accept(g,self.op(0xd8,address=off//2048,ab=3)),0)
            self.assertEqual(self.lib.bw_nand_gate_finish(C.byref(g)),0)
            self.assertNotEqual(self.lib.bw_nand_gate_finish(C.byref(g)),0)
    def test_explicit_current240_targets_never_expand_legacy_installer(self):
        for target,start,end in ((0,0x20000,0x180000),(1,0x200000,0xa00000),(2,0xa00000,0xb00000),(3,0xf00000,0xff00000)):
            for off in (start,end-0x20000):
                g=Gate();self.assertEqual(self.lib.bw_nand_gate_begin_target(C.byref(g),target,off,None,2),0)
                self.assertEqual(self.accept(g,self.op(6)),0)
                self.assertEqual(self.accept(g,self.op(0xd8,address=off//2048,ab=3)),0)
                self.assertEqual(self.lib.bw_nand_gate_finish(C.byref(g)),0)
                if target:self.assertNotEqual(self.begin(off)[1],0)
            for off in (start-0x20000,end,0x180000,0x1a0000,0xb00000,0xff00000,0xffffffff):
                if start<=off<end:continue
                g=Gate();self.assertNotEqual(self.lib.bw_nand_gate_begin_target(C.byref(g),target,off,None,2),0)
            self.assertEqual(self.lib.bw_nand_target_contains(target,end-2048,2048),1)
            self.assertEqual(self.lib.bw_nand_target_contains(target,end-2048,2049),0)
        for target in (4,0xffffffff):
            self.assertEqual(self.lib.bw_nand_target_contains(target,0x20000,2048),0)
    def test_all_eleven_blocks_and_704_pages_have_exact_physical_rows(self):
        erases=pages=0;data=C.create_string_buffer(b"N"*2048,2048)
        for off in range(0x20000,0x180000,0x20000):
            g,rc=self.begin(off);self.assertEqual(rc,0)
            self.assertEqual(self.accept(g,self.op(6)),0)
            self.assertEqual(self.accept(g,self.op(0xd8,address=off//2048,ab=3)),0)
            self.assertEqual(self.lib.bw_nand_gate_finish(C.byref(g)),0);erases+=1
            for page in range(off,off+0x20000,2048):
                g,rc=self.begin(page,data);self.assertEqual(rc,0)
                for op in (self.op(6),self.op(2,ab=2,direction=2,data=data),self.op(0x10,address=page//2048,ab=3)):
                    self.assertEqual(self.accept(g,op),0)
                self.assertEqual(self.lib.bw_nand_gate_finish(C.byref(g)),0);pages+=1
        self.assertEqual((erases,pages),(11,704))
    def test_page_data_hash_row_and_one_program_per_arm(self):
        for opcode,width in ((2,1),(0x32,4)):
            data=C.create_string_buffer(b"Q"*2048,2048);g,rc=self.begin(0x17f800,data,opcode);self.assertEqual(rc,0)
            for op in (self.op(6),self.op(opcode,ab=2,direction=2,data=data,width=width),self.op(0x10,address=0x2ff,ab=3)):
                self.assertEqual(self.accept(g,op),0)
            self.assertEqual(self.lib.bw_nand_gate_finish(C.byref(g)),0)
            self.assertNotEqual(self.accept(g,self.op(6)),0)
    def test_protected_unaligned_and_out_of_range_requests_fail(self):
        for off in (0,0x1ffff,0x20001,0x180000,0x1a0000,0xb00000,0xffffffff):
            with self.subTest(off=off):self.assertNotEqual(self.begin(off)[1],0)
        data=C.create_string_buffer(2048)
        for off in (0x20001,0x20040,0x17ffff,0x180000):self.assertNotEqual(self.begin(off,data)[1],0)
    def test_wrong_row_or_opcode_is_sticky_and_cannot_be_rearmed_in_sequence(self):
        for opcode,row in ((0xd8,0),(0xd8,0x300),(0x10,0x40),(0x84,0),(0x1f,0xa0)):
            g,_=self.begin();self.assertEqual(self.accept(g,self.op(6)),0)
            self.assertNotEqual(self.accept(g,self.op(opcode,address=row,ab=3)),0)
            self.assertNotEqual(self.accept(g,self.op(0xd8,address=0x40,ab=3)),0)
            self.assertNotEqual(self.lib.bw_nand_gate_finish(C.byref(g)),0)
    def test_mutated_data_oob_and_partial_page_are_denied(self):
        for fault in ("mutated","oob","short","bus","column"):
            data=C.create_string_buffer(b"Q"*2048,2048);g,_=self.begin(page=data);self.accept(g,self.op(6))
            op=self.op(2,ab=2,direction=2,data=data)
            if fault=="mutated":data[17]=b"X"
            if fault=="oob":op.length=2176
            if fault=="short":op.length=2047
            if fault=="bus":op.data_width=4
            if fault=="column":op.address=1
            with self.subTest(fault=fault):self.assertNotEqual(self.accept(g,op),0)
    def test_missing_load_duplicate_wren_early_execute_and_incomplete_finish_fail(self):
        data=C.create_string_buffer(2048)
        for opcode in (6,0x10):
            g,_=self.begin(page=data);self.accept(g,self.op(6))
            self.assertNotEqual(self.accept(g,self.op(opcode,address=0x40 if opcode==0x10 else 0,ab=3 if opcode==0x10 else 0)),0)
        g,_=self.begin(page=data);self.accept(g,self.op(6));self.assertNotEqual(self.lib.bw_nand_gate_finish(C.byref(g)),0)
if __name__=="__main__":unittest.main()
