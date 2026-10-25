# 81 — The console can change the board's network, and the reason it could not was a glob

**Date:** 2026-10-25
**Measured on:** the board running the flashed image (kernel 6.18.38+)

## What was wrong with the last round

The NAT round on the flashed image has to move the board's WAN port onto the
port the PC's cable is in (`lan2`), because `lan3` has no cable on this
bench. Moving the port means taking `lan2` out of the bridge, and that takes
the board's LAN address (192.168.9.1) off this PC's segment — so the board
becomes unreachable over the network at exactly the moment the change is
made.

HANDOFF recorded that the change had to be made over the console, and that
on the console it does not work:

> `uci delete network.@device[0].ports` on the serial console gives a parse
> error, so `uci add_list` appends to the list that was already there and
> lan2 never leaves the bridge.

That is the wrong diagnosis, and it is worth correcting because it is what
kept the change on the network, where it cannot survive.

## The cause is the shell, not uci

`@device[0]` is a glob pattern. The console's shell expands it against the
current directory, which has no such name, so what reaches `uci` is not what
was typed — and `uci` answers with a parse error that names the syntax
rather than the shell.

With the argument quoted, `uci` sees it literally:

```
uci -q get "network.@device[0].ports"   ->   lan1 lan2 lan4 lan-cpu0
```

and the whole move works over the console:

```
uci delete "network.@device[0].ports"
uci add_list "network.@device[0].ports"=lan1
uci add_list "network.@device[0].ports"=lan4
uci add_list "network.@device[0].ports"=lan-cpu0
uci set network.wan.device=lan2
uci set network.wan6.device=lan2
uci commit network
```

Read back over ssh afterwards: `ports = lan1 lan4 lan-cpu0`,
`wan = lan2`. The bridge really lost `lan2`, and the wan really moved.

## Why the console is the only place the change can be made

The board is on a smart plug that cuts its power for about fifteen seconds
every fifteen and a half minutes. That is not a detail of this bench — it is
the bench's way of producing the cold boots the acceptance needs.

It also means a change that waits for something is racing the plug. Measured
on 2026-10-25: the port was moved over ssh at 10:25, the board rebooted
before the lease could be read, and it came back with `wan=lan3` — the
change had gone with the power.

The console does not depend on the network, so the change survives the port
leaving the bridge. And with `uci commit` it survives the power cut as
well, because the commit writes to the jffs2 root. Verified: the board was
cut off by the plug after the change, came back 57 seconds later, and
reported `wan=lan2` and `ports=lan1 lan4 lan-cpu0` — exactly what had been
written.

## The second thing the round found: dropbear only listens on the lan

With the port moved, the board is reachable on its WAN address on the shared
subnet (192.168.137.215), and it answers there — except that ssh does not
work:

```
tcp  0  0  192.168.9.1:22  0.0.0.0:*  LISTEN
```

`dropbear.main.Interface` is `lan`, so the ssh daemon listens on the lan
address only, and the lan address is on the other side of the router now.
The fix is the image's own configuration:

```
uci set dropbear.main.Interface="lan wan"
uci commit dropbear
/etc/init.d/dropbear restart
```

after which the board answers on 192.168.137.215 and the whole round can be
read from the PC side. (Put back to `lan` afterwards — that is what the
image ships.)

## The evidence

The flashed image is a router. Read from the board on its WAN address:

```
table ip nat {
	chain postrouting {
		type nat hook postrouting priority srcnat; policy accept;
		oifname "lan2" counter packets 47 bytes 2572 masquerade
	}
}
```

The counter is the part that matters. A masquerade on a port with no cable
is installed, looks right in `nft list`, and counts nothing.

The client behind the router — a veth pair in a network namespace of the
board's own making, with its lease from the board's own DHCP server:

```
namespace: labcli
9: labcli0@if10: ... inet 192.168.9.226/24 scope global labcli0
default via 192.168.9.1 dev labcli0
223.5.5.5: 2 packets transmitted, 2 packets received, 0% packet loss
```

and the flow in the conntrack table, with the address rewritten by the
board's own masquerade:

```
ipv4 2 icmp 1 19 src=192.168.9.226 dst=223.5.5.5 type=8 code=0 id=3362 \
      src=223.5.5.5 dst=192.168.137.215 type=0 code=0 id=3362 use=2
```

`192.168.9.226` is the client behind the router; `192.168.137.215` is the
board's WAN address; the rewrite between them is the NAT.

Full evidence: `staging/ctl/g4nat-evidence.txt`.

## The scripts

| file | what it does |
|---|---|
| `staging/ctl/bssh2.sh` | one ssh to the board, the command read from a file |
| `staging/ctl/portmove.sh` | move the WAN port over the console, or put it back |
| `staging/ctl/clientrun.sh` | put a client behind the router and wait for its lease |
| `staging/ctl/natcounters.sh` | the rule, its counter, the conntrack flow, the client's view |
| `staging/ctl/natevidence.sh` | the same, from the board's WAN address |
| `staging/ctl/restore.sh` | remove the client, put the port back, put dropbear back |
| `staging/ctl/saveevidence.py` | write the evidence to a file |

## One more trap: Git bash rewrites the board's paths

A command meant for the board goes through `python q.py '/etc/init.d/dropbear
restart'`, and Git bash turns the argument into
`C:/Program Files/Git/etc/init.d/dropbear restart` before Python ever sees
it. The board answers `-ash: C:/Program: not found`.

`MSYS_NO_PATHCONV=1` in the environment of the call turns that off. It has
to be set for the call, not for the shell: the board's paths are the only
ones that must not be rewritten.
