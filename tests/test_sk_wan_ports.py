#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""What zx279133-wan-ports does to the NPPT.

THE CASE

A WAN-type port is a port the hardware treats as the factory system treats
it: the PPPoE header is stripped on the way in and put back on the way out.
A DHCP or a static uplink wants ordinary Ethernet frames, so its port has to
stay LAN-type.

The tree's script decides by the name of the firewall zone, and it never
clears a port that went back into the bridge.  Both are wrong for this
board, and the tests here are about the two of them:

  * a PPPoE WAN makes its port WAN-type, a DHCP WAN does not;
  * a port that is WAN-type and must not be any more is put back;
  * the driver is told to keep its hands off the port types, because it
    would otherwise make every jack that leaves the bridge a WAN-type port.

The attribute the driver publishes is a file in the sandbox, and the test
reads what the script wrote into it.

    python3 tests/test_sk_wan_ports.py
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import sk_wan_switch_fixture as fx


class PortsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sk-wan-ports-")
        self.addCleanup(self.tmp.cleanup)
        self.board = fx.make_board(self.tmp.name)
        for jack in fx.JACKS:
            self.board.netdev(jack, lower=fx.CONDUIT)
        self.board.netdev(fx.CONDUIT, dsa=True)
        self.board.netdev("eth0")
        self.attr = self.board.wan_ports_attr("")
        self.board.module_param("wan_type", "N")
        self.log = self.board.root / "run" / "wan-ports.log"

    def network(self, wan2_proto="dhcp", wan2_device="lan2"):
        self.board.write_config("network",
                                fx.default_network(wan2_device=wan2_device,
                                                   wan2_proto=wan2_proto))

    def run_ports(self, extra=None):
        env = {"SKW_SYS": fx.posix(self.board.root),
               "SKW_LOG": fx.posix(self.log)}
        if extra:
            env.update(extra)
        return self.board.run(fx.PORTS, [], extra_env=env)

    def written(self):
        if not self.attr.is_file():
            return ""
        return self.attr.read_text(encoding="utf-8")

    # ---- the type follows the protocol ----

    def test_a_pppoe_wan_makes_its_port_a_wan_type_port(self):
        self.network(wan2_proto="pppoe", wan2_device="lan2")
        proc = self.run_ports()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("lan2 1", self.written())

    def test_a_dhcp_wan_leaves_its_port_alone(self):
        self.network(wan2_proto="dhcp", wan2_device="lan2")
        proc = self.run_ports()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("lan2 1", self.written())

    def test_a_static_wan_leaves_its_port_alone(self):
        self.network(wan2_proto="static", wan2_device="lan2")
        proc = self.run_ports()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("lan2 1", self.written())

    def test_a_port_that_is_wan_type_and_must_not_be_is_put_back(self):
        """A jack that was a PPPoE WAN and is a DHCP one now."""
        self.network(wan2_proto="dhcp", wan2_device="lan2")
        self.attr.write_text("lan2 \n", encoding="utf-8", newline="\n")
        proc = self.run_ports()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("lan2 0", self.written())

    def test_the_optical_port_is_never_a_wan_type_port(self):
        """eth0 is not a jack of the switch; there is no type to set."""
        self.network(wan2_proto="pppoe", wan2_device="lan2")
        proc = self.run_ports()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("eth0 ", self.written())

    def test_the_conduit_is_never_written(self):
        self.network(wan2_proto="pppoe", wan2_device="lan2")
        proc = self.run_ports()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("lan-cpu0 ", self.written())

    # ---- the driver ----

    def test_the_driver_is_told_to_keep_its_hands_off_the_port_types(self):
        """With wan_type=1 the driver would make every jack that leaves the
        bridge a WAN-type port, whatever it is used for."""
        self.board.module_param("wan_type", "Y")
        self.network(wan2_proto="pppoe", wan2_device="lan2")
        proc = self.run_ports()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        param = (self.board.root / "sys" / "module" / "zx279133_switch" /
                 "parameters" / "wan_type")
        self.assertEqual(param.read_text(encoding="utf-8").strip(), "0")

    # ---- a write the driver refuses ----

    def test_a_refused_write_is_reported(self):
        """A port that keeps the wrong type is worth saying out loud."""
        self.network(wan2_proto="pppoe", wan2_device="lan2")
        # A directory cannot be written, which is what the driver says when
        # it refuses the port.
        self.attr.unlink()
        self.attr.mkdir(parents=True, exist_ok=True)
        proc = self.run_ports({"SKW_ATTR": fx.posix(self.attr),
                               "SKW_RETRIES": 2, "SKW_SLEEP": 0})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("could not be written",
                      self.log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    sys.exit(0 if fx.run_tests(sys.modules[__name__]) else 1)
