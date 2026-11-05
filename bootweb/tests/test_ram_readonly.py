"""The real C read-only build must refuse writes even with hostile callbacks."""
import ctypes as C
import hashlib
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.core import Port, Core, ValidationError
from host.platform import Platform
from fixtures import system
from test_native_service import MemoryHTTP


class RamReadonlyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        suffix = ".dll" if sys.platform == "win32" else ".so"
        path = ROOT / ".build" / ("bootweb-ram-readonly-test" + suffix)
        if not path.is_file():
            raise unittest.SkipTest("build with --test-readonly")
        cls.lib = C.CDLL(str(path))
        cls.lib.bw_test_build.restype = C.c_int
        if cls.lib.bw_test_build() != 1:
            raise RuntimeError("synthetic tests cannot use a real base")
        cls.lib.bw_serve.argtypes, cls.lib.bw_serve.restype = [C.POINTER(Platform)], C.c_int
        cls.lib.bw_execute.argtypes = [C.c_void_p, C.c_size_t, C.c_char_p, C.c_uint32,
                                       C.POINTER(Port), C.c_void_p, C.c_size_t, C.c_void_p]
        cls.lib.bw_execute.restype = C.c_int
        cls.lib.bw_test_ram_read_allowed.argtypes = [C.c_uint32, C.c_size_t]
        cls.lib.bw_test_ram_read_allowed.restype = C.c_int
        cls.lib.bw_test_ram_ecc_result.argtypes = [C.c_int, C.c_size_t, C.c_size_t]
        cls.lib.bw_test_ram_ecc_result.restype = C.c_int

    def test_exact_native_policy_rejects_bad_ranges_short_and_uncorrectable_reads(self):
        allowed = self.lib.bw_test_ram_read_allowed
        for off, n in ((0,0x20000),(0x1e0000,0x20000),(0xb00000,0x20000),(0xee0000,0x20000)):
            self.assertEqual(allowed(off,n),1)
        for off, n in ((0,0),(0,0x20001),(0x1ff800,0x1000),(0x200000,0x800),
                       (0xaff800,0x1000),(0xf00000,0x800),(0xfffff800,0x1000),(0,(1<<64)-1)):
            self.assertEqual(allowed(off,n),0)
        result = self.lib.bw_test_ram_ecc_result
        self.assertEqual(result(0,0x20000,0x20000),0)
        self.assertEqual(result(-117,0x20000,0x20000),1)
        for rc,done,wanted in ((0,0,0),(-74,0x20000,0x20000),(0,0x1ffff,0x20000),
                               (-117,0x1ffff,0x20000),(0,0x20001,0x20000),(1,0x20000,0x20000)):
            self.assertEqual(result(rc,done,wanted),-1)

    def test_readonly_mode_overrides_all_capabilities_and_reboot_callback(self):
        io = MemoryHTTP(self.lib, capabilities=0x7f)
        data = system()
        io.add("GET", "/")
        io.add("POST", "/api/upload", data)
        io.add("GET", "/api/state")
        io.add("POST", "/api/confirm", "1\n" + hashlib.sha256(data).hexdigest() + "\n")
        io.add("POST", "/api/reboot")
        io.add("GET", "/api/state")
        self.assertEqual(io.run(), 0)
        for index in (1, 2, 5):
            state = io.responses[index]["body"]
            self.assertEqual(state["mode"], "native-ram-readonly")
            self.assertEqual(state["state"], "ready")
            self.assertFalse(state["can_confirm"])
            self.assertFalse(state["can_reboot"])
        self.assertEqual([r["status"] for r in io.responses[3:5]], [409, 409])
        self.assertEqual(io.reboots, 0)
        self.assertEqual(io.nand.events, [])

    def test_direct_writer_api_cannot_bypass_http_lock(self):
        io = MemoryHTTP(self.lib, capabilities=0x7f)
        data = system()
        error = C.create_string_buffer(192)
        self.assertNotEqual(self.lib.bw_execute(data, len(data), (b"a" * 64), 0,
                                                C.byref(io.nand.port), io.scratch, len(io.scratch), error), 0)
        self.assertIn(b"permanently disabled", error.value)
        self.assertEqual(io.nand.events, [])
        self.assertEqual(io.reboots, 0)

    def test_installed_version_does_not_enable_readonly_writes_or_reboot(self):
        io=MemoryHTTP(self.lib,capabilities=0x7f)
        io.platform.resident_version=b"bootweb-resident-v1"
        io.add("GET","/api/state");io.add("POST","/api/confirm",b"0"*140);io.add("POST","/api/reboot")
        self.assertEqual(io.run(),0)
        state=io.responses[0]["body"]
        self.assertEqual(state["mode"],"native-resident-readonly")
        self.assertEqual(state["version"],"bootweb-resident-v1");self.assertTrue(state["resident"])
        self.assertFalse(state["can_confirm"]);self.assertFalse(state["can_reboot"])
        self.assertEqual([r["status"] for r in io.responses[1:]],[409,409])
        self.assertEqual(io.nand.events,[]);self.assertEqual(io.reboots,0)

    def test_ten_interrupted_uploads_then_good_upload_remain_readonly(self):
        io = MemoryHTTP(self.lib, capabilities=0x7f)
        data = system()
        for index in range(10):
            io.add("POST", "/api/upload", data[:17 + index * 101], declared=len(data))
            io.add("GET", "/api/state")
            io.add("POST", "/api/cancel")
        io.add("POST", "/api/upload", data)
        self.assertEqual(io.run(), 0)
        for index in range(10):
            self.assertEqual(io.responses[index * 3 + 1]["body"]["state"], "failed")
        self.assertEqual(io.responses[-1]["body"]["state"], "ready")
        self.assertFalse(io.responses[-1]["body"]["can_confirm"])
        self.assertEqual(io.nand.events, [])

    def test_upload_deadline_closes_stream_without_validation_or_write(self):
        io = MemoryHTTP(self.lib, capabilities=0x7f)
        original_recv = io.recv
        def expired_recv(ctx, fd, dest, size, timeout):
            result = original_recv(ctx, fd, dest, size, timeout)
            if io.active and io.active["read"] > 1024:
                io.stopped = True
            return result
        from host.platform import RECV
        io.platform.recv = callback = RECV(expired_recv)
        io.add("POST", "/api/upload", system())
        self.assertEqual(io.run(), 0)
        self.assertTrue(io.stopped)
        self.assertFalse(io.responses[0]["reply"])
        self.assertEqual(io.nand.events, [])
        self.assertEqual(io.reboots, 0)

    def test_validator_crc_and_sha_cancel_and_clear(self):
        core = Core(testing=True)
        cancel_type = C.CFUNCTYPE(C.c_int, C.c_void_p)
        core.lib.bw_set_cancel.argtypes = [cancel_type, C.c_void_p]
        core.lib.bw_set_cancel.restype = None
        calls = 0
        def cancel(ctx):
            nonlocal calls
            calls += 1
            return int(calls >= 3)
        callback = cancel_type(cancel)
        try:
            core.lib.bw_set_cancel(callback, None)
            with self.assertRaises(ValidationError):
                core.validate(system())
            self.assertGreaterEqual(calls, 3)
            before = calls
            output = C.create_string_buffer(65)
            core.lib.bw_sha256(b"x" * 0x100000, 0x100000, output)
            self.assertLessEqual(calls - before, 2)
            before = calls
            core.lib.bw_crc32(b"x" * 0x100000, 0x100000)
            self.assertEqual(calls - before, 1)
        finally:
            core.lib.bw_set_cancel(cancel_type(), None)
        self.assertEqual(core.validate(system()).count, 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
