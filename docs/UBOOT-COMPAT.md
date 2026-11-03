# SK-D840N vendor U-Boot FIT compatibility

`tools/sk_uboot.py` is a host-side validation module, not a bootloader image.
It supports the measured `2021.01-svn308776` console without requiring the
optional `iminfo` command. It has no serial, SSH, flash, or environment-save I/O.

Before using it, the controller must verify the actual `help bootm` response,
exclusive serial ownership, a fresh physical OFF/ON cycle, board identity,
release SHA256/FIT hashes, available RAM and reserved ranges, and transfer
length/CRC. Set only temporary `autostart=no` and `verify=yes`, then read them
back; never use `saveenv`.

`verified_boot()` requires `bootm start 0x89000000` to verify both `kernel-1`
and `fdt-1` in `config-1`, with the exact CRC32/SHA1 values from the validated
image manifest, and return to the prompt without loading/executing the OS.
It checks the same RAM CRC again, calls the controller's physical-power gate,
and only then permits a full `bootm`, whose hash checks are also recorded.
Missing commands or failed checks have no direct-boot fallback.

Every response must belong to one command transaction and receive cursor,
bound to the same physical boot. A prior console tail or a previous `OK` is
not evidence. The caller must additionally bind the transaction to its own
attempt/session and reject concurrent commands, restarts and stale proofs.

The module distinguishes `ramoops:` from a real `Oops:`. An exact JFFS2 EOF
warning can be identified only when the four-byte word read from the device
matches the EOF word at an eraseblock boundary in an independently
SHA-verified published image. This does not suppress other magic/CRC errors
or establish that the rest of a mounted filesystem is intact.

No helper authorizes flash writes, relaxes partition sizes or ECC checks, or
permits a 241 MiB writable root to be downgraded to a 240 MiB DTB. Backup data,
SSH keys, serial recordings and per-device attempts remain local and ignored.

Run the portable fault-injection tests with:

```sh
python3 tests/test_sk_uboot.py
```

The SK-D840N workflow runs this suite before both build modes. Passing it is
an offline controller check, not RAM, flash, cold-start or physical LED/PON
hardware acceptance.
