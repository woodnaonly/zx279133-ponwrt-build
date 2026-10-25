# 89 — The init script that was never executable

**Date:** 2026-10-25
**Subject:** the NAT rule's init script reached the image as 0644, because
that is how it is in the repository - so it never ran, and the masquerade
rule existed on the first boot only.

---

## 1. What was found

The NAT round on the CI's own release failed at the one thing it exists to
prove.  The board had the lease, the client behind the router had its
address, and the masquerade table was not there:

```
=== the masquerade rule and its counter ===
Error: No such file or directory
list table ip nat
              ^^^
```

## 2. Why the table was not there

The image carries the rule in two places, and only one of them was running.

**The uci-defaults** (`/etc/uci-defaults/99-sk-nat`) installs the rule
directly, and it runs on the first boot.  OpenWrt's boot script *sources*
the uci-defaults, so the file's mode does not matter - and the rule was
there after the first boot:

```
[   10.834216] sk-nat: the masquerade is on lan3
```

**The init script** (`/etc/init.d/sk-nat`) is what installs the rule on
every boot after that, through the `S96sk-nat` link in `/etc/rc.d`.  And
it was never run, because it is not executable:

```
root@ImmortalWrt:~# ls -l /etc/init.d/sk-nat
-rw-r--r--    1 root     root          3822 Oct 24 06:18 /etc/init.d/sk-nat
```

The reason is the repository:

```
$ git ls-files -s overlays/target/linux/zte/zx279133/base-files/etc/init.d/sk-nat
100644 d73fb7261a1b4cc3fc5b8d8bb888626869037856 0	overlays/target/linux/zte/zx279133/base-files/etc/init.d/sk-nat
```

`100644` is a file that is not executable.  The build copies it into the
image with that mode, and a script that is not executable is not run -
**silently, because the image still boots, and everything else in it still
works.**

This is the fourth time in this project that a difference between "what
the board says" and "what the build made" has cost a round, and the first
one where the difference was a single bit of the file mode.

## 3. What else was in the same state

The same sweep over the overlay found five more files that the image runs
and that were not executable:

| file | what runs it |
|---|---|
| `overlays/package/utils/zrec/files/etc/rc.d/S21zrec` | the boot, as an rc.d link |
| `overlays/package/utils/zrec/files/lib/preinit/85_zrec` | the boot, as a preinit hook |
| `overlays/package/utils/zrec/files/usr/libexec/zrec-bringup` | `zrec-bringup` |
| `overlays/package/utils/zrec/files/usr/libexec/zrec-ops` | `zrec-bringup` |
| `overlays/package/utils/zrec/files/usr/sbin/zrec-run` | `zrec-ops` |
| `overlays/target/linux/zte/zx279133/base-files/etc/uci-defaults/99-sk-nat` | the boot, sourced |
| `overlays/target/linux/zte/zx279133/base-files/lib/upgrade/platform.sh` | the upgrade flow |

The zrec files are the recovery service of the RAM image.  The one file in
this repository that was *already* right is
`overlays/target/linux/zte/zx279133/base-files/etc/board.d/02_network`
(100755) - which is why the board's LAN worked while the NAT did not.

## 4. The proof that the mode is the whole story

On the board, with the image as the CI built it:

```
root@ImmortalWrt:~# ls -l /etc/init.d/sk-nat
-rw-r--r--    1 root     root          3822 Oct 24 06:18 /etc/init.d/sk-nat
root@ImmortalWrt:~# nft list table ip nat
Error: No such file or directory
```

After `chmod +x` and running the script:

```
[  503.168825] sk-nat: masquerade installed on lan3
```

```
table ip nat {
	chain postrouting {
		type nat hook postrouting priority srcnat; policy accept;
		oifname "lan3" counter packets 0 bytes 0 masquerade
	}
}
```

Nothing else was changed: the same bytes, the same board, the same image.

## 5. The fix, and the gate that keeps it fixed

The eight files are 100755 in the repository now
(`git update-index --chmod=+x`), and `tools/prepush.py` has a seventh
check that refuses a push in which a file the image runs is not
executable:

```
prepush: the overlay's executable bits are checked (0 problems)
```

The check knows where a file is run rather than read: `/etc/init.d/`,
`/etc/rc.d/`, `/etc/uci-defaults/`, `/lib/preinit/`, `/usr/libexec/`,
`/usr/sbin/` and `/lib/upgrade/`.  Everything else - the C files, the
Makefiles, the firmware blob, the authorized key, the html - is left
alone.

## 6. What this cost, and what it proves

The image that was flashed and booted twice on 2026-10-25 - the CI's own
release, run 181 - is **correct in every way except this**, and this is
enough: a board flashed with it has the masquerade rule after its first
boot and none after its second.

So the round has to be run again, on the image that the fix produces.  That
is what the next run of the acceptance is for, and until that round has
passed, the honest statement is: **the CI builds and boots the image, and
the image's NAT rule does not survive the second boot.**  The fix is one
bit in the repository and one new check in `prepush.py`.
