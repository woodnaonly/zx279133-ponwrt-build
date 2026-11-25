#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""A synthetic board for tools/trialsurvey.sh to read.

The instrument reads the board through a handful of directories, and it says
so in its own header: "The paths are variables so that the script can be
rehearsed against a synthetic tree before it is put on the board."  That hook
is what makes the instrument testable without a board.

The tree built here has the shape the instrument expects:

  <root>/class/net/<dev>/{operstate,carrier,mtu,statistics/*}
  <root>/class/leds/<led>/{brightness,trigger}
  <root>/kernel/debug/gpio

The trigger file is written the way the kernel writes it: every trigger the
lamp supports, with the one that is in effect in square brackets.

Two things are not directories and are therefore provided differently:

  - ethtool is a program, not a file in sysfs.  A stub is put in front of
    the PATH; it prints the statistics table the instrument parses.
  - the counters have to move between the instrument's two samples, or the
    verdict cannot tell a board that receives frames from a board that does
    not.  A writer thread moves them.

The writer is what makes the two samples different, and it also carries the
PON state and the OMCI channel over the same two steps: the state walks from
O1 to O5 and the channel becomes ready, which is what the driver's own code
does once there is light.
"""

import os
import re
import shutil
import threading
import time
from pathlib import Path

# The netdevs the instrument's first table lists, and how each one behaves.
# lan-cpu0 is the conduit, eth0 carries the driver's statistics and the PON
# attributes; lan3 and lan4 are down, so they never move.
NETDEVS = {
    "lan-cpu0": {"state": "up", "carrier": "1", "rate": 200},
    "lan0": {"state": "up", "carrier": "1", "rate": 100},
    "lan1": {"state": "up", "carrier": "1", "rate": 100},
    "lan2": {"state": "up", "carrier": "1", "rate": 100},
    "lan3": {"state": "down", "carrier": "0", "rate": 0},
    "lan4": {"state": "down", "carrier": "0", "rate": 0},
    "eth0": {"state": "up", "carrier": "1", "rate": 1000},
}

# The trigger file of a lamp, in the shape the kernel writes it: every
# trigger the lamp supports, with the one that is in effect in square
# brackets.  This is what the board really says - the LOS lamp reads
#
#   none default rfkill-any rfkill-none timer heartbeat default-on netdev
#   usbport [zx279133-los] zx279133-pon
#
# so the instrument has to work the trigger in effect out of the file
# instead of taking the whole line for it.
LOS_TRIGGER_FILE = (
    "none default rfkill-any rfkill-none timer heartbeat default-on "
    "netdev usbport [zx279133-los] zx279133-pon"
)

# The same file with another trigger in effect: the lamp is there, but the
# driver is not driving it.  The end-to-end check uses it to prove that the
# judge turns red on the lamp criterion and on no other.
LOS_TRIGGER_FILE_PON = (
    "none default rfkill-any rfkill-none timer heartbeat default-on "
    "netdev usbport [zx279133-pon] zx279133-los"
)

# The LAN1 lamp, on the netdev trigger - the board's green:lan1 reads the
# same way.
LAN1_TRIGGER_FILE = (
    "none rfkill-any rfkill-none timer heartbeat default-on [netdev] "
    "usbport zx279133-los zx279133-pon"
)

LEDS = {
    # On the board the lamp is called "skyworth:red:los".  Windows will not
    # create a directory with a colon in its name, so the synthetic board
    # uses a name that can be created - the instrument finds the lamp by the
    # driver's trigger, not by its name, and so does the judge.
    "los": {"brightness": "0", "trigger": LOS_TRIGGER_FILE},
    "link": {"brightness": "1", "trigger": LAN1_TRIGGER_FILE},
}

# The driver's four TX isolation counters, and the interface they sit on.
TX_STATS = (
    "tx_invalid_completions",
    "tx_recovery_requests",
    "tx_recovery_restarts",
    "tx_recovery_failures",
)

# The kernel's gpio dump, in the shape the current kernel writes it
# (gpiolib.c:5444): the number is the index inside the chip and not the
# flat number, the name of the consumer is in the column after the "|",
# and the level is written "hi" and "lo".  This is the shape the board's
# own dump has, and the shape the instrument has to read.
GPIO_DUMP = """\
gpiochip0: 16 GPIOs, parent: platform/10d10000.gpio, 10d10000.gpio:
 gpio-9   (                    |skyworth:red:los    ) out lo ACTIVE LOW
 gpio-10  (                    |skyworth:green:power) out lo ACTIVE LOW
gpiochip1: 16 GPIOs, parent: platform/10d10040.gpio, 10d10040.gpio:
 gpio-5   (                    |skyworth:green:usb1 ) out hi ACTIVE LOW
 gpio-8   (                    |skyworth:green:wan  ) out hi ACTIVE LOW
gpiochip2: 16 GPIOs, parent: platform/10d10080.gpio, 10d10080.gpio:
 gpio-1   (                    |opsd                ) in  hi
 gpio-3   (                    |skyworth:green:phone) out hi ACTIVE LOW
gpiochip3: 16 GPIOs, parent: platform/10d100c0.gpio, 10d100c0.gpio:
 gpio-15  (                    |skyworth:green:pon  ) out hi ACTIVE LOW
gpiochip4: 6 GPIOs, parent: platform/10d10100.gpio, 10d10100.gpio:
 gpio-3   (                    |skyworth:green:lan2 ) out lo ACTIVE LOW
 gpio-4   (                    |skyworth:green:lan3 ) out hi ACTIVE LOW
 gpio-5   (                    |skyworth:green:lan4 ) out hi ACTIVE LOW
"""

# The shape the older kernels wrote: the flat number, and no column for
# the consumer's name.  The instrument reads this one by the number, and
# this board proves that the fallback still works.
GPIO_DUMP_OLD = """\
GPIOs 0-31:
gpio-0  (gpio0)  : in  high IRQ ACTIVE LOW
gpio-1  (gpio1)  : in  low IRQ ACTIVE LOW
gpio-65 (gpio65) : in  high IRQ ACTIVE LOW
"""

# The kernel's symbol table, in the shape /proc/kallsyms has: address, type,
# name.  The judge looks the four driver symbols up in it, so the synthetic
# board has to carry them - and one of them has to be missable, so the
# end-to-end check can prove the judge turns red when a driver is not in
# the kernel.
KALLSYMS_SYMBOLS = (
    "zx279133_lan_netdev_open",
    "zx279133_lan_conduit_get_stats64",
    "zx279133_switch_get_tag_protocol",
    "zx279133_offload_attach",
)

# A symbol that is not one of ours, so the lookup cannot be a match on
# "there is a line at all".
KALLSYMS_NOISE = (
    "zx279133_lan_conduit_xmit",
    "zx279133_switch_setup",
)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


class Board:
    """A synthetic board, and the thread that moves its counters."""

    def __init__(self, root, missing_symbol=None, los_trigger=None,
                 pon_attr_in_device_dir=False, gpio_dump_style="new"):
        self.root = Path(root)
        self.net = self.root / "class" / "net"
        self.leds = self.root / "class" / "leds"
        self.debug = self.root / "kernel" / "debug"
        self.bin = self.root / "bin"
        # The trigger file of the LOS lamp.  It can be replaced so that the
        # end-to-end check can put another trigger in effect and see the
        # judge turn red on the lamp criterion alone.
        self.los_trigger = (LOS_TRIGGER_FILE if los_trigger is None
                            else los_trigger)
        # Where the PON attributes live: the netdev's own directory, which
        # is the shape the board has, or the parent device's directory,
        # which is the other shape a driver could publish them in.
        self.pon_attr_in_device_dir = pon_attr_in_device_dir
        # Which shape the kernel's gpio dump has: the current one, which
        # names the consumer and numbers the lines inside the chip, or the
        # older one, which prints the flat number.
        self.gpio_dump_style = gpio_dump_style
        # The kernel's symbol table.  "missing_symbol" takes one of the four
        # driver symbols out of it, which is what a board whose driver is
        # not in the kernel looks like.
        self.kallsyms = self.root / "kallsyms"
        self.missing_symbol = missing_symbol
        self.stop = threading.Event()
        self.thread = None
        self.ticks = 0
        self.light = True

    # ---- the tree ----------------------------------------------------

    def pon_attr_dir(self):
        """The directory the PON attributes are published in.

        The netdev's own directory is the shape the board has: the driver
        creates the attributes on ndev->dev, and ndev->dev is the netdev's
        own device, so they are at /sys/class/net/<netdev>/pon_state.  The
        netdev's "device" directory is the PARENT device's - the platform
        device the netdev hangs under - and the instrument looks there as
        well; the end-to-end check builds a board of that shape too, so
        that the fallback is not a branch nobody runs.
        """
        dev = self.net / "eth0"
        if self.pon_attr_in_device_dir:
            dev = dev / "device"
        return dev

    def build(self):
        if self.root.exists():
            shutil.rmtree(self.root)
        self.net.mkdir(parents=True)
        self.leds.mkdir(parents=True)
        self.debug.mkdir(parents=True)
        self.bin.mkdir(parents=True)

        for name, spec in NETDEVS.items():
            d = self.net / name
            (d / "statistics").mkdir(parents=True)
            write(d / "operstate", spec["state"] + "\n")
            write(d / "carrier", spec["carrier"] + "\n")
            write(d / "mtu", "1500\n")
            write(d / "statistics" / "rx_packets", "0\n")
            write(d / "statistics" / "rx_bytes", "0\n")
            write(d / "statistics" / "rx_dropped", "0\n")

        for name, spec in LEDS.items():
            d = self.leds / name
            d.mkdir(parents=True)
            write(d / "brightness", spec["brightness"] + "\n")
            trigger = spec["trigger"]
            if name == "los":
                trigger = self.los_trigger
            write(d / "trigger", trigger + "\n")

        dump = GPIO_DUMP_OLD if self.gpio_dump_style == "old" else GPIO_DUMP
        write(self.debug / "gpio", dump)

        # The kernel's symbol table.  The addresses do not matter; the shape
        # does - address, type letter, name - because that is what the
        # instrument's grep looks at.
        lines = []
        addr = 0xFFFFFFFFC0300000
        for name in KALLSYMS_SYMBOLS + KALLSYMS_NOISE:
            if name == self.missing_symbol:
                continue
            lines.append("%016x t %s" % (addr, name))
            addr += 0x40
        write(self.kallsyms, "\n".join(lines) + "\n")

        # The PON attributes live on the WAN netdev's own device, which
        # is the netdev's own directory in sysfs.  The instrument looked
        # in the parent device's directory only, read nothing at all and
        # reported the PON stack as not attached to any netdev.
        dev = self.pon_attr_dir()
        dev.mkdir(parents=True, exist_ok=True)
        write(dev / "pon_state", "O1\n")
        write(dev / "omci_transport",
              "implemented=1 ready=0 rx_ready=0 mic_ready=0 submitted=0 "
              "completed=0 error=-19\n")

        # The ethtool stub.
        stats = self.root / "ethtool-stats.txt"
        write(stats, "".join("%s: 0\n" % n for n in TX_STATS))
        stub = self.bin / "ethtool"
        write(stub, '#!/bin/sh\ncat "%s"\n' % str(stats).replace("\\", "\\\\"))
        stub.chmod(0o755)
        return self

    # ---- the writer ---------------------------------------------------

    def _bump(self):
        """Move the counters.  This happens all the time."""
        for name, spec in NETDEVS.items():
            if spec["state"] != "up":
                continue
            path = self.net / name / "statistics" / "rx_packets"
            cur = int(path.read_text().strip() or "0")
            path.write_text("%d\n" % (cur + 100 * spec["rate"]), encoding="utf-8")
            bytes_path = self.net / name / "statistics" / "rx_bytes"
            cur_b = int(bytes_path.read_text().strip() or "0")
            bytes_path.write_text("%d\n" % (cur_b + 1000 * spec["rate"]),
                                   encoding="utf-8")

    def _advance(self):
        """Move the PON state and the OMCI channel.

        This happens ONCE, between the instrument's two samples: the state
        walks from O1 to O5 and the channel becomes ready, which is what the
        driver's own code does once there is light.  It must not happen on
        every tick, or the instrument's first sample would already see the
        later state.
        """
        dev = self.pon_attr_dir()
        write(dev / "pon_state", "O5\n")
        write(dev / "omci_transport",
              "implemented=1 ready=1 rx_ready=0 mic_ready=0 submitted=12 "
              "completed=10 error=0\n")

    def advance(self):
        """Walk the PON state and make the OMCI channel ready.

        This is what the driver's own code does once there is light, and it
        has to happen between the instrument's two samples: the first one
        has to see O1 and a channel that is not ready, the second one O5
        and a ready channel.
        """
        self._advance()

    def start(self, interval=0.4):
        """Start moving the counters.

        The board's own state change is NOT timed here: it is driven by the
        caller, from the moment the instrument announces its second sample.
        That trigger does not depend on how fast the machine is - a timer
        would put the change in the wrong place on a slow host.
        """
        self.thread = threading.Thread(target=self._run, args=(interval,),
                                        daemon=True)
        self.thread.start()
        return self

    def _run(self, interval):
        while not self.stop.is_set():
            self.stop.wait(interval)
            if self.stop.is_set():
                return
            self._bump()

    def shutdown(self):
        self.stop.set()
        t = getattr(self, "thread", None)
        if t is not None:
            t.join(timeout=3)

    # ---- the environment the instrument reads -------------------------

    def env(self, extra=None):
        e = dict(os.environ)
        e["SK_CLASS_NET"] = str(self.net)
        e["SK_LEDS"] = str(self.leds)
        e["SK_DEBUGFS"] = str(self.debug)
        e["SK_OPSD_GPIO"] = "65"
        e["SK_OPSD_LABEL"] = "opsd"
        e["SK_STATDEV"] = "eth0"
        e["SK_KALLSYMS"] = str(self.kallsyms)
        # the stub has to come first, or the host's ethtool wins
        e["PATH"] = str(self.bin) + os.pathsep + e.get("PATH", "")
        if extra:
            e.update(extra)
        return e

    def set_light(self, on):
        """Move the optical RX signal detect and the lamp with it."""
        self.light = on
        gpio = self.debug / "gpio"
        text = gpio.read_text(encoding="utf-8")
        if self.gpio_dump_style == "old":
            word = "high" if on else "low"
            text = re.sub(r"(gpio-65 \(gpio65\) : in  )\S+",
                         r"\g<1>" + word, text)
        else:
            # The kernel writes the level as "hi" and "lo".
            word = "hi" if on else "lo"
            text = re.sub(r"(\|opsd\s*\)\s*in\s+)\S+",
                         r"\g<1>" + word, text)
        gpio.write_text(text, encoding="utf-8", newline="\n")
        led = self.leds / "los" / "brightness"
        led.write_text("0\n" if on else "1\n", encoding="utf-8", newline="\n")
