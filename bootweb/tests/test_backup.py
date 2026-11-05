"""Native backup service/capture fault injection. No device or socket backend."""
import ctypes as C
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.backup import CHUNK, ERASE, HEADER, decode_chunk, download_backup
from host.core import BAD, Port, READ
from host.platform import Platform
from test_native_service import MemoryHTTP
from fixtures import system


def frame(offset, data, bad=0):
    return (b"BWROBK01" + struct.pack("<IIII", offset, len(data), bad, HEADER) +
            hashlib.sha256(data).hexdigest().encode() + bytes(40) + data)


class BackupNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = C.CDLL(str(ROOT / ".build" / ("bootweb-ram-backup-test.dll" if sys.platform == "win32" else "bootweb-ram-backup-test.so")))
        cls.lib.bw_serve.argtypes, cls.lib.bw_serve.restype = [C.POINTER(Platform)], C.c_int
        cls.lib.bw_backup_capture.argtypes = [C.POINTER(Port), C.c_uint32, C.c_uint32, C.c_void_p, C.c_size_t, C.c_void_p]
        cls.lib.bw_backup_capture.restype = C.c_int
        cls.lib.bw_test_ram_read_allowed.argtypes = [C.c_uint32, C.c_size_t]
        cls.lib.bw_backup_finish_reset.argtypes, cls.lib.bw_backup_finish_reset.restype = [], None
        cls.lib.bw_backup_finished.argtypes, cls.lib.bw_backup_finished.restype = [], C.c_int

    def test_authenticated_backup_completion_is_idle_only_and_never_reboots(self):
        for fault in ("token", "body", "busy", "none"):
            with self.subTest(fault=fault):
                self.lib.bw_backup_finish_reset()
                io = MemoryHTTP(self.lib, capabilities=0xff)
                if fault == "busy":io.add("POST", "/api/upload", system())
                io.add("POST", "/api/backup-finish", b"x" if fault == "body" else b"",
                       token="b"*64 if fault == "token" else io.token)
                self.assertEqual(io.run(), 0)
                self.assertEqual(io.responses[-1]["status"], 200 if fault == "none" else 409)
                self.assertEqual(self.lib.bw_backup_finished(), int(fault == "none"))
                self.assertEqual(io.nand.events, [])
                self.assertEqual(io.reboots, 0)
        self.lib.bw_backup_finish_reset()

    def test_real_service_backup_uses_physical_ecc_and_never_enables_confirm(self):
        io = MemoryHTTP(self.lib, capabilities=0x7f)
        io.nand.bad.add(0x240000)
        io.add("GET", "/api/state")
        io.add("POST", "/api/backup", f"{0x200000}\n{CHUNK}\n")
        io.add("POST", "/api/confirm", b"1\n" + b"a"*64 + b"\n" + b"a"*64 + b"\n")
        io.add("POST", "/api/reboot")
        self.assertEqual(io.run(), 0)
        self.assertTrue(io.responses[0]["body"]["can_backup"])
        self.assertFalse(io.responses[0]["body"]["can_confirm"])
        data, metadata = decode_chunk(io.responses[1]["body"], 0x200000, CHUNK)
        self.assertEqual(data, b"\xff" * CHUNK)
        self.assertEqual(metadata["bad_blocks"], [0x240000])
        self.assertEqual([r["status"] for r in io.responses[2:]], [409, 409])
        self.assertEqual(io.nand.events, [])
        self.assertEqual(io.reboots, 0)
        for offset in (0x200000, 0xa00000, 0xf00000, 0xffe0000):
            self.assertEqual(self.lib.bw_test_ram_read_allowed(offset, ERASE), 1)

    def test_invalid_auth_range_and_busy_task_never_return_a_backup(self):
        for body in ("0\n0\n", "1\n131072\n", "0\n1048577\n", "268435456\n131072\n",
                     "4294967296\n131072\n", "-1\n131072\n", "0\n131072\nextra", "0\n", ""):
            io = MemoryHTTP(self.lib)
            io.add("POST", "/api/backup", body)
            self.assertEqual(io.run(), 0)
            self.assertEqual(io.responses[0]["status"], 409, body)
        io = MemoryHTTP(self.lib)
        io.add("POST", "/api/backup", "0\n131072\n", token="b"*64)
        io.add("POST", "/api/upload", system())
        io.add("POST", "/api/backup", "0\n131072\n")
        self.assertEqual(io.run(), 0)
        self.assertEqual([r["status"] for r in io.responses], [409, 200, 409])
        self.assertEqual(io.nand.events, [])

    def test_bad_query_or_ecc_error_is_failure_not_a_bad_block_placeholder(self):
        for fault in ("read", "query"):
            io = MemoryHTTP(self.lib)
            if fault == "read":
                io.nand.fail_read.add(0x200000)
            else:
                original = io.nand.callbacks[0]
                callback = BAD(lambda ctx, off: -1 if off == 0x200000 else original(ctx, off))
                io.nand.port.is_bad = callback
                io.platform.nand.is_bad = callback
            io.add("POST", "/api/backup", f"{0x200000}\n{ERASE}\n")
            self.assertEqual(io.run(), 0)
            self.assertEqual(io.responses[0]["status"], 409)
            self.assertEqual(io.nand.events, [])

    def test_known_bad_block_is_not_read_or_remapped(self):
        io, reads = MemoryHTTP(self.lib), []
        original = io.nand.callbacks[1]
        def read(ctx, off, dest, length):
            reads.append(off)
            return original(ctx, off, dest, length)
        callback = READ(read)
        io.nand.port.read = callback
        io.nand.bad.add(0x220000)
        buffer, error = C.create_string_buffer(HEADER+CHUNK), C.create_string_buffer(192)
        self.assertEqual(self.lib.bw_backup_capture(C.byref(io.nand.port), 0x200000, CHUNK,
                                                    buffer, len(buffer), error), 0)
        _, metadata = decode_chunk(buffer.raw, 0x200000, CHUNK)
        self.assertNotIn(0x220000, reads)
        self.assertEqual(reads, [0x200000+i*ERASE for i in range(8) if i != 1])
        self.assertEqual(metadata["bad_blocks"], [0x220000])

    def test_deadline_during_ecc_capture_sends_no_success_or_partial_frame(self):
        io = MemoryHTTP(self.lib)
        original = io.nand.callbacks[1]
        def read(ctx, off, dest, length):
            result = original(ctx, off, dest, length)
            if off == 0x200000:
                io.stopped = True
            return result
        callback = READ(read)
        io.platform.nand.read = callback
        io.add("POST", "/api/backup", f"{0x200000}\n{CHUNK}\n")
        self.assertEqual(io.run(), 0)
        self.assertFalse(io.responses[0]["reply"])
        self.assertEqual(io.nand.events, [])
        self.assertEqual(io.reboots, 0)

    def test_sparse_wire_packing_still_hashes_and_exports_full_physical_data(self):
        io = MemoryHTTP(self.lib)
        io.upload = C.create_string_buffer(4*CHUNK)
        io.platform.upload, io.platform.upload_capacity = C.addressof(io.upload), len(io.upload)
        io.nand.bad.add(0x220000)
        io.add("POST", "/api/backup", f"{0x200000}\n{CHUNK}\n")
        self.assertEqual(io.run(), 0)
        packet = io.responses[0]["body"]
        self.assertEqual(packet[:8], b"BWROBK02")
        self.assertLess(len(packet), 1024)
        data, metadata = decode_chunk(packet, 0x200000, CHUNK)
        self.assertEqual(data, b"\xff"*CHUNK)
        self.assertEqual(metadata["bad_blocks"], [0x220000])
        self.assertEqual(io.nand.events, [])

    def test_dense_data_is_not_expanded_or_modified_by_wire_packing(self):
        io = MemoryHTTP(self.lib)
        io.upload = C.create_string_buffer(4*CHUNK)
        io.platform.upload, io.platform.upload_capacity = C.addressof(io.upload), len(io.upload)
        pattern = bytes(range(256))*(ERASE//256)
        for off in range(0x200000, 0x300000, ERASE):
            io.nand.blocks[off] = bytearray(pattern)
        io.add("POST", "/api/backup", f"{0x200000}\n{CHUNK}\n")
        self.assertEqual(io.run(), 0)
        packet = io.responses[0]["body"]
        self.assertEqual(packet[:8], b"BWROBK01")
        self.assertEqual(decode_chunk(packet, 0x200000, CHUNK)[0], pattern*8)
        self.assertEqual(io.nand.events, [])


class BackupCaptureTests(unittest.TestCase):
    def test_compressed_frame_run_length_codec_and_reserved_fields_are_bounded(self):
        data = b"A"*ERASE
        encoded = b"\xff\xffA"*3 + b"\x02\x00A"
        header = bytearray(frame(0, data)[:HEADER])
        header[:8] = b"BWROBK02"
        struct.pack_into("<II", header, 88, 1, len(encoded))
        # Three maximal runs exceed one erase block: fail before allocating
        # data beyond the physical chunk, even if its digest looks plausible.
        with self.assertRaisesRegex(ValueError, "overflows"):
            decode_chunk(header+encoded, 0, ERASE)
        encoded = b"\xff\xffA"*2 + b"\x02\x00A"
        data = b"A"*ERASE
        header = bytearray(frame(0, data)[:HEADER])
        header[:8] = b"BWROBK02"
        struct.pack_into("<II", header, 88, 1, len(encoded))
        packet = header+encoded
        self.assertEqual(decode_chunk(packet, 0, ERASE)[0], data)
        for position in (88, 92, 96, HEADER, HEADER+1):
            invalid = bytearray(packet)
            invalid[position] ^= 1
            with self.assertRaises(ValueError):
                decode_chunk(invalid, 0, ERASE)

    def test_frame_rejects_truncation_corruption_range_and_false_badblock_data(self):
        packet = frame(0, b"A"*ERASE)
        self.assertEqual(decode_chunk(packet, 0, ERASE)[0], b"A"*ERASE)
        mutations = [packet[:-1], packet+b"X", frame(ERASE, b"A"*ERASE), frame(0, b"A"*ERASE, 1)]
        for position in (0, 20, 24, 88, HEADER):
            changed = bytearray(packet)
            changed[position] ^= 1
            mutations.append(changed)
        mutations.append(frame(0, b"\xff"*ERASE, 2))
        for bad in mutations:
            with self.assertRaises(ValueError):
                decode_chunk(bad, 0, ERASE)

    def test_saved_capture_is_hashed_but_partial_size_is_not_a_full_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            report = download_backup(lambda off, size, timeout: frame(off, b"B"*size),
                                     Path(temp)/"capture", 100, clock=lambda: 0, size=2*CHUNK)
            self.assertTrue(report["capture_complete"])
            self.assertFalse(report["full_flash_backup"])
            self.assertFalse(report["configuration_exported"])
            self.assertEqual(report["sha256"], hashlib.sha256(b"B"*(2*CHUNK)).hexdigest())

    def test_disconnect_hash_mismatch_and_deadline_preserve_failed_evidence_without_retry(self):
        for failure in ("disconnect", "corrupt", "deadline"):
            with tempfile.TemporaryDirectory() as temp:
                calls = []
                def reader(off, size, timeout):
                    calls.append(off)
                    if failure == "disconnect":
                        raise ConnectionError("lost UART-bound peer")
                    return frame(off, b"B"*size)[:-1]
                with self.assertRaises((ConnectionError, ValueError, TimeoutError)):
                    download_backup(reader, Path(temp)/"failed", 5 if failure=="deadline" else 100,
                                    clock=lambda: 0, size=CHUNK)
                report = json.loads((Path(temp)/"failed/manifest.json").read_text(encoding="utf-8"))
                self.assertFalse(report["capture_complete"])
                self.assertFalse(report["full_flash_backup"])
                self.assertLessEqual(len(calls), 1)

    def test_changed_protected_repeat_never_reports_a_complete_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            calls = []
            def reader(off, size, timeout):
                calls.append(off)
                return frame(off, (b"A" if len(calls)>2 else b"B")*size)
            with self.assertRaisesRegex(ValueError, "protected region changed"):
                download_backup(reader, Path(temp)/"changed", 100, clock=lambda: 0, size=2*CHUNK)
            report = json.loads((Path(temp)/"changed/manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(report["capture_complete"])
            self.assertFalse(report["protected_repeat_pass"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
