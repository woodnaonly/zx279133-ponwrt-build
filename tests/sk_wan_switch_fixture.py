#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""A board in a directory, for the WAN scripts to run in.

WHAT THIS IS

The three programs that move the second WAN around - sk-wan-switch,
sk-wan-adapt and zx279133-wan-ports - are shell scripts that talk to the
board: they read /etc/config, they write /sys/class/net, they run the
network service.  A test that only reads their text says nothing about
what they do to the board.

So the tests run them.  This module is the board they run on: a directory
with the configuration files in it, a /sys/class/net with the DSA jacks in
it, and the commands they call - uci, ip, brctl, ubus and udhcpc - replaced
by the fakes in tests/fixtures/sk-wan.  The scripts themselves are not
modified: they are run as they ship, with the paths they use pointed at the
sandbox.

    board = Board(tmp)
    board.setup()
    board.run(SWITCH, ["apply-wan2", "--device", "lan2", "--proto", "dhcp"])
    print(board.config_text("network"))

The fakes read the same directory, so what a script writes into the
configuration is what the next command reads back.
"""

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "overlays" / "target" / "linux" / "zte" / "zx279133" / "base-files"
SWITCH = BASE / "usr" / "libexec" / "sk-wan-switch"
ADAPT = BASE / "usr" / "libexec" / "sk-wan-adapt"
PORTS = BASE / "usr" / "libexec" / "zx279133-wan-ports"
FAKES = ROOT / "tests" / "fixtures" / "sk-wan"

# The panel jacks of the board, and the jackless one.
JACKS = ("lan0", "lan1", "lan2", "lan3", "lan4")
CONDUIT = "lan-cpu0"


def find_sh():
    """The shell the scripts run with."""
    found = shutil.which("sh")
    if found:
        return found
    # A Windows box with Git for Windows has the shell, but not on PATH.
    for candidate in (r"C:\Program Files\Git\usr\bin\sh.exe",
                      r"C:\Program Files\Git\bin\bash.exe"):
        if os.path.isfile(candidate):
            return candidate
    raise RuntimeError("no sh on PATH; the tests run the shipped scripts")


def posix(path):
    """A path the shell understands, on a box whose paths use backslashes."""
    return str(path).replace("\\", "/")


class Board:
    """A board in a directory."""

    def __init__(self, root):
        self.root = Path(root)
        self.state = self.root / "state"
        self.bin = self.root / "bin"
        self.sh = find_sh()

    # ---- the directory ----

    def setup(self):
        """A board that is switched on: the directories and the commands."""
        for d in ("etc/config", "etc/init.d", "run", "tmp", "bin", "state",
                  "sys/class/net", "sys/module/zx279133_switch/parameters",
                  "state/addr", "state/neigh", "state/iface"):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        for name in ("ip", "brctl", "ubus", "udhcpc", "service"):
            self._install(FAKES / name, self.bin / name)
        self._install(FAKES / "service", self.bin / "network-init")
        self._install(FAKES / "service", self.bin / "firewall-init")
        # The fake uci is a program, so it is run by the interpreter that is
        # running this test and not by the shell: the shell of a Windows box
        # has no python in it.
        self._install(FAKES / "uci", self.bin / "uci", wrapper=True)
        return self

    def _install(self, source, target, wrapper=False):
        target.parent.mkdir(parents=True, exist_ok=True)
        if wrapper:
            target.write_text("#!/bin/sh\nexec \"%s\" \"%s\" \"$@\"\n"
                              % (posix(sys.executable), posix(source)),
                              encoding="utf-8", newline="\n")
        else:
            shutil.copyfile(source, target)
        try:
            target.chmod(0o755)
        except OSError:
            pass

    # ---- the configuration ----

    def write_config(self, name, text):
        path = self.root / "etc" / "config" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    def config_text(self, name):
        """The committed configuration file, as it is on the board."""
        path = self.root / "etc" / "config" / name
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")

    def uci_get(self, path):
        """What uci answers for one path."""
        proc = self.run(None, None, uci_get=path)
        return proc.stdout.strip()

    # ---- the netdevs ----

    def netdev(self, name, lower=None, dsa=False, master=None, address=None):
        d = self.root / "sys" / "class" / "net" / name
        d.mkdir(parents=True, exist_ok=True)
        if lower:
            (d / ("lower_" + lower)).mkdir(parents=True, exist_ok=True)
        if dsa:
            (d / "dsa").mkdir(parents=True, exist_ok=True)
        if master:
            (d / ("master_" + master)).mkdir(parents=True, exist_ok=True)
        if address:
            self.set_address(name, address)
        return self

    def set_address(self, name, address):
        f = self.state / "addr" / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(address + "\n", encoding="utf-8", newline="\n")

    def bridge(self, name, members):
        """A bridge with its members and the port number of each."""
        d = self.root / "sys" / "class" / "net" / name
        d.mkdir(parents=True, exist_ok=True)
        for number, member in enumerate(members, start=1):
            brif = d / "brif" / member
            brif.mkdir(parents=True, exist_ok=True)
            (brif / "port_no").write_text("%d\n" % number, encoding="utf-8",
                                         newline="\n")
        return self

    def neighbour(self, address, mac, dev="br-lan"):
        f = self.state / "neigh" / address
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("%s dev %s lladdr %s REACHABLE\n" % (address, dev, mac),
                     encoding="utf-8", newline="\n")

    def showmacs(self, text):
        f = self.state / "showmacs"
        f.write_text(text, encoding="utf-8", newline="\n")

    def interface_up(self, name, up=True):
        f = self.state / "iface" / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("up\n" if up else "down\n", encoding="utf-8",
                     newline="\n")

    def module_param(self, name, value):
        f = (self.root / "sys" / "module" / "zx279133_switch" / "parameters" /
             name)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(value + "\n", encoding="utf-8", newline="\n")

    def wan_ports_attr(self, text=""):
        """The attribute the driver publishes the port types in."""
        f = (self.root / "sys" / "class" / "net" / "eth0" / "device" /
             "wan_ports")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8", newline="\n")
        return f

    # ---- running ----

    def env(self, extra=None):
        """The environment the scripts run in."""
        env = dict(os.environ)
        for name in ("CI", "IPKG_INSTROOT", "PKG_ROOT"):
            env.pop(name, None)
        env.update({
            "SKW_PREFIX": posix(self.root),
            "SKW_UCI": posix(self.bin / "uci"),
            "UCI_ROOT": posix(self.root),
            "FAKE_STATE": posix(self.state),
            "SKW_WATCHDOG": "0",
            "SKW_NETWORK_INIT": posix(self.bin / "network-init"),
            "SKW_FIREWALL_INIT": posix(self.bin / "firewall-init"),
            "PATH": self._path(env),
        })
        if extra:
            env.update({k: str(v) for k, v in extra.items()})
        return env

    def _path(self, env):
        """The PATH the shell runs with.

        A Windows box that runs the tests with the shell of Git for Windows
        needs that shell's own tools - tr, sed and awk - and it needs them
        with a colon between them, which is not what a Windows PATH looks
        like.  On a Linux runner the PATH is the one the runner has.
        """
        entries = [posix(self.bin)]
        if sys.platform.startswith("win") and "git" in self.sh.lower():
            # The shell of Git for Windows is an MSYS2 shell: its own tools
            # are at /usr/bin inside it, and that is the path that works.
            entries.append("/usr/bin")
            entries.append("/bin")
            return ":".join(entries)
        entries.append(env.get("PATH", ""))
        return os.pathsep.join(entries)

    def run(self, script, args, extra_env=None, uci_get=None, timeout=300):
        """Run one of the shipped scripts, or the fake uci alone."""
        if uci_get is not None:
            cmd = [self.sh, posix(self.bin / "uci"), "-q", "get", uci_get]
        else:
            cmd = [self.sh, posix(script)] + [str(a) for a in args]
        proc = subprocess.run(cmd, cwd=str(self.root), env=self.env(extra_env),
                              capture_output=True, text=True, timeout=timeout)
        return proc


def default_network(bridge=("lan1", "lan2", "lan3", "lan4"),
                    wan2_device=None, wan2_proto="dhcp"):
    """The network configuration the board ships with.

    The second WAN is in the file from the first boot on: an interface with
    no device, disabled, and the backup's metric.  A device means the second
    WAN is in use, and then it is not disabled.  The section is there
    because the board has it there - the firewall zone names the network
    'wan2', and a zone whose network does not exist is what fw4 complains
    about.
    """
    ports = "".join("\tlist ports '%s'\n" % p for p in bridge)
    text = ("config device\n"
            "\toption name 'br-lan'\n"
            "\toption type 'bridge'\n"
            + ports + "\n" +
            "config interface 'wan'\n"
            "\toption device 'eth0'\n"
            "\toption proto 'dhcp'\n"
            "\toption metric '10'\n"
            "\n"
            "config interface 'wan2'\n"
            "\toption proto '%s'\n" % wan2_proto +
            "\toption metric '20'\n")
    if wan2_device:
        text += "\toption device '%s'\n" % wan2_device
    else:
        text += "\toption disabled '1'\n"
    return text


def default_firewall():
    """The firewall configuration the board ships with."""
    return ("config zone\n"
            "\toption name 'wan'\n"
            "\toption network 'wan'\n"
            "\toption input 'REJECT'\n"
            "\toption output 'ACCEPT'\n"
            "\toption forward 'REJECT'\n"
            "\toption masq '1'\n"
            "\n"
            "config forwarding\n"
            "\toption src 'lan'\n"
            "\toption dest 'wan'\n")


def default_sk_wan():
    """The policy file the board ships with."""
    return ("config settings 'wan'\n"
            "\toption auto '1'\n"
            "\toption primary 'wan'\n"
            "\toption exclude ''\n")


def run_tests(module):
    """The tests of one module, with a verdict a machine with no token can read.

    The job log of a workflow run is 403 for a machine with no token, so a
    failure that only says "the step failed" is a failure nobody can act on
    from outside.  The annotations of a check run are public, and a gate
    that fails is worth saying in one.

    The runner is built here rather than through unittest.main() so that
    this works on every Python the gate jobs run on: unittest.TextTestResult
    only exists from 3.11 on, and the gate jobs run 3.10.
    """
    suite = unittest.TestLoader().loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    for test, traceback in list(result.failures) + list(result.errors):
        # One annotation per line: the page shows the first line of an
        # annotation and hides the rest behind a button, so a failure whose
        # assertion is on the fourth line would say nothing about what it
        # was.
        for line in traceback.strip().splitlines():
            print("::error::%s: %s" % (test, line), flush=True)
    return result.wasSuccessful()


def make_board(tmp, network=None, firewall=None, sk_wan=None):
    """A board with the configuration the board ships with."""
    board = Board(tmp).setup()
    board.write_config("network", network if network is not None
                       else default_network())
    board.write_config("firewall", firewall if firewall is not None
                       else default_firewall())
    board.write_config("sk_wan", sk_wan if sk_wan is not None
                       else default_sk_wan())
    return board
