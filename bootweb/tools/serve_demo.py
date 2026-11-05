#!/usr/bin/env python3
"""Loopback-only page/HTTP/C-core demonstrator; NEVER touches a real NAND device.

Default: validate only, confirm locked. --simulate-writes requires an explicit
known full boot image and writes only an in-memory sparse NAND model.
"""
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import secrets
import socket
import socketserver
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from host.core import Core, LAYOUTS, MAX_UPLOAD, ROOT, STATES, Task, ValidationError
from host.simulator import SparseNand


class Demo(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, port=8088, *, boot=None, layout="current240", core=None):
        self.core, self.task = core or Core(), Task()
        self.layout = layout
        self.token = secrets.token_hex(32)
        self.lock = threading.RLock()
        self.slots = threading.BoundedSemaphore(4)
        self.buffer = C.create_string_buffer(1536)
        self.upload_active, self.worker, self.sequence = False, None, 0
        self.phase, self.done, self.total = "idle", 0, 0
        self.nand = None
        if boot is not None:
            if len(boot) != 0x200000 or hashlib.sha256(boot[0x20000:0x180000]).hexdigest() != self.core.base:
                raise ValidationError("simulation seed is not the known community boot base")
            self.nand = SparseNand(boot, progress=self.progress)
        self.initialize()
        super().__init__(("127.0.0.1", port), Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def initialize(self):
        rc = self.core.lib.bw_task_init(C.byref(self.task), self.buffer, len(self.buffer),
                                        self.core.base.encode(), LAYOUTS[self.layout], self.token.encode())
        if rc:
            raise RuntimeError("task initialization failed")

    def progress(self, phase, done, total):
        with self.lock:
            self.phase, self.done, self.total = phase, done, total

    def state(self):
        with self.lock:
            state = STATES[self.task.state]
            return {"board": "skyworth_sk-d840n", "base": self.core.base, "layout": self.layout,
                    "version": "bootweb-development-v2", "resident": False,
                    "vendor_abi_sha256": self.core.base, "installed_sha256": self.core.base,
                    "lock_reason": "" if self.nand else "No hardware write adapter is attached",
                    "mode": "电脑内存模拟" if self.nand else "电脑校验预览（写入锁定）",
                    "notice": "仅本机演示：不会连接设备，不会读写真实 Flash；未完成实机验收。",
                    "token": self.token, "state": state, "id": str(self.task.id),
                    "error": self.task.error.decode(), "phase": self.phase,
                    "done": self.done, "total": self.total,
                    "can_confirm": self.nand is not None, "can_reboot": False,
                    "result": self.task.result.report() if self.task.result.count else None}

    def execute(self):
        scratch = C.create_string_buffer(0x20000)
        # Deliberately do not hold the HTTP lock for a long flash operation:
        # read-only status remains available and browser disconnects do not stop it.
        self.core.lib.bw_task_run(C.byref(self.task), C.byref(self.nand.port), scratch, len(scratch))


class Handler(socketserver.StreamRequestHandler):
    remaining_body = 0

    def finish(self):
        # An early 409 with unread POST bytes can become a TCP reset on Windows,
        # hiding the rejection response. Half-close first, then discard only a
        # bounded tail; never parse or validate a rejected body.
        try:
            if self.remaining_body:
                self.wfile.flush()
                self.request.shutdown(socket.SHUT_WR)
                deadline = time.monotonic() + .2
                discard = min(self.remaining_body, 256 * 1024)
                while discard and time.monotonic() < deadline:
                    self.request.settimeout(max(.001, deadline-time.monotonic()))
                    chunk = self.rfile.read(min(discard, 65536))
                    if not chunk:
                        break
                    discard -= len(chunk)
        except OSError:
            pass
        finally:
            super().finish()

    def reply(self, code, data, content_type="application/json; charset=utf-8"):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode()
        headers = (f"HTTP/1.1 {code} Response\r\nConnection: close\r\nContent-Length: {len(data)}\r\n"
                   f"Content-Type: {content_type}\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n"
                   "Content-Security-Policy: default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'\r\n\r\n").encode()
        try:
            self.wfile.write(headers + data)
        except (OSError, ConnectionError):
            pass

    def body(self, n, *, upload=False):
        deadline = time.monotonic() + (300 if upload else 5)
        done = 0
        while done < n:
            self.request.settimeout(max(0.01, min(30, deadline - time.monotonic())))
            if time.monotonic() > deadline:
                raise ValidationError("request body total timeout")
            chunk = self.rfile.read(min(65536, n - done))
            if not chunk:
                raise ValidationError("truncated request body")
            self.remaining_body = n - done - len(chunk)
            yield chunk
            done += len(chunk)

    def handle(self):
        server = self.server
        header, deadline = bytearray(), time.monotonic() + 5
        receiving = False
        try:
            while len(header) < 4096 and not header.endswith(b"\r\n\r\n"):
                self.request.settimeout(max(0.01, deadline - time.monotonic()))
                if time.monotonic() > deadline:
                    raise ValidationError("header timeout")
                char = self.rfile.read(1)
                if not char:
                    return
                header += char
            request = server.core.parse_http(bytes(header))
            self.remaining_body = request.length
            host = f"127.0.0.1:{server.server_address[1]}"
            if request.host.decode() != host or request.origin and request.origin.decode() != "http://" + host:
                raise ValidationError("Host/Origin rejected")
            path = request.path.decode()
            if request.method == b"GET":
                if path == "/":
                    return self.reply(200, (ROOT / "web/index.html").read_bytes(), "text/html; charset=utf-8")
                if path == "/api/state":
                    return self.reply(200, server.state())
                raise ValidationError("GET route not allowed")
            if not secrets.compare_digest(request.token.decode(), server.token):
                raise ValidationError("invalid token")
            if path == "/api/upload":
                with server.lock:
                    if server.task.state != 0 or server.upload_active:
                        raise ValidationError("another task exists; explicit cancel required")
                    if not 1536 <= request.length <= MAX_UPLOAD:
                        raise ValidationError("invalid upload length")
                    server.buffer = C.create_string_buffer(request.length)
                    server.initialize()
                    server.sequence += 1
                    if server.core.lib.bw_task_begin(C.byref(server.task), request.length, server.sequence):
                        raise ValidationError(server.task.error.decode())
                    server.upload_active, receiving = True, True
                for chunk in self.body(request.length, upload=True):
                    with server.lock:
                        if server.core.lib.bw_task_feed(C.byref(server.task), chunk, len(chunk)):
                            raise ValidationError(server.task.error.decode())
                with server.lock:
                    server.core.lib.bw_task_finish(C.byref(server.task))
                    code = 200 if server.task.state == 3 else 409
                return self.reply(code, server.state())
            if request.length > 192:
                raise ValidationError("control body exceeds fixed limit")
            body = b"".join(self.body(request.length))
            with server.lock:
                if server.upload_active:
                    raise ValidationError("upload still active")
                if path == "/api/confirm":
                    if server.nand is None:
                        raise ValidationError("real writes are locked; start explicit in-memory simulation if needed")
                    lines = body.split(b"\n")
                    if len(lines) != 4 or lines[3] or not lines[0].isdigit() or len(lines[0]) > 20 or len(lines[1]) != 64 or len(lines[2]) != 64:
                        raise ValidationError("invalid confirmation body")
                    task_id = int(lines[0])
                    if task_id > 2**64 - 1:
                        raise ValidationError("task id overflow")
                    prior = server.task.state
                    if server.core.lib.bw_task_confirm_identity(C.byref(server.task), task_id, lines[1], lines[2], request.token):
                        raise ValidationError(server.task.error.decode())
                    if prior == 3:
                        server.worker = threading.Thread(target=server.execute, daemon=True)
                        server.worker.start()
                    return self.reply(202, server.state())
                if path == "/api/cancel" and not body:
                    if server.worker and server.worker.is_alive():
                        raise ValidationError("cannot cancel running writer")
                    if server.core.lib.bw_task_cancel(C.byref(server.task), request.token):
                        raise ValidationError(server.task.error.decode())
                    return self.reply(200, server.state())
                raise ValidationError("POST route/body not allowed; simulator does not reboot devices")
        except (OSError, ValueError, RuntimeError, OverflowError) as error:
            self.reply(409, {"error": str(error)})
        finally:
            if receiving:
                with server.lock:
                    if server.task.state == 1:
                        server.core.lib.bw_task_finish(C.byref(server.task))
                    server.upload_active = False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--layout", choices=LAYOUTS, default="current240")
    parser.add_argument("--simulate-writes", action="store_true")
    parser.add_argument("--base-boot", type=Path)
    args = parser.parse_args()
    if args.simulate_writes and not args.base_boot:
        parser.error("--simulate-writes requires --base-boot; all writes remain in RAM on this PC")
    if args.base_boot and not args.simulate_writes:
        parser.error("--base-boot is only used with --simulate-writes")
    if not 0 <= args.port <= 65535:
        parser.error("invalid listener port")
    boot = args.base_boot.read_bytes() if args.base_boot else None
    with Demo(args.port, boot=boot, layout=args.layout) as server:
        print(f"Local demo http://127.0.0.1:{server.server_address[1]} — no real-device backend", flush=True)
        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
