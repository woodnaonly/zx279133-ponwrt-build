"""Real lwIP and bridge, entirely in-memory Ethernet; NO OS network interface."""
import ctypes as C
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.platform import INIT, NOW, POLL, Platform

FRAME_SEND = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p, C.c_size_t)
FRAME_RECV = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p, C.c_size_t)
FRAME_POLL = C.CFUNCTYPE(C.c_int, C.c_void_p, FRAME_RECV, C.c_void_p)
RANDOM = C.CFUNCTYPE(C.c_uint32, C.c_void_p)


class Ethernet(C.Structure):
    _fields_ = [("context", C.c_void_p), ("mac", C.c_ubyte * 6), ("start", INIT),
                ("send", FRAME_SEND), ("poll", FRAME_POLL), ("now", NOW),
                ("random", RANDOM), ("watchdog", POLL)]


def checksum(data):
    data += bytes(len(data) % 2)
    value = sum(struct.unpack("!" + "H" * (len(data) // 2), data))
    while value >> 16:
        value = (value & 0xffff) + (value >> 16)
    return value ^ 0xffff


class NetTests(unittest.TestCase):
    def test_virtual_arp_tcp_stream_duplicates_checksum_and_disconnect(self):
        suffix = ".dll" if sys.platform == "win32" else ".so"
        path = ROOT / ".build" / ("bootweb-net-test" + suffix)
        if not path.is_file():
            self.skipTest("build with --test-net to test the actual native TCP bridge")
        lib = C.CDLL(str(path))
        lib.bw_lwip_bind.argtypes = [C.POINTER(Platform), C.POINTER(Ethernet)]
        lib.bw_lwip_bind.restype = C.c_int
        incoming, outgoing, callback_errors = [], [], []
        clock = [1000]
        pc, dev = bytes.fromhex("020000000002"), bytes.fromhex("020000000001")
        pcip, devip = bytes([192, 168, 9, 2]), bytes([192, 168, 9, 1])

        def transmit(ctx, p, n):
            outgoing.append(C.string_at(p, n))
            return 0

        def poll(ctx, receive, arg):
            clock[0] += 1
            try:
                while incoming:
                    data = incoming.pop(0)
                    receive(arg, data, len(data))
            except Exception as error:
                callback_errors.append(str(error))
                return -1
            return 0

        callbacks = [INIT(lambda ctx: 0), FRAME_SEND(transmit), FRAME_POLL(poll),
                     NOW(lambda ctx: clock[0]), RANDOM(lambda ctx: 0x84001337), POLL(lambda ctx: None)]
        ethernet = Ethernet(None, (C.c_ubyte * 6)(*dev), *callbacks)
        platform = Platform()
        self.assertEqual(lib.bw_lwip_bind(C.byref(platform), C.byref(ethernet)), 0)
        self.assertEqual(platform.nand.capabilities, 0)
        self.assertEqual(platform.listen(None, 0xc0a80901, 80), 0)
        # ARP request and response. Frames never leave Python memory.
        arp = struct.pack("!HHBBH", 1, 0x800, 6, 4, 1) + pc + pcip + bytes(6) + devip
        incoming.append(b"\xff" * 6 + pc + b"\x08\x06" + arp)
        platform.poll(None)
        self.assertTrue(any(frame[12:14] == b"\x08\x06" and frame[20:22] == b"\x00\x02" for frame in outgoing))
        outgoing.clear()

        def packet(seq, ack, flags, data=b"", *, port=40000):
            tcp = struct.pack("!HHIIBBHHH", port, 80, seq, ack, 5 << 4, flags, 16384, 0, 0) + data
            pseudo = pcip + devip + struct.pack("!BBH", 0, 6, len(tcp))
            tcp = tcp[:16] + struct.pack("!H", checksum(pseudo + tcp)) + tcp[18:]
            ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(tcp), 0, 0, 64, 6, 0, pcip, devip)
            ip = ip[:10] + struct.pack("!H", checksum(ip)) + ip[12:]
            return dev + pc + b"\x08\x00" + ip + tcp

        def decoded(frame):
            if frame[12:14] != b"\x08\x00":
                return None
            off = 14 + (frame[14] & 15) * 4
            seq, ack = struct.unpack_from("!II", frame, off + 4)
            size = (frame[off + 12] >> 4) * 4
            total = struct.unpack_from("!H", frame, 16)[0]
            return seq, ack, frame[off + 13], frame[off + size:14 + total]

        incoming.append(packet(1000, 0, 2))
        platform.poll(None)
        syn = next(decoded(frame) for frame in outgoing if decoded(frame) and decoded(frame)[2] & 0x12 == 0x12)
        outgoing.clear()
        server_seq = syn[0] + 1
        incoming.append(packet(1001, server_seq, 0x10))
        platform.poll(None)
        fd = platform.accept(None)
        self.assertGreater(fd, 0)
        request = b"GET /api/state HTTP/1.1\r\nHost: 192.168.9.1\r\n\r\n"
        incoming.append(packet(1001, server_seq, 0x18, request))
        platform.poll(None)
        buf = C.create_string_buffer(32768)
        n = platform.recv(None, fd, buf, len(buf), 20)
        self.assertEqual(buf.raw[:n], request)
        # Same TCP sequence retransmitted: no second request delivered.
        incoming.append(packet(1001, server_seq, 0x18, request))
        platform.poll(None)
        self.assertLess(platform.recv(None, fd, buf, len(buf), 5), 0)
        # Bad TCP checksum does not reach the upload stream.
        broken = bytearray(packet(1001 + len(request), server_seq, 0x18, b"BAD"))
        broken[-1] ^= 1
        incoming.append(bytes(broken))
        platform.poll(None)
        self.assertLess(platform.recv(None, fd, buf, len(buf), 5), 0)
        # Ring wrap across >16 KiB, drained with real TCP receive-window updates.
        client_seq = 1001 + len(request)
        for index in range(28):
            body = bytes([index]) * 1000
            incoming.append(packet(client_seq, server_seq, 0x18, body))
            platform.poll(None)
            n = platform.recv(None, fd, buf, len(buf), 20)
            self.assertEqual(buf.raw[:n], body)
            client_seq += len(body)
        response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}"
        outgoing.clear()
        self.assertEqual(platform.send(None, fd, response, len(response)), len(response))
        payloads = [decoded(frame)[3] for frame in outgoing if decoded(frame)]
        self.assertIn(response, payloads)
        incoming.append(packet(client_seq, server_seq + len(response), 0x10))
        platform.poll(None)
        incoming.append(packet(client_seq, server_seq + len(response), 0x14))
        platform.poll(None)
        self.assertEqual(platform.recv(None, fd, buf, len(buf), 20), 0)
        platform.close(None, fd)
        # Dropped connections before accept must not exhaust all stream slots.
        for index in range(4):
            port = 41000 + index
            outgoing.clear()
            incoming.append(packet(2000, 0, 2, port=port))
            platform.poll(None)
            syn = next(decoded(frame) for frame in outgoing if decoded(frame) and decoded(frame)[2] & 0x12 == 0x12)
            incoming.append(packet(2001, syn[0] + 1, 0x10, port=port))
            incoming.append(packet(2001, syn[0] + 1, 0x14, port=port))
            platform.poll(None)
        self.assertEqual(callback_errors, [])
        self.assertEqual(platform.nand.capabilities, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
