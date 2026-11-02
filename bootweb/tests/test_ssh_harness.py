"""Offline tests of the opt-in SSH runner; never open sockets or contact SSH."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import test_ssh_readonly as runner


class SSHHarnessTests(unittest.TestCase):
    def test_device_test_requires_explicit_opt_in(self):
        with mock.patch.object(sys, "argv", ["test_ssh_readonly.py"]), \
                mock.patch.object(subprocess, "run") as command, \
                mock.patch.object(subprocess, "Popen") as tunnel, \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as stopped:
                runner.main()
        self.assertEqual(stopped.exception.code, 2)
        command.assert_not_called()
        tunnel.assert_not_called()

    def test_disconnect_waits_for_complete_body_receipt(self):
        first = {"event": "body_received", "connection": 6, "body_bytes": 1024}
        second = {"event": "body_received", "connection": 9, "body_bytes": 512}
        log = (json.dumps({"event": "connection_closed", "received_bytes": 0}) + "\n" +
               json.dumps(first) + "\n" + json.dumps(second))
        self.assertEqual(runner.body_receipt(log, 1), first)
        self.assertIsNone(runner.body_receipt(log, 2))  # unfinished log line is not evidence
        self.assertEqual(runner.body_receipt(log + "\n", 2), second)
        self.assertIsNone(runner.body_receipt("", 1))
        with self.assertRaises(ValueError):
            runner.body_receipt(log, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
