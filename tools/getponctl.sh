#!/bin/sh
# Fetch the CI-built aarch64 ponctl client into staging/ponctl and check it before the
# device ever sees it.
#
#	sh tools/getponctl.sh
#
# Releases are public, so no token is read - which matters because this file is in the
# repo. Ranked by run id rather than by the API's order: on 2026-10-10 the release list
# put three older builds above the newest one, and taking the first match would flash a
# stale binary.
set -eu

REPO=woodnaonly/zx279133-ponwrt-build
D=staging/ponctl
FILE=ponctl

mkdir -p "$D"
cd "$D"

# python3 on this PC is a stub, so ask for `python`.
runid() { echo "$1" | sed 's/.*_//'; }
set -- $(curl -sf "https://api.github.com/repos/$REPO/releases?per_page=40" |
	python -c "
import json, sys
def runid(tag):
    return int(tag.rsplit('_', 1)[-1])
found = []
for r in json.load(sys.stdin):
    tag = r.get('tag_name', '')
    if not tag.startswith('ponctl_'):
        continue
    for a in r.get('assets', []):
        if a['name'] == 'ponctl':
            found.append((tag, a['browser_download_url']))
for tag, url in sorted(found, key=lambda t: runid(t[0]), reverse=True):
    print(tag, url)
    sys.exit(0)
")
[ $# -eq 2 ] || { echo "no published release carrying ponctl yet" >&2; exit 1; }
rel=$1
url=$2

echo "downloading $url (release $rel)"
# Direct dies with curl rc=56 after ~19 s against this network; the local proxy does the
# whole asset in seconds (measured on the trial kit).
dl() { # url dest
	rm -f "$2"
	curl -sfL -m 60 -o "$2" "$1" && [ -s "$2" ] && return 0
	echo "direct failed, trying the proxy"
	curl -sfL -m 300 -x http://127.0.0.1:10808 -o "$2" "$1" && [ -s "$2" ]
}
dl "$url" "$FILE"
chmod +x "$FILE"

u=$(curl -sf "https://api.github.com/repos/$REPO/releases/tags/$rel" |
	python -c "
import json, sys
for a in json.load(sys.stdin)['assets']:
    if a['name'] == 'sha256sums-ponctl':
        print(a['browser_download_url']); break
")
[ -n "$u" ] || { echo "no sha256sums-ponctl in release $rel" >&2; exit 1; }
dl "$u" sha256sums-ponctl >/dev/null
echo "verifying against $rel"
sha256sum -c sha256sums-ponctl

ls -l "$FILE"
echo "next: sh tools/zpush.sh $FILE  (then ponctl status on the device)"
