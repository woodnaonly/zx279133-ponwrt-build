#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
cd "$(dirname "$0")/.."
BUILD=$(mktemp -d)
trap 'rm -rf "$BUILD"' EXIT HUP INT TERM
SOURCE=overlays/package/utils/zxpond/src
LIB=overlays/package/libs/libzxpon-omci
CARGO=${ZX_CARGO:-cargo}
[ -z "${ZX_CARGO:-}" ] || export RUSTC="$(dirname "$ZX_CARGO")/rustc"
"$CARGO" test --manifest-path "$LIB/omci/Cargo.toml" --locked
"$CARGO" build --manifest-path "$LIB/omci/Cargo.toml" --locked
gcc -Wall -Wextra -Werror -DZX_CONFIG_STANDALONE -I"$SOURCE" -I"$LIB" \
	tests/sk_pon_config.c "$SOURCE/zxpon-config.c" -o "$BUILD/config"
"$BUILD/config"
gcc -Wall -Wextra -Werror -Ikernel-overlays/drivers/net/ethernet/zte \
	tests/sk_pon_descriptor.c -o "$BUILD/descriptor"
"$BUILD/descriptor"
gcc -Wall -Wextra -Werror -I"$SOURCE" tests/sk_pon_transport.c "$SOURCE/zxpon-transport.c" \
	-Wl,--wrap=socket,--wrap=setsockopt,--wrap=close,--wrap=sendto,--wrap=recvfrom -o "$BUILD/transport"
"$BUILD/transport"
for test in sk_pon_frames sk_omci_apply sk_omci_key_replay; do
	extra=
	[ "$test" != sk_pon_frames ] || extra="$SOURCE/zxpon-backend.c"
	gcc -Wall -Wextra -I"$SOURCE" -I"$LIB" "tests/$test.c" $extra \
		"$LIB/omci/target/debug/libzxpon_omci.a" -lpthread -ldl -lm -o "$BUILD/$test"
	"$BUILD/$test"
done
