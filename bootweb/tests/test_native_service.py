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
from host.platform import ACCEPT, CLOSE, INIT, LISTEN, NOW, POLL, RECV, SEND, Platform, UPDATE_LOCK, PREPARE_UPDATE, FINISH_UPDATE
from host.core import PREPARE_UBOOT, VERIFY_IDENTITY, Task
from host.simulator import SparseNand
from fixtures import boot, nand_seed, system


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
        # Queue exhaustion is not a deadline until the active response closes.
        return int(self.stopped or (not self.queue and self.active is None))

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
        cls.lib.bw_base_hash.restype = C.c_char_p
        if cls.lib.bw_test_build() != 1:
            raise RuntimeError("refusing a non-synthetic native service test library")
        cls.lib.bw_serve.argtypes, cls.lib.bw_serve.restype = [C.POINTER(Platform)], C.c_int

    def test_native241_continuous_ack_is_required_and_bound_to_current_task(self):
        io=MemoryHTTP(self.lib,capabilities=0xff);io.platform.layout=1;io.platform.require_continuous_power=1
        data=system(layout="native241");digest=hashlib.sha256(data).hexdigest();installed=self.lib.bw_base_hash().decode()
        io.add("POST","/api/upload",data)
        for body in (f"1\n{digest}\n{installed}\n",f"2\n{digest}\n{installed}\ncontinuous-power\n",
                     f"1\n{'0'*64}\n{installed}\ncontinuous-power\n",f"1\n{digest}\n{installed}\nperiodic-power\n"):
            io.add("POST","/api/confirm",body)
        io.add("POST","/api/reboot");io.add("GET","/api/state")
        self.assertEqual(io.run(),0)
        self.assertTrue(all(r["status"]==409 for r in io.responses[1:6]))
        self.assertEqual(io.responses[-1]["body"]["state"],"ready")
        self.assertEqual(io.responses[-1]["body"]["power_mode"],"continuous_required")
        self.assertFalse(io.responses[-1]["body"]["can_reboot"])
        self.assertEqual(io.nand.events,[])

    def test_native241_confirm_disconnect_finishes_once_and_verifies_last_mib(self):
        io=MemoryHTTP(self.lib,capabilities=0xff);io.platform.layout=1;io.platform.require_continuous_power=1
        data=system(layout="native241");prepared=[];finished=[]
        def prepare(ctx,task,error):
            t=C.cast(task,C.POINTER(Task)).contents
            prepared.append((t.id,t.continuous_power_confirmed));return 0
        callbacks=(PREPARE_UPDATE(prepare),FINISH_UPDATE(lambda ctx,success:finished.append(success)))
        io.platform.prepare_update,io.platform.finish_update=callbacks
        body=f"1\n{hashlib.sha256(data).hexdigest()}\n{self.lib.bw_base_hash().decode()}\ncontinuous-power\n"
        io.add("POST","/api/upload",data);io.add("POST","/api/confirm",body,disconnect_reply=True)
        def finished_phase(phase,done,total):
            if phase=="complete":io.add("GET","/api/state")
        def closed(response):
            if response["path"]=="/api/state" and response.get("body",{}).get("phase")=="complete" and response["body"]["state"]=="writing":
                io.add("GET","/api/state")
        io.on_progress=finished_phase;io.on_close=closed
        self.assertEqual(io.run(),0);self.assertEqual(prepared,[(1,1)]);self.assertEqual(finished,[1])
        erases=[off for op,off in io.nand.events if op=="erase"]
        self.assertIn(0xffe0000,erases);self.assertEqual(len(erases),2000);self.assertEqual(len(set(erases)),2000)
        self.assertEqual(io.responses[-1]["body"]["state"],"success")
        self.assertTrue(io.responses[-1]["body"]["can_reboot"])

    def test_native241_strict_bad_block_refuses_before_any_erase(self):
        io=MemoryHTTP(self.lib,capabilities=0xff);io.platform.layout=1;io.platform.require_continuous_power=1
        io.nand.bad.add(0xffe0000);data=system(layout="native241")
        io.add("POST","/api/upload",data)
        io.add("POST","/api/confirm",f"1\n{hashlib.sha256(data).hexdigest()}\n{self.lib.bw_base_hash().decode()}\ncontinuous-power\n")
        io.add("GET","/api/state")
        def closed(response):
            if response["path"]=="/api/state" and response.get("body",{}).get("state")=="writing":
                io.add("GET","/api/state")
        io.on_close=closed
        self.assertEqual(io.run(),0);self.assertEqual(io.nand.events,[])
        self.assertEqual(io.responses[-1]["body"]["state"],"failed")
        self.assertIn("nothing erased",io.responses[-1]["body"]["error"])

    def test_native241_tail_readback_and_whole_boot_failures_never_allow_reboot(self):
        for fault in ("image","tail","boot","read-error"):
            with self.subTest(fault=fault):
                io=MemoryHTTP(self.lib,capabilities=0xff);io.platform.layout=1;io.platform.require_continuous_power=1
                data=system(layout="native241")
                if fault=="image":io.nand.corrupt_readback=0xf00000
                elif fault=="tail":io.nand.corrupt_readback=0xfffffff
                elif fault=="boot":io.nand.corrupt_readback=0x22000
                else:
                    # Start returning ECC failures only after the first root
                    # erase, so the pre-erase checks themselves still pass.
                    io.on_progress=lambda phase,done,total:io.nand.fail_read.add(0xf00000) if phase=="write" else None
                io.add("POST","/api/upload",data)
                io.add("POST","/api/confirm",f"1\n{hashlib.sha256(data).hexdigest()}\n{self.lib.bw_base_hash().decode()}\ncontinuous-power\n")
                def progress(phase,done,total):
                    if fault=="read-error" and phase=="write":io.nand.fail_read.add(0xf00000)
                def closed(response):
                    if response["path"]=="/api/confirm":io.add("GET","/api/state")
                    elif response["path"]=="/api/state":
                        if response["body"]["state"]=="writing":io.add("GET","/api/state")
                        else:io.add("POST","/api/reboot")
                io.on_progress=progress;io.on_close=closed
                self.assertEqual(io.run(),0)
                state=next(r["body"] for r in reversed(io.responses) if r["path"]=="/api/state")
                self.assertEqual(state["state"],"failed");self.assertFalse(state["can_reboot"])
                self.assertEqual(io.responses[-1]["status"],409);self.assertEqual(io.reboots,0)
                if fault!="boot":
                    self.assertTrue(all(off>=0xf00000 for _,off in io.nand.events))

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
        transport.add("POST", "/api/confirm", "2\n" + digest + "\n" + self.lib.bw_base_hash().decode() + "\n")
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
        confirm = "1\n" + hashlib.sha256(data).hexdigest() + "\n" + self.lib.bw_base_hash().decode() + "\n"
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

    def test_confirmation_without_current_image_or_with_wrong_binding_is_rejected(self):
        transport = MemoryHTTP(self.lib, capabilities=0x7f)
        data = system()
        digest, installed = hashlib.sha256(data).hexdigest(), self.lib.bw_base_hash().decode()
        transport.add("POST", "/api/upload", data)
        for body in (f"1\n{digest}\n", f"1\n{digest}\n{'0' * 64}\n",
                     f"2\n{digest}\n{installed}\n", f"1\n{'0' * 64}\n{installed}\n"):
            transport.add("POST", "/api/confirm", body)
        transport.add("GET", "/api/state")
        self.assertEqual(transport.run(), 0)
        self.assertTrue(all(r["status"] == 409 for r in transport.responses[1:5]))
        self.assertEqual(transport.responses[-1]["body"]["state"], "ready")
        self.assertEqual(transport.nand.events, [])

    def test_patched_service_uses_ecc_hash_and_requires_native_identity_verification(self):
        transport = MemoryHTTP(self.lib)
        transport.nand.blocks[0x20000][0] ^= 1
        installed = hashlib.sha256(transport.nand.read(0x20000, 0x160000)).hexdigest()
        transport.add("GET", "/api/state")
        self.assertEqual(transport.run(), -1)
        self.assertEqual(transport.responses, [])
        calls = []
        def verify(ctx, identity):
            calls.append(identity.contents.installed_sha256.decode())
            return 0
        callback = VERIFY_IDENTITY(verify)
        transport.platform.nand.verify_identity = callback
        self.assertEqual(transport.run(), 0)
        state = transport.responses[-1]["body"]
        self.assertEqual(calls, [installed])
        self.assertEqual(state["installed_sha256"], installed)
        self.assertEqual(state["vendor_abi_sha256"], self.lib.bw_base_hash().decode())
        self.assertEqual(state["base"], installed)
        self.assertEqual(state["version"], "bootweb-development-v2")
        self.assertFalse(state["resident"])
        self.assertFalse(state["can_confirm"])
        self.assertTrue(state["lock_reason"])

    def test_uboot_route_stays_locked_without_native_power_and_recovery_preflight(self):
        transport = MemoryHTTP(self.lib, capabilities=0x7f)
        transport.platform.nand.prepare_uboot_write = PREPARE_UBOOT()
        data = boot(self.lib.bw_base_hash().decode())
        transport.add("POST", "/api/upload", data)
        transport.add("POST", "/api/confirm", "1\n" + hashlib.sha256(data).hexdigest() + "\n" + self.lib.bw_base_hash().decode() + "\n")
        self.assertEqual(transport.run(), 0)
        self.assertFalse(transport.responses[0]["body"]["can_confirm"])
        self.assertIn("preflight", transport.responses[0]["body"]["lock_reason"])
        self.assertEqual(transport.responses[1]["status"], 409)
        self.assertEqual(transport.nand.events, [])

    def test_native_runtime_lock_overrides_capabilities_and_reports_reason(self):
        io=MemoryHTTP(self.lib,capabilities=0x7f);data=system()
        reason=C.create_string_buffer(b"native timing evidence is missing")
        callback=UPDATE_LOCK(lambda ctx,result:C.addressof(reason))
        io.platform.update_lock=callback
        io.add("POST","/api/upload",data)
        io.add("POST","/api/confirm","1\n"+hashlib.sha256(data).hexdigest()+"\n"+self.lib.bw_base_hash().decode()+"\n")
        self.assertEqual(io.run(),0)
        self.assertFalse(io.responses[0]["body"]["can_confirm"])
        self.assertEqual(io.responses[0]["body"]["lock_reason"],reason.value.decode())
        self.assertEqual(io.responses[1]["status"],409)
        self.assertEqual(io.nand.events,[])

    def test_native_prepare_failure_after_confirmation_never_erases_and_finishes_once(self):
        io=MemoryHTTP(self.lib,capabilities=0x7f);data=system();finished=[]
        def reject(ctx,task,error):
            C.memmove(error,b"backup changed\0",15);return -1
        callbacks=(PREPARE_UPDATE(reject),FINISH_UPDATE(lambda ctx,success:finished.append(success)))
        io.platform.prepare_update,io.platform.finish_update=callbacks
        io.add("POST","/api/upload",data)
        io.add("POST","/api/confirm","1\n"+hashlib.sha256(data).hexdigest()+"\n"+self.lib.bw_base_hash().decode()+"\n")
        io.add("GET","/api/state")
        self.assertEqual(io.run(),0)
        self.assertEqual(finished,[0]);self.assertEqual(io.nand.events,[])
        self.assertEqual(io.responses[-1]["body"]["state"],"failed")
        self.assertEqual(io.responses[-1]["body"]["error"],"backup changed")

    def test_native_prepare_finish_and_duplicate_confirm_do_not_repeat_write(self):
        io=MemoryHTTP(self.lib,capabilities=0x7f);data=system();prepared=[];finished=[]
        callbacks=(PREPARE_UPDATE(lambda ctx,task,error:(prepared.append(True) or 0)),
                   FINISH_UPDATE(lambda ctx,success:finished.append(success)))
        io.platform.prepare_update,io.platform.finish_update=callbacks
        confirm="1\n"+hashlib.sha256(data).hexdigest()+"\n"+self.lib.bw_base_hash().decode()+"\n"
        io.add("POST","/api/upload",data);io.add("POST","/api/confirm",confirm)
        def closed(response):
            if response["path"]=="/api/confirm" and response.get("status")==202 and not finished:
                io.add("GET","/api/state")
        io.on_close=closed
        io.on_progress=lambda phase,done,total:io.add("POST","/api/confirm",confirm) if phase=="complete" else None
        self.assertEqual(io.run(),0)
        self.assertEqual(prepared,[True]);self.assertEqual(finished,[1])
        erases=[off for kind,off in io.nand.events if kind=="erase"]
        self.assertEqual(len(erases),len(set(erases)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
