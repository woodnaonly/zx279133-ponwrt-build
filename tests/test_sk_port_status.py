#!/usr/bin/env python3
"""Offline checks for the SK-D840N port names and the copper speed.

The stock overview prints netdev names from board.json. Panel LAN1 is
netdev lan1 (DSA port 8). The jackless port is lan0 and is not published
there. PHYSR decoding is unchanged: 0x192c is 1000 full link, 0x1b1e is
2500 full link. The outer-port callback reads only 1f.0xa434.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SW = ROOT / "kernel-overlays/drivers/net/ethernet/zte/zx279133-switch.c"
DTS = ROOT / "kernel-overlays/arch/arm64/boot/dts/zte/zx279133-sk-d840n.dts"
NET = ROOT / "overlays/target/linux/zte/zx279133/base-files/etc/board.d/02_network"
BRIDGE = ROOT / "overlays/target/linux/zte/zx279133/base-files/etc/init.d/sk-bridge"
UCI = ROOT / "overlays/target/linux/zte/zx279133/base-files/etc/uci-defaults/99-sk-bridge"
JS = ROOT / "overlays/target/linux/zte/zx279133/base-files/usr/share/sk-port-status/29_ports.js"
OLD = ROOT / "overlays/target/linux/zte/zx279133/base-files/etc/uci-defaults/99-sk-ports"

SPEED_MASK = 0x630
SPEEDS = {0x0000: 10, 0x0010: 100, 0x0020: 1000, 0x0210: 2500}

fail = 0


def check(cond, msg):
    global fail
    if cond:
        print("PASS", msg)
    else:
        print("FAIL", msg)
        fail += 1


def decode(raw):
    code = raw & SPEED_MASK
    if code not in SPEEDS:
        return None
    return {
        "speed": SPEEDS[code],
        "duplex": "full" if raw & 0x8 else "half",
        "link": bool(raw & 0x4),
    }


def func_body(src, name):
    start = src.find(name)
    if start < 0:
        return ""
    brace = src.find("{", start)
    depth = 0
    for i in range(brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[brace:i + 1]
    return ""


def port_label(dts, reg):
    needle = "reg = <%d>;" % reg
    at = dts.find(needle)
    if at < 0:
        return ""
    window = dts[at:at + 200]
    key = 'label = "'
    lab = window.find(key)
    if lab < 0:
        return ""
    lab += len(key)
    return window[lab:window.find('"', lab)]


check(decode(0x192c) == {"speed": 1000, "duplex": "full", "link": True},
      "0x192c is 1000 full, link up")
check(decode(0x1b1e) == {"speed": 2500, "duplex": "full", "link": True},
      "0x1b1e is 2500 full, link up")
check(((0x1b1e >> 4) & 3) == 1,
      "bits 5:4 of 0x1b1e are 1, so that field alone is not the speed")

src = SW.read_text(encoding="utf-8")
body = func_body(src, "zx279133_switch_outer_link_state")
check("PHYSR" in body and "mdiobus_c45_read" in body, "outer link state reads PHYSR")
check("0x00" not in body and body.count("mdiobus_c45_read") == 1,
      "outer link state performs one MDIO read and does not touch AN BMCR")

dts = DTS.read_text(encoding="utf-8")
check(port_label(dts, 8) == "lan1", "DSA port 8, the panel LAN1, is netdev lan1")
check(port_label(dts, 4) == "lan0", "DSA port 4, the jackless port, is netdev lan0")
check('label = "lan5"' not in dts, "there is no lan5 netdev")
check("speed = <2500>;" in dts, "the DTS ceiling stays 2500")

net = NET.read_text(encoding="utf-8")
check("for d in lan1 lan2 lan3 lan4; do" in net, "board.json is built from the four panel names")
check("lan5" not in net, "02_network does not look for lan5")
check('ipaddr "192.168.9.1"' in net, "the lan address is still 192.168.9.1")
check('ucidef_set_interface "wan"' not in net, "wan is not published in board.json")

bridge = BRIDGE.read_text(encoding="utf-8")
uci = UCI.read_text(encoding="utf-8")
check("lan-cpu0" in bridge and "lan0" in bridge, "sk-bridge adds the CPU port and lan0")
check("network.wan.device=eth0" in bridge and "network.wan.proto=dhcp" in bridge,
      "sk-bridge creates the wan slot on eth0")
check("IPKG_INSTROOT" in bridge and "CI" in uci, "the image build does not edit the network config")
check("setsid" not in uci and "&" not in uci, "uci-defaults does not background anything")
check("S15sk-bridge" in uci, "the init script is linked for later boots")
check(not JS.exists() and not OLD.exists(), "no LuCI view is shipped over the stock one")

sys.exit(1 if fail else 0)