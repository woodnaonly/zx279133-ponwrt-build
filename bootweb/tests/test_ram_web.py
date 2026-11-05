"""Host acceptance orchestration only: all sockets/UART are replaced by fakes."""
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import ram_web


class RamWebTests(unittest.TestCase):
    def test_http_waits_cannot_extend_a_live_uart_generation_deadline(self):
        with patch.object(ram_web,"HTTP_DEADLINE",100),patch.object(ram_web.time,"monotonic",return_value=95):
            self.assertEqual(ram_web.http_timeout(30),5)
        with patch.object(ram_web,"HTTP_DEADLINE",100),patch.object(ram_web.time,"monotonic",return_value=20):
            self.assertEqual(ram_web.http_timeout(100),80)
        with patch.object(ram_web,"HTTP_DEADLINE",100),patch.object(ram_web.time,"monotonic",return_value=100):
            with self.assertRaises(ram_web.TrialError):ram_web.http_timeout(1)

    def test_full_http_sequence_includes_ten_interrupts_and_privileged_denials(self):
        token, payload = "a" * 64, b"sample firmware" * 1024
        complete = False
        uploads, checks, confirmations = [], [], []
        class Sock:
            def shutdown(self, mode): pass
            def recv(self, size): return b""
        class Connection:
            def __init__(self, *args, **kwargs): self.sock = Sock()
            def putrequest(self, *args, **kwargs): pass
            def putheader(self, *args): pass
            def endheaders(self): pass
            def send(self, data): uploads.append(data)
            def close(self): pass
        def state(unused):
            return {"state": "ready" if complete else "failed", "error": "truncated upload", "id": "11",
                    "installed_sha256": "b"*64,
                    "result": {"sha256": hashlib.sha256(payload).hexdigest()}}
        def request(method, path, body=None, **kwargs):
            nonlocal complete
            if path == "/": return 200, (ROOT / "web/index.html").read_bytes()
            if path == "/api/cancel": return 200, b"{}"
            if path == "/api/upload" and body == payload:
                complete = True
                return 200, json.dumps({"state":"ready", "can_confirm":False}).encode()
            if path == "/api/confirm": confirmations.append((body, kwargs["token"]))
            return 409, b"{}"
        with patch.object(ram_web, "request", request), patch.object(ram_web, "native_state", state), \
                patch.object(ram_web.http.client, "HTTPConnection", Connection):
            ram_web.http_checks(token, payload, lambda event, **fields: checks.append(fields))
        self.assertEqual(len(uploads), 10)
        self.assertEqual([len(data) for data in uploads], [1024+i*137 for i in range(10)])
        self.assertEqual([c["iteration"] for c in checks if c["case"] == "interrupted-upload"], list(range(1,11)))
        self.assertTrue(complete)
        self.assertEqual(confirmations, [(("11\n"+hashlib.sha256(payload).hexdigest()+"\n"+"b"*64+"\n").encode(), token)])
        self.assertEqual([c["case"] for c in checks[-3:]], ["complete-real-firmware", "/api/confirm", "/api/reboot"])

    def test_http_identity_never_accepts_linux_or_a_different_uart_token(self):
        good = {"mode":"native-ram-readonly", "token":"a"*64, "can_confirm":False, "can_reboot":False}
        with patch.object(ram_web, "request", return_value=(200,json.dumps(good).encode())):
            self.assertEqual(ram_web.native_state("a"*64), good)
        for field, value in (("mode","linux-readonly"),("token","b"*64),("can_confirm",True),("can_reboot",True)):
            with patch.object(ram_web, "request", return_value=(200,json.dumps({**good,field:value}).encode())):
                with self.assertRaises(ram_web.TrialError): ram_web.native_state("a"*64)

    def test_native_hash_return_and_watchdog_evidence_are_all_required(self):
        lines = [f"BW_RAM_HASH {phase} {region} " + ("a" if region=="boot" else "b")*64
                 for phase in ("before","repeat","after") for region in ("boot","tags")]
        lines += [f"BW_RAM_WATCHDOG phase={phase} controls=12340000,0,0,0 disabled=1" for phase in ("before","after")]
        lines += ["BW_RAM_READONLY_EXIT rc=0 reads=155 corrected_reads=0 rx=1 tx=1 rx_restored=1 driver_restored=1 phy_modes_restored=1"]
        text = "\n".join(lines)
        self.assertTrue(ram_web.native_evidence(text)["native_return_pass"])
        for bad in ("\n".join(lines[1:]), text.replace("driver_restored=1","driver_restored=0"),
                    text.replace("phy_modes_restored=1","phy_modes_restored=0"),
                    text.replace("controls=12340000,0,0,0","controls=12340001,0,0,0",1),
                    text.replace("BW_RAM_HASH after boot " + "a"*64, "BW_RAM_HASH after boot " + "c"*64)):
            self.assertFalse(ram_web.native_evidence(bad)["native_return_pass"])
        with self.assertRaises(ram_web.TrialError): ram_web.native_evidence(text+"\n"+lines[0])
