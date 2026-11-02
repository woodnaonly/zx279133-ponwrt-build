#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Assert image contents, not just selected config symbols. Existing size/zrec gates remain separate.
set -eu
TREE=${1:?OpenWrt tree}
TREE=$(cd "$TREE" && pwd)
ROOT=$(find "$TREE/build_dir" -maxdepth 3 -type d -name root-zte | head -1)
[ -n "$ROOT" ] || { echo 'No staged root-zte image'; exit 1; }
MANIFEST=$(find "$TREE/bin/targets" -name '*.manifest' | head -1)
[ -n "$MANIFEST" ] || { echo 'No target image manifest'; exit 1; }
for package in zxpond libzxpon-omci luci-app-sk-d840n-pon luci-theme-argon luci-i18n-base-zh-cn luci-i18n-argon-config-zh-cn firewall4 nftables luci-app-firewall luci-i18n-firewall-zh-cn rpcd-mod-ucode ucode-mod-fs; do
	grep -q "^$package " "$MANIFEST" || { echo "Image manifest is missing $package"; exit 1; }
done
[ -L "$ROOT/etc/rc.d/S96zxpond" ] || { echo 'Protocol daemon is not enabled in the image'; exit 1; }
for file in usr/sbin/zxpond etc/init.d/zxpond sbin/fw4 etc/init.d/firewall etc/config/firewall \
	usr/sbin/nft usr/share/rpcd/ucode/luci.skd840n-pon usr/share/rpcd/ucode/luci.skd840n-system \
	usr/share/rpcd/acl.d/luci-app-sk-d840n-pon.json usr/share/luci/menu.d/luci-app-sk-d840n-pon.json \
	www/luci-static/resources/skd840n/ui.js www/luci-static/argon/css/cascade.css; do
	[ -s "$ROOT/$file" ] || { echo "Image root is missing $file"; exit 1; }
done
for view in overview hardware config internet iptv acceleration diagnostics cpu; do
	[ -s "$ROOT/www/luci-static/resources/view/skd840n/$view.js" ] || { echo "Missing $view page"; exit 1; }
done
[ "$(stat -c %a "$ROOT/etc/pon/identity")" = 600 ] || { echo 'Legacy credentials are not mode 0600'; exit 1; }
UCODE="$TREE/staging_dir/hostpkg/bin/ucode"
[ -x "$UCODE" ] || { echo 'No build host ucode interpreter'; exit 1; }
python3 tests/test_sk_pon_rpc.py "$UCODE"
python3 tests/test_sk_pon_firstboot.py --ucode "$UCODE"
python3 tests/test_sk_pon_target.py "$ROOT"
python3 tools/check-sk-lan1-led.py --root "$ROOT"
python3 tests/test_sk_lan1_led.py --root "$ROOT"
FIREWALL=$(find "$TREE/build_dir" -maxdepth 2 -type d -name 'firewall4-*' | head -1)
[ -n "$FIREWALL" ] || { echo 'No firewall4 source to test'; exit 1; }
# Upstream tests exercise the shipped rule generator with real ucode and mock ubus/UCI.
(cd "$FIREWALL" && PATH="$(dirname "$UCODE"):$PATH" bash run_tests.sh)
echo 'PASS: final staged root/manifest contain management UI, single protocol daemon, pinned theme and firewall4'
