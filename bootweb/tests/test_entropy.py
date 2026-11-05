"""SHA3 vectors and fault injection; simulated clocks do NOT certify entropy."""
import ctypes as C
import hashlib
from pathlib import Path
import random
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
NOW=C.CFUNCTYPE(C.c_uint64,C.c_void_p)
class IO(C.Structure):
    _fields_=[("context",C.c_void_p),("counter",NOW),("milliseconds",NOW),("timeout_ms",C.c_uint32)]
class Sha3(C.Structure):
    _fields_=[("state",C.c_uint64*25),("used",C.c_size_t)]

class EntropyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        zig=ROOT/".build/toolchain/zig-x86_64-windows-0.14.1/zig.exe"
        upstream=ROOT/"vendor/jitterentropy";obj=ROOT/".build/jent-test-O0.o"
        common=[str(zig),"cc","-std=c11","-Wall","-Wextra","-Werror","-Wno-unused-parameter",
                "-I"+str(upstream),"-I"+str(ROOT/"include")]
        subprocess.run([*common,"-O0","-Wno-pointer-sign","-c",str(upstream/"jitterentropy.c"),"-o",str(obj)],check=True,capture_output=True)
        lib=ROOT/(".build/jent-test.dll" if sys.platform=="win32" else ".build/jent-test.so")
        subprocess.run([*common,"-O2","-shared",*(["-fPIC"] if sys.platform!="win32" else []),
                        str(ROOT/"adapter/jitter_binding.c"),str(ROOT/"src/sha3.c"),str(obj),"-o",str(lib)],check=True,capture_output=True)
        cls.lib=C.CDLL(str(lib))
        cls.lib.bw_sha3_256.argtypes=[C.c_void_p,C.c_size_t,C.c_void_p]
        cls.lib.bw_sha3_init.argtypes=[C.POINTER(Sha3)]
        cls.lib.bw_sha3_add.argtypes=[C.POINTER(Sha3),C.c_void_p,C.c_size_t]
        cls.lib.bw_sha3_end.argtypes=[C.POINTER(Sha3),C.c_void_p]
        cls.lib.bw_entropy_token.argtypes=[C.POINTER(IO),C.c_void_p]
        cls.lib.bw_entropy_token.restype=C.c_int

    def test_sha3_matches_hashlib_across_padding_and_multiblock_boundaries(self):
        for n in (0,1,3,31,32,64,135,136,137,271,272,273,4096,10001):
            data=bytes(i%251 for i in range(n));out=C.create_string_buffer(32)
            self.lib.bw_sha3_256(data,n,out)
            with self.subTest(n=n):self.assertEqual(out.raw,hashlib.sha3_256(data).digest())

    def test_incremental_and_zero_length_sha3(self):
        data=bytes(range(256))*13;out=C.create_string_buffer(32);state=Sha3();self.lib.bw_sha3_init(C.byref(state))
        for at in range(0,len(data),17):self.lib.bw_sha3_add(C.byref(state),data[at:at+17],len(data[at:at+17]))
        self.lib.bw_sha3_add(C.byref(state),None,0);self.lib.bw_sha3_end(C.byref(state),out)
        self.assertEqual(out.raw,hashlib.sha3_256(data).digest());self.assertEqual(bytes(state),bytes(C.sizeof(state)))

    def run_entropy(self,kind):
        step=0;tick=100;sim=random.Random(8473)
        def counter(ctx):
            nonlocal tick
            if kind=="zero":return 0
            if kind=="stopped":return 100
            if kind=="backward":tick-=1;return max(1,tick)
            tick+=sim.randint(1,20000);return tick
        def clock(ctx):
            nonlocal step
            step+=1
            if kind=="deadline":return step
            if kind=="clockback":return max(1,100-step)
            return step//100000
        callbacks=(NOW(counter),NOW(clock));io=IO(None,*callbacks,2 if kind=="deadline" else 10000)
        token=C.create_string_buffer(b"x"*64,65)
        rc=self.lib.bw_entropy_token(C.byref(io),token)
        return rc,token.raw,step

    def test_synthetic_varied_clock_exercises_health_path_not_entropy_quality(self):
        rc,token,_=self.run_entropy("varied")
        self.assertEqual(rc,0);self.assertRegex(token[:-1].decode(),r"^[0-9a-f]{64}$");self.assertEqual(token[-1],0)

    def test_no_time_stopped_counter_and_counter_reversal_zero_output(self):
        for kind in ("zero","stopped","backward"):
            rc,token,n=self.run_entropy(kind)
            with self.subTest(kind=kind):
                self.assertNotEqual(rc,0);self.assertEqual(token,bytes(65));self.assertLess(n,10000)

    def test_deadline_and_clock_reversal_zero_output(self):
        for kind in ("deadline","clockback"):
            rc,token,n=self.run_entropy(kind)
            with self.subTest(kind=kind):
                self.assertNotEqual(rc,0);self.assertEqual(token,bytes(65));self.assertLess(n,10000)

    def test_bad_interface_does_not_retain_a_previous_token(self):
        token=C.create_string_buffer(b"x"*64,65)
        self.assertNotEqual(self.lib.bw_entropy_token(None,token),0);self.assertEqual(token.raw,bytes(65))

    def test_vendored_source_is_unmodified(self):
        expected={"jitterentropy.c":"9c5bd55fb589248b5de5d0cf7d73c66d38eba025b9c4f54527398d5f42bc679c",
                  "jitterentropy.h":"9dcbc0f1b97637ca231f053f07cadbad2f7bf28fa9cd9aa8ec7fb94a1aeec43a"}
        for name,digest in expected.items():
            self.assertEqual(hashlib.sha256((ROOT/"vendor/jitterentropy"/name).read_bytes()).hexdigest(),digest)

if __name__=="__main__":unittest.main()
