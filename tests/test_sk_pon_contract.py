#!/usr/bin/env python3
"""Architecture/security contracts that do not need an OpenWrt checkout or device."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "overlays/package/luci-app-sk-d840n-pon"
RUNTIME = PACKAGE / "root"
menu = json.loads((RUNTIME / "usr/share/luci/menu.d/luci-app-sk-d840n-pon.json").read_text(encoding="utf-8"))
acl = json.loads((RUNTIME / "usr/share/rpcd/acl.d/luci-app-sk-d840n-pon.json").read_text(encoding="utf-8"))
assert set(acl) == {"luci-app-sk-d840n-pon", "luci-skd840n-system"}
for role in acl.values():
    for access in ("read", "write"):
        assert not (set(role.get(access, {})) - {"ubus", "uci"})
        assert "*" not in json.dumps(role.get(access, {}))
        assert "file" not in role.get(access, {})
assert acl["luci-app-sk-d840n-pon"]["write"] == {"uci": ["pon", "network"]}
assert "write" not in acl["luci-skd840n-system"]
for route, node in menu.items():
    if node["action"]["type"] == "view":
        assert (PACKAGE / "htdocs/luci-static/resources/view" / (node["action"]["path"] + ".js")).is_file(), route
for resource in PACKAGE.rglob("*"):
    if not resource.is_file() or resource.name in ("LICENSE", "UPSTREAM"):
        continue
    source = resource.read_text(encoding="utf-8")
    assert not source.startswith("\ufeff"), resource
    assert b"\r" not in resource.read_bytes(), resource
    assert not re.search(r"TYFZA1|tyfza1|pon0|0x4f[0-9a-f]{7}", source), resource
    if resource.name.startswith("luci.skd840n"):
        assert not re.search(r"\b(system|popen|exec|writefile)\s*\(", source), resource
configure = (ROOT / "scripts/configure-sk-d840n.sh").read_text(encoding="utf-8")
for package in ("firewall4", "luci-app-sk-d840n-pon", "luci-theme-argon"):
    assert "CONFIG_PACKAGE_" + package + "=y" in configure
assert "539e3e1db56b2348c3655615540e45638b797c73" in (ROOT / "scripts/prepare-sk-d840n-ui.sh").read_text(encoding="utf-8")
assert "1.96.1" in (ROOT / ".github/workflows/build-sk-d840n.yml").read_text(encoding="utf-8")
kernel = ROOT / "kernel-overlays/drivers/net/ethernet/zte"
assert "ZXPON_CMD_OMCI_TX" in (kernel / "zx279133-pon.c").read_text(encoding="utf-8")
assert "GENL_ADMIN_PERM" in (kernel / "zx279133-pon.c").read_text(encoding="utf-8")
sender = (kernel / "zx279133-omci.c").read_text(encoding="utf-8")
assert "readl_poll_timeout" in sender and "skb_put_padto" not in sender
for define in ("ZXPON_CMD_OMCI_TX", "ZXPON_A_OMCI_FRAME", "ZXPON_A_MAX", "ZXPON_CMD_MAX"):
    values = [re.search(r"#define\s+" + define + r"\s+(\d+)", p.read_text(encoding="utf-8"))[1]
              for p in (kernel / "zxpon.h", ROOT / "overlays/package/utils/zxpond/src/zxpon.h")]
    assert values[0] == values[1], define
assert "version 2 of the GNU General Public License" in (ROOT / "overlays/package/libs/libzxpon-omci/omci/LICENSE").read_text(encoding="utf-8")
print("PASS: 8 menu targets, bounded read-only RPC/ACL, no donor runtime paths, UTF-8, source pins and build selections")
