import ctypes as C
import hashlib
import http.client
import importlib.util
import json
from pathlib import Path
import random
import socket
import struct
import sys
import tarfile
import threading
import time
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.core import BASE, Core, LAYOUTS, Result, Task, ValidationError
from host.simulator import SparseNand
from fixtures import (BOARD, PREFIX, boot, boot_parts, fdt, meta, nand_seed,
                      root, sys_parts, system, tar, trailer, uimage)


class ValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = Core()

    def reject(self, data, message=None, **kwargs):
        with self.assertRaisesRegex(ValidationError, message or "."):
            self.core.validate(data, **kwargs)

    def test_hash_and_crc_vectors(self):
        # Real mkfs.jffs2 clean marker, not a mutually-consistent synthetic CRC.
        self.assertEqual(root(), bytes.fromhex("851903200c000000b1b01ee4"))
        for n in (0, 1, 55, 56, 63, 64, 65, 4096, 65536, 131073):
            data = bytes((i * 37) % 256 for i in range(n))
            result = C.create_string_buffer(65)
            self.core.lib.bw_sha256(data, n, result)
            self.assertEqual(result.value.decode(), hashlib.sha256(data).hexdigest())
            self.assertEqual(self.core.lib.bw_crc32(data, n), zlib.crc32(data))

    def test_layouts_sizes_order_and_fixed_targets(self):
        for layout, cap in (("current240", 240), ("native241", 241), ("recovery237", 237)):
            result = self.core.validate(system(layout=layout), layout=layout)
            self.assertEqual([s.name for s in result.writes], [b"root", b"dtb", b"kernel"])
            self.assertEqual([s.offset for s in result.writes], [0xf00000, 0xa00000, 0x200000])
            self.assertEqual(result.writes[0].capacity, cap * 1024**2)
            self.assertFalse(result.report()["flashable_release"])
        self.assertEqual(self.core.validate(system(format=tarfile.GNU_FORMAT)).count, 3)

    def test_native241_is_strict_and_partition_errors_report_both_ranges(self):
        self.assertEqual(self.core.validate(system(layout="native241"),layout="native241").writes[0].capacity,0xf100000)
        self.reject(system(layout="current240"),"FDT",layout="native241")
        parts=sys_parts("native241")
        parts["dtb"]=fdt("native241",changes={"root":(0xf00000,0xf000000)})
        self.reject(system(parts),r"root expected offset=0x00f00000 size=0x0f100000 actual offset=0x00f00000 size=0x0f000000",layout="native241")
        self.reject(system(layout="recovery237"),"FDT",layout="native241")

    def test_wrong_board_base_and_metadata(self):
        p = sys_parts()
        p["CONTROL"] = b"BOARD=other\n"
        self.reject(system(p), "CONTROL board")
        self.reject(system(), "unknown/unadapted", base="0" * 64)
        for key, bad in (("metadata_version", "1.2"), ("compat_version", "2.0"),
                         ("supported_devices", ["other"]), ("supported_devices", ["skyworth,sk-d840n", "zte,133", "zte,133"]),
                         ("version", {"board": BOARD, "target": "other"})):
            m = meta()
            m[key] = bad
            self.reject(system(metadata=m))
        raw = json.dumps(meta()).encode().replace(b'"metadata_version":', b'"metadata_version":"1.1","metadata_version":')
        self.reject(system(metadata_raw=raw), "JSON")
        raw = json.dumps(meta()).encode().replace(b'"version"', b'"ver\\u0073ion"')
        self.reject(system(metadata_raw=raw), "JSON")

    def test_fdt_geometry_alias_and_protection(self):
        cases = [dict(board="other"), dict(changes={"root": (0xf00000, 0xf100000)}),
                 dict(changes={"kernel": (0x180000, 0x800000)}),
                 dict(alias_changes={"mtd5": b"/flash/partitions/partition@200000\0"}),
                 dict(extra={"extra": (0xfe00000, 0x20000)}), dict(readonly=False),
                 dict(alias_changes={"mtd05": b"/flash/partitions/partition@f00000\0"}),
                 dict(alias_changes={"mtd7": b"/flash/partitions/partition@f00000\0"})]
        for case in cases:
            with self.subTest(case=case):
                p = sys_parts()
                p["dtb"] = fdt(**case)
                self.reject(system(p), "FDT")
        self.reject(system(), "FDT", layout="recovery237")
        self.reject(system(layout="native241"), "FDT", layout="current240")
        for off, word in ((0, 0), (4, 39), (8, 0xfffffffc), (12, 0), (16, 56), (36, 0xffffffff)):
            p = sys_parts()
            data = bytearray(p["dtb"])
            struct.pack_into(">I", data, off, word)
            p["dtb"] = bytes(data)
            self.reject(system(p), "FDT")

    def test_bad_crc_and_truncated_kernel(self):
        for off, text in ((4, "header CRC"), (70, "data CRC")):
            p = sys_parts()
            data = bytearray(p["kernel"])
            data[off] ^= 1
            p["kernel"] = bytes(data)
            self.reject(system(p), text)
        for arg in (dict(load=0x88000000), dict(entry=0x88000000), dict(arch=2), dict(comp=0), dict(size=1)):
            p = sys_parts()
            p["kernel"] = uimage(**arg)
            self.reject(system(p), "uImage")
        p = sys_parts()
        p["root"] = b"\x85\x19" + bytes(10)
        self.reject(system(p), "JFFS2")

    def test_fdt_bootargs_and_disabled_ancestors_cannot_override_layout(self):
        for case in (dict(bootargs=None), dict(bootargs="root=/dev/mtdblock4 rootfstype=jffs2"),
                     dict(bootargs="root=/dev/mtdblock5 rootfstype=ubifs"),
                     dict(bootargs="root=/dev/mtdblock5 rootfstype=jffs2 mtdparts=flash:-(root)"),
                     dict(bootargs="root=/dev/mtdblock5 rootfstype=jffs2 ubi.mtd=5"),
                     dict(bootargs="root=/dev/mtdblock5 rootfstype=jffs2 root=/dev/mtdblock5"),
                     dict(flash_disabled=True), dict(table_disabled=True), dict(extra_root=True),
                     dict(duplicate_aliases=True), dict(duplicate_chosen=True), dict(ambiguous_table=True)):
            p = sys_parts()
            p["dtb"] = fdt(**case)
            with self.subTest(case=case):
                self.reject(system(p), "FDT")

    def test_each_capacity_and_oversized_tar_size(self):
        for part, size in (("kernel", 0x800001), ("dtb", 0x100001), ("root", 0xf000001)):
            p = sys_parts()
            if part == "root":
                # Advertise the oversized member without allocating 240 MiB.
                data = bytearray(tar([(PREFIX + "/root", b"R")]))
                data[124:136] = f"{size:011o}\0".encode()
                data[148:156] = b"        "
                data[148:156] = f"{sum(data[:512]):06o}\0 ".encode()
                self.reject(trailer(bytes(data), bytes(8) + json.dumps(meta()).encode()), "truncated")
                continue
            p[part] = bytes(size)
            self.reject(system(p), "capacity")

    def test_missing_duplicate_special_and_paths(self):
        for name in ("CONTROL", "kernel", "dtb", "root"):
            p = sys_parts()
            del p[name]
            self.reject(system(p), "missing")
            self.reject(system(extras=[(PREFIX + "/" + name, b"X")]), "duplicate")
        for path in ("../outside", "/etc/shadow", PREFIX + "/../CONTROL", "CONTROL", PREFIX + "/script", PREFIX + "//root"):
            self.reject(system(extras=[(path, b"X")]), "member/path")
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.FIFOTYPE, tarfile.XHDTYPE):
            info = tarfile.TarInfo(PREFIX + "/root")
            info.type = kind
            self.reject(system(extras=[("ignored", info)]))

    def test_fwtool_crc_chain_and_archive_end(self):
        valid = system()
        data = bytearray(valid)
        data[1024] ^= 1
        self.reject(bytes(data), "fwtool CRC")
        self.core.validate(trailer(valid, b"not a verified cryptographic signature", 0))
        for count in (1, 15, 16, 512, 1024, 4096):
            self.reject(valid[:-count])
        # Nonzero data after the first tar end, with a repaired outer CRC.
        data = bytearray(tar([(PREFIX + "/" + k, v) for k, v in sys_parts().items()]))
        data[-512] = 1
        self.reject(trailer(bytes(data), bytes(8) + json.dumps(meta()).encode()), "tar end")
        self.reject(tar([(PREFIX + "/" + k, v) for k, v in sys_parts().items()]), "fwtool")

    def test_uboot_payload_control_and_hash(self):
        good = boot(self.core.base)
        r = self.core.validate(good)
        self.assertEqual((r.count, r.writes[0].offset, r.writes[0].capacity), (1, 0x20000, 0x160000))
        for size in (1, 0x15ffff, 0x160001, 0x200000):
            self.reject(boot(self.core.base, payload=b"C" * size), "fixed stage2")
        self.reject(boot("0" * 64), "CONTROL")
        for field in (b"BOARD=" + BOARD.encode(), b"BASE_SHA256=" + self.core.base.encode(), b"PAYLOAD_BYTES=1441792"):
            p = boot_parts(self.core.base)
            p["CONTROL"] = p["CONTROL"].replace(field, field + b"X")
            self.reject(tar(p.items()), "CONTROL")
        for extra in (b"OFFSET=0\n", b"BOARD=" + BOARD.encode() + b"\n"):
            p = boot_parts(self.core.base)
            p["CONTROL"] += extra
            self.reject(tar(p.items()), "CONTROL")
        p = boot_parts(self.core.base)
        p["sha256sums"] = b"0" + p["sha256sums"][1:]
        self.reject(tar(p.items()), "SHA256")
        self.reject(tar(list(boot_parts(self.core.base).items()) + [("uboot.bin", b"X")]), "duplicate")

    def test_random_inputs_and_mutations_are_bounded(self):
        rng = random.Random(840)
        for _ in range(150):
            self.reject(rng.randbytes(rng.randrange(1536, 65536)))
        good = system()
        for _ in range(80):
            data = bytearray(good)
            i = rng.randrange(len(data) - 16)
            data[i] ^= rng.randrange(1, 256)
            self.reject(bytes(data))


class EngineHarness:
    @classmethod
    def setUpClass(cls):
        cls.core = Core(testing=True)

    def execute(self, data, nand, layout="current240"):
        error, scratch = C.create_string_buffer(192), C.create_string_buffer(0x20000)
        rc = self.core.lib.bw_execute(data, len(data), self.core.base.encode(), LAYOUTS[layout],
                                       C.byref(nand.port), scratch, len(scratch), error)
        self.assertEqual(nand.errors, [])
        return rc, error.value.decode()


class EngineTests(EngineHarness, unittest.TestCase):
    def test_test_and_production_profiles_cannot_mix(self):
        with self.assertRaises(ValidationError):
            Core().validate(boot(self.core.base))
        self.assertNotEqual(self.core.base, BASE)

    def test_system_write_order_readback_and_protected_bytes(self):
        seed, p = nand_seed(), sys_parts()
        nand = SparseNand(seed)
        rc, error = self.execute(system(p), nand)
        self.assertEqual(rc, 0, error)
        writes = [off for op, off in nand.events if op == "write"]
        groups = []
        for off in writes:
            part = "root" if off >= 0xf00000 else "dtb" if off >= 0xa00000 else "kernel"
            if not groups or part != groups[-1]:
                groups.append(part)
        self.assertEqual(groups, ["root", "dtb", "kernel"])
        for label, off in (("root", 0xf00000), ("dtb", 0xa00000), ("kernel", 0x200000)):
            self.assertEqual(nand.read(off, len(p[label])), p[label])
        self.assertEqual(nand.read(0, 0x200000), seed)

    def test_uboot_exact_window_preserves_protected_hashes(self):
        seed = nand_seed()
        nand = SparseNand(seed)
        rc, error = self.execute(boot(self.core.base), nand)
        self.assertEqual(rc, 0, error)
        self.assertEqual(nand.read(0x20000, 0x160000), b"C" * 0x160000)
        self.assertEqual(nand.read(0, 0x20000), seed[:0x20000])
        self.assertEqual(nand.read(0x180000, 0x80000), seed[0x180000:])

    def test_all_rejection_paths_erase_nothing(self):
        for caps, layout in ((0, "current240"), (7, "native241"), (7, "recovery237")):
            nand = SparseNand(nand_seed(), capabilities=caps)
            rc, _ = self.execute(system(layout=layout), nand, layout)
            self.assertNotEqual(rc, 0)
            self.assertEqual(nand.events, [])
        for defect in ("unknown-base", "kernel-capacity", "boot-badblock", "ecc-read", "container"):
            nand = SparseNand(nand_seed())
            data = system()
            if defect == "unknown-base":
                nand.blocks[0x20000][0] ^= 1
            if defect == "kernel-capacity":
                nand.bad.update(range(0x200000, 0xa00000, 0x20000))
            if defect == "boot-badblock":
                nand.bad.add(0x20000)
                data = boot(self.core.base)
            if defect == "ecc-read":
                nand.fail_read.add(0xb00000)
            if defect == "container":
                nand.port.verify_boot = type(nand.port.verify_boot)()
                data = boot(self.core.base)
            rc, _ = self.execute(data, nand)
            self.assertNotEqual(rc, 0, defect)
            self.assertEqual(nand.events, [], defect)

    def test_badblock_skip_stays_inside_partition(self):
        nand = SparseNand(nand_seed())
        nand.bad.add(0xf00000)
        p = sys_parts()
        rc, error = self.execute(system(p), nand)
        self.assertEqual(rc, 0, error)
        self.assertNotIn(("erase", 0xf00000), nand.events)
        self.assertEqual(nand.read(0xf20000, len(p["root"])), p["root"])

    def test_io_and_readback_failures_never_success(self):
        for defect in ("erase", "write", "readback", "protected"):
            nand = SparseNand(nand_seed())
            if defect == "erase":
                nand.fail_erase.add(0xf20000)
            elif defect == "write":
                nand.fail_write.add(0xf00000)
            elif defect == "readback":
                nand.corrupt_readback = 0xf00000
            else:
                nand.mutate_protected = True
            rc, error = self.execute(system(), nand)
            self.assertNotEqual(rc, 0, (defect, error))
            if defect != "protected":
                self.assertFalse(any(op == "write" and off < 0xf00000 for op, off in nand.events))

    def test_failed_boot_update_still_audits_protected_regions(self):
        nand = SparseNand(nand_seed())
        nand.mutate_protected = True
        nand.fail_write.add(0x20000)
        rc, error = self.execute(boot(self.core.base), nand)
        self.assertNotEqual(rc, 0)
        self.assertIn("protected region hash changed", error)
        self.assertFalse(any(op == "write" for op, off in nand.events))


class TaskTests(EngineHarness, unittest.TestCase):
    def prepare(self, data):
        self.task, self.buffer = Task(), C.create_string_buffer(len(data))
        self.token = b"a" * 64
        api = self.core.lib
        self.assertEqual(api.bw_task_init(C.byref(self.task), self.buffer, len(self.buffer), self.core.base.encode(), 0, self.token), 0)
        self.assertEqual(api.bw_task_begin(C.byref(self.task), len(data), 1), 0)
        return api

    def test_full_upload_then_confirm_and_idempotence(self):
        data = system()
        api = self.prepare(data)
        self.assertNotEqual(api.bw_task_confirm(C.byref(self.task), 1, b"0" * 64, self.token), 0)
        split = len(data) // 2
        self.assertEqual(api.bw_task_feed(C.byref(self.task), data[:split], split), 0)
        self.assertEqual(api.bw_task_feed(C.byref(self.task), data[split:], len(data) - split), 0)
        self.assertEqual(api.bw_task_finish(C.byref(self.task)), 0)
        self.assertEqual(self.task.state, 3)
        digest = self.task.result.package_sha256
        self.assertNotEqual(api.bw_task_confirm(C.byref(self.task), 2, digest, self.token), 0)
        self.assertNotEqual(api.bw_task_confirm(C.byref(self.task), 1, digest, b"b" * 64), 0)
        self.assertEqual(api.bw_task_confirm(C.byref(self.task), 1, digest, self.token), 0)
        self.assertEqual(api.bw_task_confirm(C.byref(self.task), 1, digest, self.token), 0)
        nand, scratch = SparseNand(nand_seed()), C.create_string_buffer(0x20000)
        self.assertEqual(api.bw_task_run(C.byref(self.task), C.byref(nand.port), scratch, len(scratch)), 0)
        count = len(nand.events)
        self.assertEqual(self.task.state, 6)
        self.assertEqual(api.bw_task_confirm(C.byref(self.task), 1, digest, self.token), 0)
        self.assertNotEqual(api.bw_task_run(C.byref(self.task), C.byref(nand.port), scratch, len(scratch)), 0)
        self.assertEqual(len(nand.events), count)
        self.assertNotEqual(api.bw_task_cancel(C.byref(self.task), self.token), 0)

    def test_disconnect_partial_upload_has_no_ready_state(self):
        data = system()
        api = self.prepare(data)
        api.bw_task_feed(C.byref(self.task), data[:1024], 1024)
        self.assertNotEqual(api.bw_task_finish(C.byref(self.task)), 0)
        self.assertEqual(self.task.state, 7)
        self.assertIn(b"truncated", self.task.error)
        self.assertEqual(api.bw_task_cancel(C.byref(self.task), self.token), 0)
        self.assertEqual(self.task.state, 0)

    def test_confirm_freezes_exact_bytes(self):
        data = system()
        api = self.prepare(data)
        api.bw_task_feed(C.byref(self.task), data, len(data))
        api.bw_task_finish(C.byref(self.task))
        api.bw_task_confirm(C.byref(self.task), 1, self.task.result.package_sha256, self.token)
        # A mutation need not remain valid: the approval is bound to ALL bytes.
        self.buffer[100] = b"X"
        nand, scratch = SparseNand(nand_seed()), C.create_string_buffer(0x20000)
        self.assertNotEqual(api.bw_task_run(C.byref(self.task), C.byref(nand.port), scratch, len(scratch)), 0)
        self.assertEqual(nand.events, [])
        self.assertIn(b"frozen", self.task.error)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = Core()

    def header(self, method="GET", path="/api/state", extra=""):
        return (f"{method} {path} HTTP/1.1\r\nHost: 192.168.9.1\r\n{extra}\r\n").encode()

    def test_bounded_http_contract(self):
        self.assertEqual(self.core.parse_http(self.header()).path, b"/api/state")
        good = "Content-Length: 1536\r\nX-Bootweb-Token: " + "a" * 64 + "\r\n"
        self.assertEqual(self.core.parse_http(self.header("POST", "/api/upload", good)).length, 1536)
        cases = [self.header("DELETE"), self.header(path="/api/cmd"), self.header(path="/api/state?x=1"),
                 self.header(extra="Content-Length: 1\r\n"),
                 self.header("POST", "/api/upload"), self.header("POST", "/api/upload", good + "Content-Length: 1536\r\n"),
                 self.header("POST", "/api/upload", good + "Transfer-Encoding: chunked\r\n"),
                 self.header("POST", "/api/upload", good + "Expect: 100-continue\r\n"),
                 self.header(extra="Host: evil.invalid\r\n"), self.header(extra="Origin: x\r\nOrigin: y\r\n"),
                 self.header(extra=" folded: 1\r\n"), self.header(extra="X-Test: a\nb\r\n"),
                 self.header() + b"GET / HTTP/1.1\r\n\r\n", b"X" * 4097]
        for data in cases:
            with self.subTest(data=data[:100]):
                with self.assertRaises(ValidationError):
                    self.core.parse_http(data)

    def test_real_loopback_server_upload_fail_and_locked_confirm(self):
        spec = importlib.util.spec_from_file_location("bootweb_demo", ROOT / "tools/serve_demo.py")
        demo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(demo)
        with demo.Demo(port=0) as server:
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            thread.start()
            port = server.server_address[1]
            def request(method, path, body=None, headers=None):
                con = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                con.request(method, path, body=body, headers=headers or {})
                response = con.getresponse()
                result = response.read()
                con.close()
                return response.status, result
            try:
                code, page = request("GET", "/")
                self.assertEqual(code, 200)
                self.assertLessEqual(len(page), 32768)
                code, data = request("GET", "/api/state")
                state = json.loads(data)
                self.assertFalse(state["can_confirm"])
                headers = {"X-Bootweb-Token": state["token"], "Content-Type": "application/octet-stream"}
                code, data = request("POST", "/api/upload", system(), headers)
                state = json.loads(data)
                self.assertEqual((code, state["state"]), (200, "ready"))
                code, _ = request("POST", "/api/confirm", state["id"] + "\n" + state["result"]["sha256"] + "\n", headers)
                self.assertEqual(code, 409)
                code, _ = request("POST", "/api/upload", system(), headers)
                self.assertEqual(code, 409)
                self.assertEqual(request("POST", "/api/cancel", b"", headers)[0], 200)
                bad = bytearray(system())
                bad[100] ^= 1
                code, data = request("POST", "/api/upload", bytes(bad), headers)
                self.assertEqual((code, json.loads(data)["state"]), (409, "failed"))
                self.assertEqual(request("POST", "/api/cancel", b"", headers)[0], 200)
                self.assertEqual(request("GET", "/api/state", headers={"Host": "evil.invalid"})[0], 409)
            finally:
                server.shutdown()
                thread.join(2)

    def test_disconnect_after_confirmation_continues_once_and_status_stays_available(self):
        spec = importlib.util.spec_from_file_location("bootweb_demo_sim", ROOT / "tools/serve_demo.py")
        demo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(demo)
        with demo.Demo(port=0, boot=nand_seed(), core=Core(testing=True)) as server:
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            thread.start()
            port = server.server_address[1]
            stopped, resume = threading.Event(), threading.Event()
            first = [True]
            original = server.nand.progress

            def progress(phase, done, total):
                original(phase, done, total)
                if phase == "erase" and done == 0 and first[0]:
                    first[0] = False
                    stopped.set()
                    resume.wait(3)

            server.nand.progress = progress

            def request(method, path, body=None):
                con = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                headers = {"X-Bootweb-Token": server.token} if method == "POST" else {}
                con.request(method, path, body, headers)
                response = con.getresponse()
                data = json.loads(response.read())
                con.close()
                return response.status, data

            def detached(path, body, declared=None):
                with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
                    head = (f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nX-Bootweb-Token: {server.token}\r\n"
                            f"Content-Length: {len(body) if declared is None else declared}\r\n\r\n").encode()
                    connection.sendall(head + body)
                    connection.shutdown(socket.SHUT_WR)
                    # Leave without reading; writing must not depend on this socket.

            try:
                data = system()
                detached("/api/upload", data[:1024], declared=len(data))
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    if request("GET", "/api/state")[1]["state"] == "failed":
                        break
                    time.sleep(0.01)
                self.assertEqual(request("GET", "/api/state")[1]["state"], "failed")
                self.assertEqual(server.nand.events, [])
                self.assertEqual(request("POST", "/api/cancel", b"")[0], 200)
                code, state = request("POST", "/api/upload", data)
                self.assertEqual((code, state["state"]), (200, "ready"))
                confirm = (state["id"] + "\n" + state["result"]["sha256"] + "\n" + state["installed_sha256"] + "\n").encode()
                detached("/api/confirm", confirm)
                self.assertTrue(stopped.wait(2), "writer did not continue after browser disconnect")
                self.assertEqual(request("GET", "/api/state")[1]["state"], "writing")
                self.assertEqual(request("POST", "/api/confirm", confirm)[0], 202)
                self.assertEqual(request("POST", "/api/cancel", b"")[0], 409)
                self.assertEqual(request("POST", "/api/upload", data)[0], 409)
                resume.set()
                server.worker.join(3)
                self.assertFalse(server.worker.is_alive())
                self.assertEqual(request("GET", "/api/state")[1]["state"], "success")
                events = len(server.nand.events)
                self.assertEqual(request("POST", "/api/confirm", confirm)[0], 202)
                self.assertEqual(len(server.nand.events), events)
                self.assertEqual(server.nand.errors, [])
            finally:
                resume.set()
                if server.worker:
                    server.worker.join(3)
                server.shutdown()
                thread.join(2)


class EntryTests(unittest.TestCase):
    def test_reset_entry_stopped_or_reversing_clock_is_bounded(self):
        core=Core();PRESS=C.CFUNCTYPE(C.c_int,C.c_void_p);NOW=C.CFUNCTYPE(C.c_uint64,C.c_void_p);POLL=C.CFUNCTYPE(None,C.c_void_p)
        class Entry(C.Structure):
            _fields_=[("context",C.c_void_p),("verified",C.c_int),("pressed",PRESS),("now",NOW),("poll",POLL)]
        core.lib.bw_reset_entry.argtypes=[C.POINTER(Entry)];core.lib.bw_reset_entry.restype=C.c_int
        for backwards in (False,True):
            calls=[0]
            def now(ctx):calls[0]+=1;return 1000-calls[0] if backwards else 1000
            callbacks=PRESS(lambda ctx:1),NOW(now),POLL(lambda ctx:None)
            io=Entry(None,1,*callbacks)
            self.assertEqual(core.lib.bw_reset_entry(C.byref(io)),-1);self.assertLessEqual(calls[0],1000002)

    def test_reset_hold_requires_measured_gpio_and_continuous_three_seconds(self):
        core = Core()
        PRESS = C.CFUNCTYPE(C.c_int, C.c_void_p)
        NOW = C.CFUNCTYPE(C.c_uint64, C.c_void_p)
        POLL = C.CFUNCTYPE(None, C.c_void_p)
        class Entry(C.Structure):
            _fields_ = [("context", C.c_void_p), ("verified", C.c_int),
                        ("pressed", PRESS), ("now", NOW), ("poll", POLL)]
        core.lib.bw_reset_entry.argtypes = [C.POINTER(Entry)]
        core.lib.bw_reset_entry.restype = C.c_int
        for verified, release, expected in ((False, 5000, -1), (True, 0, 0),
                                             (True, 2999, 0), (True, 3001, 1), (True, -1, -1)):
            clock, reads = [0], [0]
            def press(ctx):
                reads[0] += 1
                return -1 if release == -1 else int(clock[0] < release)
            def poll(ctx):
                clock[0] += 1
            callbacks = PRESS(press), NOW(lambda ctx: clock[0]), POLL(poll)
            io = Entry(None, int(verified), *callbacks)
            self.assertEqual(core.lib.bw_reset_entry(C.byref(io)), expected)
            if not verified:
                self.assertEqual(reads[0], 0)


class SourceTests(unittest.TestCase):
    def test_page_and_native_hard_stop(self):
        page = (ROOT / "web/index.html").read_bytes()
        self.assertLessEqual(len(page), 32768)
        for bad in (b"cdn.", b"<script src=", b"@import", b"eval(", b"innerHTML", b"/api/cmd", b"/api/exec"):
            self.assertNotIn(bad, page)
        self.assertIn("return -1;", (ROOT / "adapter/vendor_unverified.c").read_text())
        library = (ROOT / "tools/build.py").read_text()
        self.assertIn('"bootweb-test"', library)


if __name__ == "__main__":
    unittest.main(verbosity=2)
