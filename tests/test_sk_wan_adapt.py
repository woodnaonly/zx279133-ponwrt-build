#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""What sk-wan-adapt does to the board.

THE CASE

The adaptive second WAN asks the panel jacks which one has something on it
that answers DHCP, and the one that answers becomes the second WAN.  Two
jacks are never taken: the one the management client is in, and the ones
the user excluded.

The other half of the script is the failover: the two WANs prefer each other
in turn, so the traffic moves to the backup when the primary stops working
and back when it comes back.

The daemon runs in a loop, so the tests give it a number of rounds and it
stops after them.  The number is a hook in the script, and it is there for
this: a daemon that cannot be stopped cannot be tested.

    python3 tests/test_sk_wan_adapt.py
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import sk_wan_switch_fixture as fx


class AdaptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sk-wan-adapt-")
        self.addCleanup(self.tmp.cleanup)
        self.board = fx.make_board(self.tmp.name)
        for jack in fx.JACKS:
            self.board.netdev(jack, lower=fx.CONDUIT)
        self.board.netdev(fx.CONDUIT, dsa=True)
        self.board.netdev("eth0")
        self.board.bridge("br-lan", ("lan1", "lan2", "lan3", "lan4"))
        # The client is in lan1.
        self.board.showmacs("1 84:47:09:5b:bd:2c\n")

    def policy(self, auto="1", primary="wan", exclude=""):
        self.board.write_config("sk_wan",
                                "config settings 'wan'\n"
                                "\toption auto '%s'\n" % auto +
                                "\toption primary '%s'\n" % primary +
                                "\toption exclude '%s'\n" % exclude)

    def run_adapt(self, **extra):
        env = {"SKW_SWITCH": fx.posix(fx.SWITCH),
               "SKW_ROUNDS": 2,
               "SKW_FAILOVER_INTERVAL": 0,
               "SKW_FAILOVER_ROUNDS": 99,
               "SKW_ADOPT_GRACE": 10}
        env.update(extra)
        return self.board.run(fx.ADAPT, [], extra_env=env)

    def wan2_device(self):
        return self.board.uci_get("network.wan2.device")

    # ---- the search ----

    def test_the_port_that_answers_becomes_the_second_wan(self):
        self.policy(auto="1")
        self.board.interface_up("wan2")
        proc = self.run_adapt(FAKE_DHCP_ANSWER="lan3")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.wan2_device(), "lan3", proc.stdout + proc.stderr)
        net = self.board.config_text("network")
        self.assertNotIn("list ports 'lan3'", net)
        self.assertIn("list ports 'lan2'", net)

    def test_the_port_the_client_is_in_is_never_taken(self):
        """The bench learned this the hard way on 2026-10-29."""
        self.policy(auto="1")
        self.board.interface_up("wan2")
        proc = self.run_adapt(FAKE_DHCP_ANSWER="lan1")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.wan2_device(), "")

    def test_an_excluded_port_is_never_taken(self):
        self.policy(auto="1", exclude="lan3")
        self.board.interface_up("wan2")
        proc = self.run_adapt(FAKE_DHCP_ANSWER="lan3")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.wan2_device(), "")

    def test_nothing_is_taken_when_the_adaptive_mode_is_off(self):
        self.policy(auto="0")
        self.board.interface_up("wan2")
        proc = self.run_adapt(FAKE_DHCP_ANSWER="lan3")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.wan2_device(), "")

    def test_a_working_second_wan_is_left_alone(self):
        """A port that was chosen by hand and works is not looked for again."""
        self.policy(auto="1")
        self.board.write_config("network",
                                fx.default_network(wan2_device="lan2"))
        self.board.interface_up("wan2")
        proc = self.run_adapt(FAKE_DHCP_ANSWER="lan3")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.wan2_device(), "lan2")

    # ---- the failover ----

    def test_the_traffic_moves_to_the_backup_when_the_primary_stops(self):
        self.policy(auto="0", primary="wan")
        self.board.interface_up("wan", up=False)
        self.board.interface_up("wan2")
        proc = self.run_adapt(SKW_FAILOVER_ROUNDS=1)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.board.uci_get("network.wan.metric"), "20")
        self.assertEqual(self.board.uci_get("network.wan2.metric"), "10")

    def test_the_traffic_goes_back_when_the_primary_comes_back(self):
        self.policy(auto="0", primary="wan")
        self.board.interface_up("wan")
        self.board.interface_up("wan2")
        state = self.board.root / "run" / "sk-wan-adapt.state"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text("good=0\nbad=0\nswapped=1\n", encoding="utf-8",
                         newline="\n")
        proc = self.run_adapt(SKW_FAILOVER_ROUNDS=1)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.board.uci_get("network.wan.metric"), "10")
        self.assertEqual(self.board.uci_get("network.wan2.metric"), "20")


if __name__ == "__main__":
    sys.exit(0 if fx.run_tests(sys.modules[__name__]) else 1)
