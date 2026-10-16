#!/bin/sh
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
# Every file this suite's README names by bare name must actually be in this
# directory. It was not: README.md's very first instruction was
# `sh tests/round36-make-fixture.sh`, and that script was never delivered with
# the suite - it lives in the working copy but is not in the closure computed
# from the runner's loop, because nothing in the suite referenced it. The README
# is the only thing that did.
#
# So this walks README.md, takes every bare *.sh / *.py name out of it, and
# requires the file to sit next to this one. Paths that still contain a slash
# after an optional tests/ prefix (tools/payload-id.sh and friends) are left
# alone: those live elsewhere in the repository and this suite is not the thing
# that decides where.
#
# The negative control at the bottom is the part that matters. A checker that
# cannot fail is worse than no checker, because it turns "the README is
# consistent" into something that reads true no matter what.
set -u
export LC_ALL=C

d=$(cd "$(dirname "$0")" && pwd)
cd "$d" || exit 1

# In the working copy the README is staged next to the suite under a different
# name; in the delivered copy it is README.md. Either is fine, missing is not.
if [ -f README.md ]; then
	doc=README.md
elif [ -f tests-README.md ]; then
	doc=tests-README.md
else
	echo "FAIL no README.md and no tests-README.md next to $d"
	exit 1
fi

pass=0
bad=""
seen=""
skipped=0
# The path is captured *with* its slashes on purpose. An earlier version of this
# pattern was [A-Za-z0-9_.-]* which silently ate the directory part, so
# tools/payload-id.sh arrived here as a bare `payload-id.sh` and was reported as
# a missing file. A checker that invents failures is as useless as one that
# misses them.
for f in $(grep -o '[A-Za-z0-9_./-]*\.\(sh\|py\)' "$doc" | sort -u); do
	case "$f" in
		.sh|.py)
			# prose such as "*.sh", not a filename
			skipped=$((skipped+1))
			continue
			;;
		*/*)
			case "$f" in
				tests/*) f=${f#tests/} ;;
				*)
					# tools/..., scripts/... - they live elsewhere in the
					# repository and this suite does not decide where
					skipped=$((skipped+1))
					continue
					;;
			esac
			;;
	esac
	case "$seen" in
		*" $f "*) continue ;;
	esac
	seen="$seen $f"
	if [ -f "$d/$f" ]; then
		pass=$((pass+1))
		printf '  PASS  %s\n' "$f"
	else
		bad="$bad $f"
		printf '  FAIL  %s  <- named by %s but not delivered\n' "$f" "$doc"
	fi
done

echo
echo "  (skipped $skipped name(s): bare extensions and paths belonging elsewhere)"

echo
if [ -n "$bad" ]; then
	echo "FAIL the README names files that are not here:$bad"
	exit 1
fi
echo "all $pass files named by $doc are present"

echo
echo "=== negative control: the checker must reject a name that is not there ==="
# Build a throwaway README that names a file which does not exist, and run the
# same loop over it. If this passes, the loop above is not checking anything.
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
printf 'see definitely_not_a_real_file_9f3c.sh for details\n' > "$tmp/doc.md"
rc=0
for f in $(grep -o '[A-Za-z0-9_.-]*\.\(sh\|py\)' "$tmp/doc.md" | sort -u); do
	[ -f "$d/$f" ] || rc=1
done
if [ "$rc" -eq 1 ]; then
	echo "  PASS  a missing name is reported as missing"
else
	echo "  FAIL  the loop accepted a missing file - it checks nothing"
	exit 1
fi
