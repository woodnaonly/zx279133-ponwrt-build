"""Fault-injected FIFO model, NOT a replacement for native NAND acceptance."""
import ctypes as C
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from host.core import Core

R32=C.CFUNCTYPE(C.c_uint32,C.c_void_p,C.c_uint32)
W32=C.CFUNCTYPE(None,C.c_void_p,C.c_uint32,C.c_uint32)
NOW=C.CFUNCTYPE(C.c_uint64,C.c_void_p)
SELECT=C.CFUNCTYPE(C.c_int,C.c_void_p)


class Op(C.Structure):
    _fields_=[("command_width",C.c_uint8),("opcode",C.c_uint8),("r0",C.c_uint8*6),
              ("address_bytes",C.c_uint8),("address_width",C.c_uint8),("r1",C.c_uint8*6),
              ("address",C.c_uint64),("dummy_bytes",C.c_uint8),("dummy_width",C.c_uint8),("r2",C.c_uint8*6),
              ("data_width",C.c_uint8),("r3",C.c_uint8*3),("direction",C.c_uint32),
              ("length",C.c_uint32),("r4",C.c_uint32),("data",C.c_void_p)]


class IO(C.Structure):
    _fields_=[("context",C.c_void_p),("read32",R32),("write32",W32),("milliseconds",NOW),
              ("select",SELECT),("timeout_ms",C.c_uint32),("poll_limit",C.c_uint32)]


class Fifo:
    def __init__(self,*,busy=0,level=0x030300,irq=1,step=1,select_rc=0):
        self.busy,self.level,self.irq,self.step=busy,level,irq,step
        self.clock=0;self.writes=[];self.errors=[];self.rx_words=0;self.selected=0
        def read(ctx,off):
            if off==4:return self.busy
            if off==44:return self.irq
            if off==52:return self.level
            if off==56:
                value=0x44332211+self.rx_words*0x04040404;self.rx_words+=1;return value
            self.errors.append(("unexpected-read",off));return 0
        def write(ctx,off,value):self.writes.append((off,value))
        def now(ctx):value=self.clock;self.clock+=self.step;return value
        def select(ctx):self.selected+=1;return select_rc
        self.callbacks=(R32(read),W32(write),NOW(now),SELECT(select))
        self.io=IO(None,*self.callbacks,20,1000)


class BoundedSpiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib=Core(testing=True).lib
        cls.lib.bw_bounded_spi_exec.argtypes=[C.POINTER(IO),C.POINTER(Op)]
        cls.lib.bw_bounded_spi_exec.restype=C.c_int
        cls.lib.bw_bounded_spi_exec_tracked.argtypes=[C.POINTER(IO),C.POINTER(Op),C.POINTER(C.c_int)]
        cls.lib.bw_bounded_spi_exec_tracked.restype=C.c_int
        cls.lib.bw_bounded_spi_remaining.argtypes=[C.c_uint64,C.c_uint64,C.c_uint32,C.POINTER(C.c_uint32)]
        cls.lib.bw_bounded_spi_remaining.restype=C.c_int

    def test_outer_ecc_wrapper_deadline_limits_each_fifo_wait(self):
        for elapsed,expected in ((0,5),(95,5),(96,4),(99,1)):
            left=C.c_uint32(123)
            self.assertEqual(self.lib.bw_bounded_spi_remaining(1000,1000+elapsed,100,C.byref(left)),0)
            self.assertEqual(left.value,expected)
        for start,now,limit,expected in ((1000,1100,100,-110),(1000,1200,100,-110),
                                         (1000,999,100,-22),(0,0,0,-22),(0,0,3001,-22)):
            left=C.c_uint32(123)
            self.assertEqual(self.lib.bw_bounded_spi_remaining(start,now,limit,C.byref(left)),expected)
            self.assertEqual(left.value,0)

    def run_op(self,fifo,n=0,direction=0):
        data=(C.c_uint8*(max(1,n)+8))(*([0xa5]*(max(1,n)+8)))
        op=Op();op.command_width=1;op.opcode=0x0f;op.direction=direction;op.length=n
        op.data_width=1;op.data=C.addressof(data)+4 if n else None
        return self.lib.bw_bounded_spi_exec(C.byref(fifo.io),C.byref(op)),data,op

    def test_measured_layout_and_no_data_completion(self):
        self.assertEqual((Op.data.offset,Op.direction.offset,Op.length.offset),(48,36,40))
        fifo=Fifo();rc,_,_=self.run_op(fifo)
        self.assertEqual(rc,0);self.assertEqual(fifo.writes[-1],(8,0));self.assertEqual(fifo.errors,[])
        self.assertIn((12,115776),fifo.writes);self.assertIn((32,0x0f),fifo.writes)

    def test_partial_rx_does_not_clobber_neighbor_bytes(self):
        fifo=Fifo();rc,data,_=self.run_op(fifo,7,1)
        self.assertEqual(rc,0);self.assertEqual(bytes(data)[4:11],bytes.fromhex("11223344152637"))
        self.assertEqual(bytes(data)[:4],b"\xa5"*4);self.assertEqual(bytes(data)[11:],b"\xa5"*4)
        self.assertEqual(fifo.rx_words,2)

    def test_partial_tx_does_not_read_past_the_declared_data(self):
        fifo=Fifo();rc,_,_=self.run_op(fifo,3,2)
        self.assertEqual(rc,0);self.assertEqual([v for a,v in fifo.writes if a==56],[0x00a5a5a5])

    def test_initial_busy_has_a_deadline_and_never_enables_controller(self):
        fifo=Fifo(busy=1);rc,_,_=self.run_op(fifo)
        self.assertEqual(rc,-110);self.assertEqual(fifo.writes,[]);self.assertEqual(fifo.selected,0)

    def test_submission_evidence_matches_actual_start_even_after_completion_failure(self):
        for fifo,submitted_expected in ((Fifo(),1),(Fifo(irq=2),1),(Fifo(busy=1),0),(Fifo(select_rc=-1),0)):
            op=Op();op.command_width=1;op.opcode=0xd8;op.address_bytes=3;op.address_width=1;op.address=0x40
            submitted=C.c_int(123)
            self.lib.bw_bounded_spi_exec_tracked(C.byref(fifo.io),C.byref(op),C.byref(submitted))
            self.assertEqual(submitted.value,submitted_expected)
            self.assertEqual(submitted.value,int((4,1) in fifo.writes))

    def test_fifo_stalls_in_both_directions_are_bounded_and_disable_controller(self):
        for direction in (1,2):
            fifo=Fifo(level=0);rc,_,_=self.run_op(fifo,8,direction)
            with self.subTest(direction=direction):
                self.assertEqual(rc,-110);self.assertEqual(fifo.writes[-1],(8,0));self.assertLess(fifo.clock,25)

    def test_completion_error_is_not_success(self):
        for irq in (2,4,0x10,0x20,3,0x37):
            fifo=Fifo(irq=irq);rc,_,_=self.run_op(fifo)
            with self.subTest(irq=irq):self.assertEqual(rc,-5);self.assertEqual(fifo.writes[-1],(8,0))

    def test_missing_completion_and_stopped_clock_are_bounded(self):
        for step in (0,1):
            fifo=Fifo(irq=0,step=step);fifo.io.poll_limit=30
            rc,_,_=self.run_op(fifo)
            with self.subTest(step=step):self.assertEqual(rc,-110);self.assertEqual(fifo.writes[-1],(8,0))

    def test_clock_reversal_and_select_error_do_not_start_a_command(self):
        for fifo in (Fifo(step=-1),Fifo(select_rc=-1)):
            fifo.clock=100;rc,_,_=self.run_op(fifo)
            self.assertNotEqual(rc,0);self.assertEqual(fifo.writes,[])

    def test_invalid_operations_never_touch_mmio(self):
        for key,value in (("command_width",2),("address_bytes",5),("direction",3),("length",4097),
                          ("data_width",3),("data",0)):
            fifo=Fifo();data=C.create_string_buffer(32);op=Op();op.command_width=1;op.opcode=3
            op.length=4;op.direction=1;op.data_width=1;op.data=C.addressof(data)
            setattr(op,key,value)
            with self.subTest(key=key):
                self.assertEqual(self.lib.bw_bounded_spi_exec(C.byref(fifo.io),C.byref(op)),-22)
                self.assertEqual(fifo.writes,[]);self.assertEqual(fifo.selected,0)

    def test_address_dummy_and_bus_configuration_matches_pinned_driver(self):
        fifo=Fifo();data=C.create_string_buffer(4);op=Op();op.command_width=1;op.opcode=0x6b
        op.address_bytes=3;op.address_width=1;op.address=0x123456
        op.dummy_bytes=1;op.dummy_width=1;op.data_width=4;op.direction=1;op.length=4;op.data=C.addressof(data)
        self.assertEqual(self.lib.bw_bounded_spi_exec(C.byref(fifo.io),C.byref(op)),0)
        self.assertIn((16,0x16),fifo.writes);self.assertIn((20,0x1045),fifo.writes)
        self.assertIn((28,0x123456),fifo.writes)


if __name__=="__main__":unittest.main()
