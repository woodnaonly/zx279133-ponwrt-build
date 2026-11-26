#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""What sk-wan-switch does to the board.

THE CASE

The second WAN is a panel jack that leaves the LAN bridge, and the change
has to be a transaction: it either lands or it does not, and the old
configuration comes back when it does not.

Every test here runs the shipped script in the sandbox board of
tests/sk_wan_switch_fixture.py and reads the committed configuration file
back, because that file is what the board comes back with after a power
cut.  A test that read "uci get" would read the staged delta and would not
see a change that never reached the file.

    python3 tests/test_sk_wan_switch.py
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import sk_wan_switch_fixture as fx


class SwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sk-wan-switch-")
        self.addCleanup(self.tmp.cleanup)
        self.board = fx.make_board(self.tmp.name)
        for jack in fx.JACKS:
            self.board.netdev(jack, lower=fx.CONDUIT)
        self.board.netdev(fx.CONDUIT, dsa=True)
        self.board.netdev("eth0")
        self.board.bridge("br-lan", ("lan1", "lan2", "lan3", "lan4"))

    # ---- the helpers ----

    def apply(self, *args, **kwargs):
        return self.board.run(fx.SWITCH, ["apply-wan2"] + list(args), **kwargs)

    def status(self, *args):
        proc = self.board.run(fx.SWITCH, ["status"] + list(args))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def token(self):
        path = self.board.root / "run" / "sk-wan-switch.token"
        self.assertTrue(path.is_file(), "the change left no token behind")
        return path.read_text(encoding="utf-8").strip()

    # ---- the change ----

    def test_the_jack_leaves_the_bridge_and_becomes_the_second_wan(self):
        proc = self.apply("--device", "lan2", "--proto", "dhcp")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        net = self.board.config_text("network")
        self.assertIn("list ports 'lan1'", net)
        self.assertIn("list ports 'lan3'", net)
        self.assertIn("list ports 'lan4'", net)
        self.assertNotIn("list ports 'lan2'", net)
        self.assertIn("option device 'lan2'", net)
        self.assertIn("option proto 'dhcp'", net)

    def test_the_firewall_gets_a_zone_for_the_second_wan(self):
        self.apply("--device", "lan2", "--proto", "dhcp")
        fw = self.board.config_text("firewall")
        self.assertIn("config zone 'wan2'", fw)
        self.assertIn("option network 'wan2'", fw)
        self.assertIn("option masq '1'", fw)
        self.assertIn("config forwarding 'lan_wan2'", fw)
        self.assertIn("option dest 'wan2'", fw)

    def test_the_bridge_is_a_list_and_not_one_string(self):
        """A bridge written as one string holds a port called "lan1 lan3"."""
        self.apply("--device", "lan2", "--proto", "dhcp")
        net = self.board.config_text("network")
        self.assertNotIn("option ports", net)
        for port in ("lan1", "lan3", "lan4"):
            self.assertIn("\n\tlist ports '%s'" % port, net)

    def test_the_status_says_what_the_board_is(self):
        self.apply("--device", "lan2", "--proto", "dhcp")
        state = self.status()
        self.assertEqual(state["wan1"]["device"], "eth0")
        self.assertEqual(state["wan2"]["device"], "lan2")
        self.assertEqual(state["wan2"]["proto"], "dhcp")
        self.assertEqual(sorted(state["bridge"]), ["lan1", "lan3", "lan4"])
        self.assertEqual(state["primary"], "wan")

    def test_asking_for_what_is_already_there_changes_nothing(self):
        """A page that applies twice must not cut the link twice."""
        self.apply("--device", "lan2", "--proto", "dhcp")
        proc = self.apply("--device", "lan2", "--proto", "dhcp")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse(json.loads(proc.stdout)["changed"])

    def test_the_old_jack_goes_back_into_the_bridge(self):
        self.apply("--device", "lan2", "--proto", "dhcp")
        self.apply("--device", "lan3", "--proto", "dhcp")
        net = self.board.config_text("network")
        self.assertIn("list ports 'lan2'", net)
        self.assertNotIn("list ports 'lan3'", net)

    def test_the_metric_follows_the_primary(self):
        self.apply("--device", "lan2", "--proto", "dhcp", "--primary", "wan2")
        self.assertEqual(self.board.uci_get("network.wan.metric"), "20")
        self.assertEqual(self.board.uci_get("network.wan2.metric"), "10")
        self.assertEqual(self.board.uci_get("sk_wan.wan.primary"), "wan2")

    def test_a_port_that_is_not_a_jack_is_refused(self):
        proc = self.apply("--device", "lan-cpu0", "--proto", "dhcp")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("must be one of", proc.stdout)
        net = self.board.config_text("network")
        self.assertIn("list ports 'lan1'", net)
        self.assertNotIn("option device 'lan-cpu0'", net)

    # ---- the transaction ----

    def test_the_change_is_taken_back_when_nobody_confirms(self):
        self.apply("--device", "lan2", "--proto", "dhcp")
        token = self.token()
        proc = self.board.run(fx.SWITCH, ["watchdog", "1", token])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        net = self.board.config_text("network")
        self.assertIn("list ports 'lan2'", net)
        self.assertNotIn("option device 'lan2'", net)

    def test_the_confirmation_keeps_the_change(self):
        self.apply("--device", "lan2", "--proto", "dhcp")
        token = self.token()
        proc = self.board.run(fx.SWITCH, ["confirm"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.board.run(fx.SWITCH, ["watchdog", "1", token])
        net = self.board.config_text("network")
        self.assertNotIn("list ports 'lan2'", net)
        self.assertIn("option device 'lan2'", net)

    def test_the_rollback_takes_the_change_back_at_once(self):
        self.apply("--device", "lan2", "--proto", "dhcp")
        proc = self.board.run(fx.SWITCH, ["rollback"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        net = self.board.config_text("network")
        self.assertIn("list ports 'lan2'", net)
        self.assertNotIn("option device 'lan2'", net)

    def test_a_change_that_does_not_reach_the_file_is_taken_back(self):
        """The check that reads the committed file back has to have teeth."""
        proc = self.apply("--device", "lan2", "--proto", "dhcp",
                          extra_env={"UCI_DROP": "network.wan2.device"})
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("did not land", proc.stdout)

    def test_the_reset_gives_the_jack_back(self):
        self.apply("--device", "lan2", "--proto", "dhcp")
        proc = self.board.run(fx.SWITCH, ["reset"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        net = self.board.config_text("network")
        self.assertIn("list ports 'lan2'", net)
        self.assertIn("option device 'eth0'", net)
        self.assertIn("option disabled '1'", net)

    # ---- the management port ----

    def test_the_management_port_is_found(self):
        """The jack the client is in is the one that must not be taken."""
        self.board.neighbour("192.168.9.25", "84:47:09:5b:bd:2c")
        self.board.showmacs("1 84:47:09:5b:bd:2c\n")
        state = self.status("192.168.9.25")
        self.assertEqual(state["mgmt_port"], "lan1")

    def test_an_unknown_client_takes_no_port(self):
        state = self.status("192.168.9.99")
        self.assertEqual(state["mgmt_port"], "")


if __name__ == "__main__":
    unittest.main()
