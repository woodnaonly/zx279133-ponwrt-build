#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
TREE=${1:?OpenWrt directory}
TREE=$(cd "$TREE" && pwd)
ARGON="$TREE/feeds/luci-theme-argon"
PIN=539e3e1db56b2348c3655615540e45638b797c73
mkdir -p "$TREE/feeds"
if [ ! -d "$ARGON/.git" ]; then git init "$ARGON"; fi
git -C "$ARGON" diff --quiet
git -C "$ARGON" diff --cached --quiet
[ -z "$(git -C "$ARGON" ls-files --others --exclude-standard)" ] || {
	echo 'Argon checkout contains untracked files; refusing to replace it' >&2; exit 1;
}
if [ "$(git -C "$ARGON" rev-parse --verify HEAD 2>/dev/null || true)" != "$PIN" ]; then
	git -C "$ARGON" fetch --depth=1 https://github.com/jerrykuku/luci-theme-argon.git "$PIN"
	git -C "$ARGON" checkout --detach "$PIN"
fi
if [ -e "$TREE/package/luci-theme-argon" ] && [ ! -L "$TREE/package/luci-theme-argon" ]; then
	echo 'Refusing to replace an unmanaged Argon package' >&2; exit 1
fi
# Argon 3 integrates the config app and its translations. The nested upstream
# compat/ recipe keeps the old package name without installing duplicate files.
# Only remove the managed feed links, never a user's real package directory.
for old in "$TREE/package/feeds/luci/luci-app-argon-config" "$TREE/package/feeds/luci/luci-theme-argon"; do
	if [ -L "$old" ]; then
		case "$(readlink -f "$old")" in
			"$TREE/feeds/luci/applications/luci-app-argon-config"|"$TREE/feeds/luci/themes/luci-theme-argon") rm -f "$old" ;;
			*) echo "Refusing to replace an unexpected Argon feed link: $old" >&2; exit 1 ;;
		esac
	elif [ -e "$old" ]; then
		echo "Refusing to replace an unmanaged Argon feed package: $old" >&2; exit 1
	fi
done
ln -sfn "$ARGON" "$TREE/package/luci-theme-argon"
