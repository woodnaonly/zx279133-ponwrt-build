#!/usr/bin/env python3
"""Offline checks for the SK-D840N port-status fix.

PHYSR speed is the Realtek mask (bits 5:4 together with bits 10:9), because
bits 5:4 of the 2500 sample 0x1b1e are 1, not 3. The two samples that have
to decode are 0x192c -> 1000 full link, and 0x1b1e -> 2500 full link.
The outer-port callback may read only 1f.0xa434.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SW = ROOT / "kernel-overlays/drivers/net/ethernet/zte/zx279133-switch.c"
DTS = ROOT / "kernel-overlays/arch/arm64/boot/dts/zte/zx279133-sk-d840n.dts"
JS = ROOT / "overlays/package/utils/sk-port-status/files/29_ports.js"
MK = ROOT / "overlays/package/utils/sk-port-status/Makefile"

SPEED_MASK = 0x630  # GENMASK(5, 4) | GENMASK(10, 9)
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


check(decode(0x192c) == {"speed": 1000, "duplex": "full", "link": True},
      "0x192c is 1000 full, link up")
check(decode(0x1b1e) == {"speed": 2500, "duplex": "full", "link": True},
      "0x1b1e is 2500 full, link up")
check(((0x1b1e >> 4) & 3) == 1,
      "bits 5:4 of 0x1b1e are 1, so that field alone is not the speed")
check(decode(0x1602) is None, "0x1602 is not a speed this jack publishes")

src = SW.read_text(encoding="utf-8")
for case, rate in (("0x0020", "SPEED_1000"), ("0x0210", "SPEED_2500"),
                   ("0x0000", "SPEED_10"), ("0x0010", "SPEED_100")):
    check(("case %s:" % case) in src and rate in src,
          "driver decodes %s as %s" % (case, rate))

body = func_body(src, "zx279133_switch_outer_link_state")
check("PHYSR" in body and "mdiobus_c45_read" in body,
      "outer link state reads PHYSR")
check("AN_CTRL" not in body and "SERDES_MODE" not in body and "0x00" not in body,
      "outer link state does not read the AN BMCR")
check(body.count("mdiobus_c45_read") == 1,
      "outer link state performs one MDIO read")

dts = DTS.read_text(encoding="utf-8")
check('label = "lan5"' in dts and "speed = <2500>;" in dts,
      "netdev stays lan5 and the DTS ceiling stays 2500")

js = JS.read_text(encoding="utf-8")
check("const SK_PANEL=['lan5','lan2','lan3','lan4']" in js, "panel order is LAN1..LAN4")
check("lan5:'LAN1'" in js and "lan2:'LAN2'" in js and "lan4:'LAN4'" in js,
      "silk names")
check("SK_PANEL_NAME[port.device]" in js, "the box title is the silk name")
check("SK_PANEL.indexOf(port.device)>=0" in js, "internal ports are filtered out")
check("port.netdev.getSpeed()" in js, "speed text still comes from the netdev")
req = js.find("'require baseclass'")
panel = js.find("SK_PANEL=")
check(req != -1 and panel != -1 and req < panel,
      "require statements stay before any other string, or LuCI drops the class")

mk = MK.read_text(encoding="utf-8")
check("DEPENDS:=+luci-mod-status" in mk, "the view is installed after luci-mod-status")
check("29_ports.js" in mk, "the package ships 29_ports.js")

sys.exit(1 if fail else 0)