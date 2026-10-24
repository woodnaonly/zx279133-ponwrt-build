# The flashed image had no firewall, and a board that boots is not a router

**2026-10-25.** The G4 flash worked: the board boots out of the NAND, twice,
and `verify_router.sh` says *ROUTER IS USABLE*. But the image it boots is not
a router. It serves LuCI, it hands out DHCP leases, and it cannot route a
single packet, because there is nothing in it that masquerades.

This is what was missing, what it took to find out, and what it is fixed
with.

## What the flashed image actually had

Measured on the board on 2026-10-25, on the image the 2026-10-25 build
produced (run 165) and that was written to the NAND:

```
# nft list tables
table inet dnsmasq

# ls /etc/init.d/
boot  cron  dnsmasq  done  dropbear  gpio_switch  led  log  network
packet_steering  rpcd  sr1010-netled  sysctl  sysfixtime  system  ucitrack
uhttpd  umount  urandom_seed  urngd  zx279133-wan-ports

# ls /etc/rc.d/ | grep -i fire
(nothing)
```

There is no `fw4` table, no `/etc/init.d/firewall`, and no firewall4 in the
image at all. Everything else a router needs is there:

| what | where |
|---|---|
| the netfilter kernel | built in (`CONFIG_NETFILTER`, `CONFIG_NF_TABLES`, `CONFIG_NFT_NAT`, `CONFIG_NFT_MASQ` are pinned in the target's config fragments) |
| the nftables userspace | `overlays/package/network/utils/nftables` (the feed's recipe without the kmod dependency) |
| the DHCP server | dnsmasq, `dhcp-range=set:lan,192.168.9.100,192.168.9.249,255.255.255.0,12h` |
| forwarding | `ip_forward=1` |
| the address | `network.lan.ipaddr=192.168.9.1/24`, from `base-files/etc/board.d/02_network` |

So the image is a Linux box with a DHCP server on it. What is missing is
exactly one thing: a rule that rewrites the source address of the traffic
leaving the WAN.

## Why firewall4 is not there, and why that is not a mistake

The build has no feed packages. The `nftables` package is not taken from the
feed either - `overlays/package/network/utils/nftables/Makefile` is the
feed's recipe with the `kmod-nft-core` dependency removed, because this
branch has no netfilter kmod packages to depend on (the kernel is mainline,
so there is no kmod package for it at all; `package/kernel/modules/nf_tables.ko`
is a 404).

`firewall4` is a feed package and it is not in the tree. Reproducing it
would mean reproducing its whole dependency chain (`ucode`, the
`ucode-mod-fw4*` family, `jsonfilter`, `libustream-*`, and the templates in
`/usr/share/firewall4/`) out of overlays - a large, fragile thing to build
blind, and the failure mode of getting it wrong is a board that answers ssh
and routes nothing.

## The fix: one rule, in one file

The router needs one rule. It is now installed by
`overlays/target/linux/zte/zx279133/base-files/etc/init.d/sk-nat`, with the
same nft syntax the trial image's `S26nat` uses, and the same table name
(`ip nat`) so the acceptance scripts read the counters with the command they
already use:

```
table ip nat {
	chain postrouting {
		type nat hook postrouting priority 100; policy accept;
		oifname "lan2" counter packets 0 bytes 0 masquerade
	}
}
```

Three details in it are not obvious:

* **the counter goes before the masquerade.** `masquerade` is a terminal
  statement and nft rejects anything that follows it (*Statement after
  terminal statement has no effect*), so the rule written the other way
  round never loads - and the WAN side is never masqueraded. This is the
  same lesson the trial image's `S26nat` already carries.

* **the port is read from the config, every fifteen seconds.** The bench
  moves the WAN port around by hand (`uci set network.wan.device=lan2`),
  and a rule left on the old port masquerades nothing while looking
  exactly like a rule that works.

* **an init script has to be in `/etc/rc.d` to run at all.** The link is
  made by `base-files/etc/uci-defaults/99-sk-nat`, on the first boot, and
  the link itself then lives in the jffs2 root. It is created there rather
  than shipped as a link because a git checkout on Windows cannot represent
  a symbolic link (`core.symlinks` is false there, and the checkout produces
  a regular file with the target path in it) - a link in the repository
  would reach the build as a file and the script would never run, silently,
  because the image would still boot.

`/etc/rc.local` was considered for this and rejected: it is a conffile of
`base-files`, and a conffile that two packages provide is resolved in favour
of the one already on the device - so the replacement would have been
dropped without a word.

## How it is checked

The build refuses to ship an image without it, twice over:

* **in the tree** - the files have to have reached
  `openwrt/target/linux/zte/zx279133/base-files/`, they have to parse
  (`sh -n`), they must not carry a CR byte (they are shell scripts and the
  device runs them), the rule has to be the counted masquerade, and the
  uci-defaults has to link the script in;

* **in the image** - the built rootfs is searched for the script's name and
  for the line that starts it. jffs2 keeps its names as plain strings, so
  both are in there, and a rootfs without them is a rootfs whose board
  cannot route.

## What the round measured

On the flashed image, with the WAN moved to the port the test PC is in and
the client made by the board itself behind the router:

| check | what it showed |
|---|---|
| the masquerade rule | `oifname "lan2" counter packets N bytes M masquerade` |
| the client's lease | an address in 192.168.9.0/24 from the board's own dnsmasq |
| the client's ping | `RC=0` against 223.5.5.5 |
| conntrack | an entry with the client's source address and a destination outside the LAN |
| the counter | the masquerade rule counting packets |
| the WAN | its tx byte counter moving |

The client is the board's own: the bench has one cable, so the LAN side has
no external client, and `trial-files/etc/rc.d/S27labclient` (a veth pair
with one end in its own network namespace) is copied onto the board to be
that client. It is a trial file and is not in the flashed image.
