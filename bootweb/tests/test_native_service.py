"""Exercise the exact freestanding C HTTP server with memory-only streams/NAND.

This is NOT the Python demo server. No socket, device, UART or OS NIC is opened.
The service DLL is explicitly marked as a synthetic-base test build.
"""
import ctypes as C
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.platform import ACCEPT, CLOSE, INIT, LISTEN, NOW, POLL, RECV, SEND, Platform
from host.simulator import SparseNand
from fixtures import nand_seed, system


class MemoryHTTP:
    def __init__(self, lib, *, capabilities=0):
        self.lib = lib
        self.nand = SparseNand(nand_seed(), capabilities=capabilities, progress=self.progress)
        self.upload = C.create_string_buffer(2 * 1024**2)
        self.scratch = C.create_string_buffer(0x20000)
        self.token = "a" * 64
        self.queue, self.responses, self.errors = [], [], []
        self.active = None
        self.stopped = False
        self.clock = self.polls = self.requests = self.reboots = 0
        self.on_close = lambda response: None
        self.on_progress = lambda phase, done, total: None
        self.callbacks = (LISTEN(self.listen), ACCEPT(self.accept), RECV(self.recv), SEND(self.send),
                          CLOSE(self.close), POLL(self.poll), NOW(lambda ctx: self.clock),
                          INIT(self.reboot), INIT(self.stop))
        self.platform = Platform(self.nand.port, None, C.addressof(self.upload), len(self.upload),
                                 C.addressof(self.scratch), len(self.scratch), self.token.encode(),
                                 0, *self.callbacks)

    def add(self, method, path, body=b"", *, declared=None, host="192.168.9.1", origin=None,
            token=None, disconnect_reply=False):
        if isinstance(body, str):
            body = body.encode()
        headers = f"{method} {path} HTTP/1.1\r\nHost: {host}\r\n"
        if origin is not None:
            headers += f"Origin: {origin}\r\n"
        if method == "POST":
            headers += (f"Content-Length: {len(body) if declared is None else declared}\r\n"
                        f"X-Bootweb-Token: {self.token if token is None else token}\r\n")
        self.queue.append({"request": headers.encode() + b"\r\n" + body,
                           "method": method, "path": path, "read": 0, "reply": bytearray(),
                           "disconnect_reply": disconnect_reply})

    def listen(self, ctx, address, port):
        if (address, port) != (0xc0a80901, 80):
            self.errors.append("incorrect recovery address")
            return -1
        return 0

    def accept(self, ctx):
        if not self.queue:
            return 0
        if self.active:
            self.errors.append("overlapping HTTP request")
            return -1
        self.requests += 1
        self.active = self.queue.pop(0)
        self.active["fd"] = self.requests
        return self.requests

    def recv(self, ctx, fd, dest, size, timeout):
        if not self.active or fd != self.active["fd"]:
            self.errors.append("receive on wrong stream")
            return -1
        at, data = self.active["read"], self.active["request"]
        take = min(size, len(data) - at, 997)  # deliberately fragmented bodies
        if take:
            C.memmove(dest, data[at:at + take], take)
            self.active["read"] += take
        return take

    def send(self, ctx, fd, source, size):
        if not self.active or fd != self.active["fd"]:
            self.errors.append("send on wrong stream")
            return -1
        if self.active["disconnect_reply"]:
            return -1
        take = min(size, 131)  # server must handle partial sends
        self.active["reply"] += C.string_at(source, take)
        return take

    def close(self, ctx, fd):
        try:
            response, self.active = self.active, None
            if not response or response["fd"] != fd:
                raise ValueError("close on wrong stream")
            raw = bytes(response["reply"])
            if raw:
                header, body = raw.split(b"\r\n\r\n", 1)
                response["status"] = int(header.split(b" ", 2)[1])
                fields = dict(line.split(b": ", 1) for line in header.split(b"\r\n")[1:])
                if int(fields[b"Content-Length"]) != len(body):
                    raise ValueError("incorrect response Content-Length")
                response["body"] = (json.loads(body) if b"application/json" in fields[b"Content-Type"] else body)
                response["headers"] = fields
            self.responses.append(response)
            self.on_close(response)
        except Exception as error:
            self.errors.append(str(error))
            self.stopped = True

    def poll(self, ctx):
        self.polls += 1
        if self.polls % 32 == 0:
            self.clock += 1

    def progress(self, phase, done, total):
        self.on_progress(phase, done, total)

    def reboot(self, ctx):
        self.reboots += 1
        self.stopped = True
        return 0

    def stop(self, ctx):
        # Checked between tasks, not inside erase/write/readback.
        return int(self.stopped or not self.queue)

    def run(self):
        rc = self.lib.bw_serve(C.byref(self.platform))
        if self.errors or self.nand.errors:
            raise AssertionError(self.errors + self.nand.errors)
        return rc


class NativeServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        suffix = ".dll" if sys.platform == "win32" else ".so"
        path = ROOT / ".build" / ("bootweb-service-test" + suffix)
        if not path.is_file():
            raise unittest.SkipTest("build with --test-service to exercise the C HTTP server")
        cls.lib = C.CDLL(str(path))
        cls.lib.bw_test_build.restype = C.c_int
        if cls.lib.bw_test_build() != 1:
            raise RuntimeError("refusing a non-synthetic native service test library")
        cls.lib.bw_serve.argtypes, cls.lib.bw_serve.restype = [C.POINTER(Platform)], C.c_int

    def test_page_locked_upload_origin_and_truncated_body(self):
        transport = MemoryHTTP(self.lib)
        data = system()
        transport.add("GET", "/")
        transport.add("POST", "/api/upload", data, host="evil.invalid")
        transport.add("POST", "/api/upload", data, origin="http://evil.invalid")
        transport.add("POST", "/api/upload", data, token="b" * 64)
        transport.add("POST", "/api/upload", data[:1024], declared=len(data))
        transport.add("GET", "/api/state")
        transport.add("POST", "/api/cancel")
        transport.add("POST", "/api/upload", data)
        digest = hashlib.sha256(data).hexdigest()
        transport.add("POST", "/api/confirm", "2\n" + digest + "\n")
        transport.add("GET", "/api/state")
        self.assertEqual(transport.run(), 0)
        responses = transport.responses
        self.assertEqual(responses[0]["body"], (ROOT / "web/index.html").read_bytes())
        self.assertIn(b"frame-ancestors 'none'", responses[0]["headers"][b"Content-Security-Policy"])
        self.assertTrue(all(item["status"] == 409 for item in responses[1:4]))
        self.assertEqual(responses[5]["body"]["state"], "failed")
        self.assertIn("truncated", responses[5]["body"]["error"])
        self.assertEqual(responses[7]["body"]["state"], "ready")
        self.assertFalse(responses[7]["body"]["can_confirm"])
        self.assertEqual(responses[8]["status"], 409)
        self.assertEqual(responses[9]["body"]["state"], "ready")
        self.assertEqual(transport.nand.events, [])
        # A RAM-trial return must remove the callback to the server's old stack.
        before = transport.polls
        self.lib.bw_sha256.argtypes = [C.c_void_p, C.c_size_t, C.c_void_p]
        self.lib.bw_sha256.restype = None
        self.lib.bw_sha256(bytes(65536), 65536, C.create_string_buffer(65))
        self.assertEqual(transport.polls, before)

    def test_confirm_disconnect_writes_once_and_rejects_busy_mutation(self):
        transport = MemoryHTTP(self.lib, capabilities=0x7f)
        data = system()
        confirm = "1\n" + hashlib.sha256(data).hexdigest() + "\n"
        transport.add("POST", "/api/upload", data)
        transport.add("POST", "/api/confirm", confirm, disconnect_reply=True)
        transport.add("GET", "/api/state")
        transport.add("POST", "/api/reboot")
        transport.add("POST", "/api/confirm", confirm)

        def progress(phase, done, total):
            if phase == "complete":
                transport.add("GET", "/api/state")

        def closed(response):
            state = response.get("body", {})
            if response["path"] == "/api/state" and state.get("phase") == "complete":
                if state["state"] == "writing":
                    transport.add("GET", "/api/state")
                elif state["state"] == "success":
                    transport.add("POST", "/api/confirm", confirm)
                    transport.add("POST", "/api/cancel")

        transport.on_progress, transport.on_close = progress, closed
        self.assertEqual(transport.run(), 0)
        self.assertEqual(transport.reboots, 0)
        replies = transport.responses
        self.assertEqual(replies[2]["body"]["state"], "writing")
        self.assertFalse(replies[2]["body"]["can_reboot"])
        self.assertEqual([item["status"] for item in replies[3:5]], [409, 409])
        self.assertEqual(replies[-3]["body"]["state"], "success")
        self.assertEqual(replies[-2]["status"], 202)  # idempotent confirm, no replay
        self.assertEqual(replies[-1]["status"], 409)
        # A complete system operation erases each good target block ONCE.
        erases = [off for op, off in transport.nand.events if op == "erase"]
        self.assertEqual(len(erases), len(set(erases)))
        self.assertEqual(len(erases), (0xf000000 + 0x100000 + 0x800000) // 0x20000)

    def test_missing_scratch_or_unknown_installed_base_never_listens(self):
        transport = MemoryHTTP(self.lib)
        transport.add("GET", "/api/state")
        transport.platform.scratch = None
        self.assertEqual(transport.run(), -1)
        self.assertEqual(transport.responses, [])
        transport.platform.scratch = C.addressof(transport.scratch)
        transport.nand.blocks[0x20000][0] ^= 1
        self.assertEqual(transport.run(), -1)
        self.assertEqual(transport.responses, [])
        self.assertEqual(transport.nand.events, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
