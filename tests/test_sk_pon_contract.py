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
# The one grant beyond ubus and uci, and it is narrow on purpose.
#
# The WAN page has to run the helper that moves a panel jack out of the
# bridge: the bridge's port list is a LIST option, and the uci channel
# has no add_list, so a list written through that channel is one string
# and the bridge the kernel builds then holds nothing the user asked
# for.  One named program, one method, no wildcard - and the helper
# checks its own arguments (the device has to be one of the panel jacks,
# the primary one of the two WANs).
ALLOWED_EXEC = {"/usr/libexec/sk-wan-switch": ["exec"]}
for role in acl.values():
    for access in ("read", "write"):
        grants = role.get(access, {})
        files = grants.get("file", {})
        assert not files or files == ALLOWED_EXEC, (role, access, files)
        assert not (set(grants) - {"ubus", "uci", "file"})
        assert "*" not in json.dumps(grants)
# The write scope: the uci sections the page owns - the PON's own, the
# network's, and the WAN policy file - and the one program it may run.
# Nothing else: no other section, no other program, no wildcard.
assert acl["luci-app-sk-d840n-pon"]["write"] == {
    "uci": ["pon", "network", "sk_wan"],
    "ubus": {"file": ["exec"]},
    "file": ALLOWED_EXEC,
}
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
    # The page may name only the program the ACL allows, so that the two
    # cannot drift apart.  Only the page's own javascript: the ucode
    # plugins have a shebang, and an interpreter is not a program the
    # page runs.
    if resource.suffix == ".js":
        for program in re.findall(r"/usr/(?:s?bin|libexec)/[A-Za-z0-9._-]+", source):
            assert program in ALLOWED_EXEC, (resource, program)
configure = (ROOT / "scripts/configure-sk-d840n.sh").read_text(encoding="utf-8")
# include/scan.mk scans recipe text before evaluating luci.mk. Without this
# signature a custom LuCI package disappears entirely, not merely as '=n'.
for relative in ("luci-app-sk-d840n-pon/Makefile", "libs/libzxpon-omci/Makefile", "network/config/firewall4/Makefile"):
    recipe = ROOT / "overlays/package" / relative
    assert re.search(r"call (Build/DefaultTargets|BuildPackage|KernelPackage)", recipe.read_text(encoding="utf-8")), recipe
for package in ("firewall4", "luci-app-sk-d840n-pon", "luci-theme-argon"):
    assert "CONFIG_PACKAGE_" + package + "=y" in configure
assert "539e3e1db56b2348c3655615540e45638b797c73" in (ROOT / "scripts/prepare-sk-d840n-ui.sh").read_text(encoding="utf-8")
assert "1.96.1" in (ROOT / ".github/workflows/build-sk-d840n.yml").read_text(encoding="utf-8")
fw4 = ROOT / "overlays/package/network/config/firewall4"
assert "PATCH_DIR:=./sk-patches" in (fw4 / "Makefile").read_text(encoding="utf-8")
assert "PKG_BUILD_DEPENDS:=ucode/host" in (fw4 / "Makefile").read_text(encoding="utf-8")
assert sorted(p.name[:3] for p in (fw4 / "sk-patches").glob("*.patch")) == ["002", "003", "004", "005"]
assert "bash run_tests.sh" in (ROOT / "scripts/check-sk-d840n-ucode.sh").read_text(encoding="utf-8")
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
print("PASS: 8 menu targets, bounded RPC/ACL (one named program, no wildcard), no donor runtime paths, UTF-8, source pins and build selections")
