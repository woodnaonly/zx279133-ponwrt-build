#!/bin/sh
# Fetch the run's initramfs .itb into the bench's trial directory and prove it.
#
#	sh tools/getitb.sh			# newest initramfs release
#	sh tools/getitb.sh 185			# pin the run number in the tag
#
# Why a second fetcher next to gettrial.sh: the .itb is the whole RAM trial for
# this U-Boot (`tftpboot 0x89000000 sk-d840n-initramfs.itb; bootm 0x89000000`,
# one file, one command, zero flash writes), and it is not a tarball - so
# gettrial.sh, which verifies by extracting into a scratch directory, cannot
# check it.  This script hashes the file against the release's own manifest and
# puts it where the tftp server roots.
set -eu
REPO=woodnaonly/zx279133-ponwrt-build
FILE=sk-d840n-initramfs.itb
RUN=${1:-}
HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
D=${DEST:-$HERE/../../staging/trial}
mkdir -p "$D"
cd "$D"

# The API answer goes to a file first, and is read as UTF-8 on purpose: this
# PC's python is a native Windows build, and its default codec is GBK, which
# eats the multi-byte author names in the middle of the JSON and then reports a
# perfectly complete 906 KB answer as "Unterminated string starting at char
# 311696" (measured 2026-10-26 14:15).  A truncated download and a mis-decoded
# one look identical here unless the encoding is pinned.
curl -sfL -m 150 -o releases.json \
	"https://api.github.com/repos/$REPO/releases?per_page=40" || {
	echo "getitb: the release list did not come down" >&2
	exit 1
}
sel=$(python -c "
import json
want_run = '$RUN'
found = []
with open('releases.json', encoding='utf-8') as fh:
    for r in json.load(fh):
        tag = r.get('tag_name', '')
        parts = tag.split('_')
        if len(parts) != 4 or parts[0] != 'skd840n' or parts[1] != 'initramfs':
            continue
        if want_run and parts[2] != want_run:
            continue
        assets = {a['name']: a['browser_download_url'] for a in r.get('assets', [])}
        if '$FILE' in assets and 'sha256sums' in assets:
            found.append((int(parts[2]), tag, assets['$FILE'],
                          assets['sha256sums']))
if not found:
    raise SystemExit(1)
for _, tag, url, man in sorted(found, reverse=True)[:1]:
    print(tag, url, man)
") || { echo "getitb: no published initramfs release carries $FILE" >&2; exit 1; }
set -- $sel
rel=$1
url=$2
man=$3
echo "getitb: release $rel"

# Direct dies with curl rc=56 from this PC, ghfast.top just times out, and the
# local proxy has done 12 MB in under 45 s - so the proxy is the second try and
# the mirror the third (measured 2026-10-10, still true 2026-10-26).
dl() {
	rm -f "$2"
	curl -sfL -m 60 -o "$2" "$1" && [ -s "$2" ] && return 0
	echo "getitb: direct failed, trying the proxy" >&2
	curl -sfL -m 900 -x http://127.0.0.1:10808 -o "$2" "$1" && [ -s "$2" ] && return 0
	echo "getitb: proxy failed, trying the ghfast.top mirror" >&2
	curl -sfL -m 900 -o "$2" "https://ghfast.top/$1" && [ -s "$2" ]
}

# The initramfs release publishes `sha256sums` (one line, the .itb) and
# `sha256sums-trial` (the tgz's members), and only the flash release publishes
# `sha256sums-release`.  Both releases' manifests used to land in the same local
# name and the second fetch silently replaced the first, so this one is stored
# under a name that says which asset it is.
dl "$man" "sha256sums-initramfs-itb"
dl "$url" "$FILE.part"
want=$(awk -v F="$FILE" '$2 == F {print $1}' sha256sums-initramfs-itb)
if [ -z "$want" ]; then
	echo "getitb: the release's sha256sums does not name $FILE at all" >&2
	exit 1
fi
got=$(sha256sum "$FILE.part" | cut -d' ' -f1)
if [ "$got" != "$want" ]; then
	echo "getitb: MISMATCH got=$got want=$want - the file is left out of the way" >&2
	mv -f "$FILE.part" "$FILE.rejected-$rel"
	exit 1
fi
mv -f "$FILE.part" "$FILE"
echo "getitb: $FILE is $rel's, sha256 $got, $(wc -c < "$FILE") bytes"
echo "$rel  $url" > "$FILE.source"
ls -l "$FILE"
