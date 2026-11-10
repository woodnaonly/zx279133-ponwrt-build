# SK-D840N ImmortalWrt Firmware

[简体中文](README.md) | English

ImmortalWrt firmware for the Skyworth SK-D840N (ZX279133), with RAM recovery images and a separate Bootweb recovery project. This repository contains board support, driver overlays, build scripts and tests. GitHub Actions fetches pinned upstream sources to compile the images; this is not a complete OpenWrt source tree where you can run `make` directly.

[Download firmware](https://github.com/woodnaonly/zx279133-ponwrt-build/releases) · [Build runs](https://github.com/woodnaonly/zx279133-ponwrt-build/actions/workflows/build-sk-d840n.yml)

> Before flashing, back up configuration and device-specific data, verify the model, bootloader and partition layout, and disable scheduled power cuts such as smart-plug timers. Stop if validation fails; do not force an upgrade. A normal system upgrade neither installs Bootweb nor migrates the partition layout.

## Contents

- [Project and support status](#status)
- [Download and verification](#download)
- [First login and network setup](#setup)
- [System upgrades](#upgrade)
- [Initial installation and recovery](#recovery)
- [Building your own images](#build)
- [Troubleshooting](#faq)
- [Maintenance references and sources](#references)

<a id="status"></a>

## Project and support status

The target is the SK-D840N with a ZX279133 SoC, two Cortex-A53 cores, 512 MiB RAM and 256 MiB SPI-NAND. Sharing the same SoC does not make an SR1010 or another ONU compatible. Compatibility is not guaranteed across all hardware revisions, ISP firmware or U-Boot versions sold under the same model name.

The current build uses ImmortalWrt 25.12.1, pinned to base commit `1b50aaf4`. The ZX279133 driver base is pinned to `96e5a241`, with official stable patches applied through Linux **6.18.60**, followed by this repository's overlays. See the [kernel version configuration](configs/sk-d840n-stable.json) for versions and patch checksums, and each release's `kernel-provenance.json` for its exact sources.

| Component | Current status and limits |
| --- | --- |
| Boot, system installation and management UI | Records document installation of build #337, normal cold boot, default LAN and LuCI access on the test device on 2026-11-05; this does not validate every later build on hardware |
| Partition layout | Current images use `native241`: a 241 MiB root, no separate scratch/recovery partition; root is `mtd5` and the kernel partition is 8 MiB |
| Ethernet | Code supports the four panel LAN ports; LAN1 is the physical 2.5G port. Rated speed or CI success does not establish negotiated speed, 2.5G throughput, cold-boot behavior or long-term stability |
| LuCI management | Includes Argon, ONU management pages and firewall4; having a UI does not establish working line service |
| PON / OMCI | Driver and management interfaces and an OMCI engine are integrated, but automatic optical activation, RX demultiplexing and the OMCI integrity-key lifecycle remain incomplete; this is not firmware with completed ISP registration support |
| Hardware acceleration | Build checks cover kernel/tool availability; hardware offload of real traffic has not completed acceptance testing. The ONU acceleration page is read-only; offload options on the native firewall page require careful operator testing |
| Bootweb | A specific test device has resident installation and physical Reset re-entry records; public CI packages remain offline candidate prereleases, not universally flashable U-Boot images |

See the [#337 acceptance report](bootweb/reports/2026-11-05-native241-337-acceptance.json) and [ONU management notes](docs/100-sk-d840n-onu-management-port.md) for evidence and PON limitations. Passing CI confirms that build and the checks it ran, not optical registration, unbricking or hardware performance.

<a id="download"></a>

## Download and verification

### Choose the correct file

Open [Releases](https://github.com/woodnaonly/zx279133-ponwrt-build/releases) and select a **flash** release from a successful build. Its tag follows `skd840n_flash_<run_number>_<run_id>`. Check the commit and release notes, then confirm the corresponding `flash` job and artifact validation succeeded in Actions. Do not simply download the newest entry: initramfs and Bootweb releases serve different purposes.

| File | Purpose | Upload directly to LuCI system upgrade? |
| --- | --- | --- |
| `immortalwrt-sk-d840n-sysupgrade.bin` | Preferred upgrade file for an already compatible system | Yes; use the file as downloaded, without unpacking |
| `immortalwrt-sk-d840n-sysupgrade.bin.sha256` | SHA256 for the firmware above | No |
| `flash-sysupgrade.bin` | Byte-identical compatibility filename for the preferred upgrade file | Yes, under the same compatibility conditions |
| `flash-set.tgz` | Maintenance/recovery bundle with partition images, flasher and checksum manifest | No |
| `flash-uImage`, `flash-dtb.bin`, `flash-rootfs.jffs2` | Partition-level maintenance images; all must come from the same build | No |
| `sk-d840n-initramfs.itb` | RAM trial/recovery image from initramfs mode | No |
| `trial-set.tgz` | kexec trial kernel, DTB and scripts; kexec tools supplied separately | No |
| `sk-d840n-bootweb-native241-candidate.tar` | Separate offline Bootweb candidate for a specific original U-Boot base | No |
| `sk-d840n-bootweb-native241-tools.tgz`, `sha256sums-bootweb` | Bootweb toolkit and checksum manifest for its two download packages | No |

Release Assets are direct file downloads. Actions Artifacts arrive inside a ZIP wrapper that must be extracted first; the `sk-d840n-sysupgrade` artifact contains the upgrade `.bin` and its checksum. The `.bin` internally uses the sysupgrade tar format as part of the firmware protocol; do not unpack it again.

### Verify SHA256 on Windows

Save the firmware and its `.sha256` file from the same release into one folder, then open PowerShell in that folder:

```powershell
$firmware = 'immortalwrt-sk-d840n-sysupgrade.bin'
$expectedHash = ((Get-Content -LiteralPath "$firmware.sha256" -Encoding UTF8 -Raw).Trim() -split '\s+')[0]
$actualHash = (Get-FileHash -LiteralPath $firmware -Algorithm SHA256).Hash
if ($expectedHash -notmatch '^[0-9a-fA-F]{64}$' -or $actualHash -ne $expectedHash) {
    throw 'SHA256 mismatch'
}
$actualHash
```

Do not continue if a file is missing or verification fails. SHA256 checks file integrity; it does not establish hardware compatibility or the trustworthiness of the download source.

<a id="setup"></a>

## First login and network setup

These defaults apply to a fresh configuration of this project's firmware. An upgrade that keeps settings retains the previous address, password and port assignments.

1. Connect the computer directly to a LAN port. Disconnect other uplinks during initial setup to avoid address or DHCP conflicts with an existing network.
2. Try automatic IP configuration first. If no lease is obtained, temporarily assign the computer `192.168.9.2/24`; leave gateway and DNS unset.
3. Open [LuCI](http://192.168.9.1/cgi-bin/luci/). The username is `root`, with no password set in a fresh configuration. Set a root password under **System → Administration** before connecting other networks.
4. SSH uses `root@192.168.9.1`. Connect from Windows PowerShell:

```powershell
ssh root@192.168.9.1
```

You can also set the password with `passwd` in the device's SSH shell. Images do not embed a personal SSH public key or device identity. Never expose management access with an unset password to an untrusted network.

### Ports and Ethernet WAN

| Panel connection | System device | Default role |
| --- | --- | --- |
| LAN1 (physical 2.5G port) | `lan1` | LAN member of `br-lan` |
| LAN2 / LAN3 / LAN4 | `lan2` / `lan3` / `lan4` | LAN members of `br-lan` |
| PON-side MAC | `eth0` | Target of the default WAN configuration; not a panel RJ45 port |

All four RJ45 ports default to LAN; none is reserved for Ethernet WAN. `lan0` and `lan-cpu0` are internal interfaces, not extra panel ports. Do not remove them merely to tidy the port list.

To assign a port as WAN, using LAN4 as an example:

1. Connect the management computer to LAN2 or LAN3, keeping a management port outside the change.
2. Under **Network → Interfaces → Devices**, edit `br-lan` and remove `lan4` from its bridge ports. Keep the remaining LAN and internal interfaces.
3. Edit WAN, select `lan4` as its device, and choose DHCP client, PPPoE or a static address as required by the uplink. Check the WAN firewall zone and LAN-to-WAN forwarding. If using IPv6, also check the WAN6 binding.
4. Save and apply. Verify management still works through a remaining LAN port, then connect the uplink to LAN4.

LAN4 is no longer a management LAN port. Do not assign the same port to both `br-lan` and WAN. The ONU Internet and IPTV/multicast pages edit existing WAN/bridge configuration; they do not automatically create a complete network topology.

### ONU management and acceleration

The ONU menu in LuCI provides status, hardware identity, line/authentication, Internet, IPTV, acceleration and diagnostics pages. CPU status is under System. Device identity and protocol settings reside in `/etc/config/pon`; missing identity data is not filled with another device's identity.

Use only parameters supplied for your own device and ISP. Saving SN or LOID fields does not establish OLT registration; Ethernet carrier, a software O-state or engine readiness alone is not evidence of working service. Do not publish configuration backups, SN, REGID, PSK or LOID passwords.

Hardware offload options are on the native firewall page. For testing, use forwarded traffic between LAN and WAN endpoints, and check hardware flow counters in `/sys/kernel/debug/zx279133-flows`, conntrack's `HW_OFFLOAD` status and correct traffic in both directions. Pinging the router, loading microcode or seeing `hw-tc-offload: on` does not validate hardware forwarding.

<a id="upgrade"></a>

## System upgrades

These steps apply only to devices already running a compatible system from this project, with a bootloader and partition layout matching the target image. They are not a guaranteed direct upgrade path from stock or other community firmware.

### Checks and backups before upgrading

- Disable all scheduled power cuts and maintain continuous power. Have a recovery method appropriate to this device ready. Writes run root → dtb → kernel; power loss or a bad kernel can still leave the system unbootable.
- Download a configuration backup to the computer through LuCI's **System → Backup / Flash Firmware** page. This is not a full NAND, U-Boot or device-specific calibration/identity backup; keep those original backups separately and securely.
- Check the target model, build source and checksum. Current releases use `native241`. Do not directly upgrade a legacy 240 MiB layout to 241 MiB, or downgrade in the opposite direction.
- The current upgrade script checks partition labels, indices, offsets, capacities and erase sizes, rejecting unknown or mismatched layouts. An older system lacking those checks is not thereby compatible. Do not bypass rejection with `sysupgrade -F`, force options or manual partition writes.

Use these read-only commands in the device's SSH shell to inspect its version and root capacity:

```sh
ubus call system board
cat /proc/mtd
cat /sys/class/mtd/mtd5/size
```

A root capacity of `252706816` bytes is 241 MiB; `251658240` bytes is 240 MiB. Capacity alone does not prove the whole layout matches. Layout migration requires separate preparation, validation and backups; it is not a normal system upgrade.

### Using LuCI

1. Upload the verified `immortalwrt-sk-d840n-sysupgrade.bin` under **System → Backup / Flash Firmware**. Do not upload a ZIP, `flash-set.tgz` or a Bootweb candidate.
2. Read the validation results. Stop on model, capacity, layout or format errors.
3. Keep settings when upgrading within a compatible series if you want to retain network settings and passwords. Uncheck that option for a clean configuration; the system will return to its default address with no password set.
4. Confirm, then wait for writing and reboot to finish. Do not remove power, press Reset or submit again. After reconnecting, check the version, LAN/WAN, password and required services.

Keeping settings carries only the supported configuration backup contents; it does not automatically preserve every installed package or arbitrary file. Configuration compatibility across versions still needs checking.

### Using SSH

Verify the firmware on the computer as described above. If you have not already downloaded a configuration backup, run this in the device's SSH shell:

```sh
sysupgrade -b /tmp/sk-d840n-config-backup.tar.gz
```

Return to Windows PowerShell to retrieve the backup and upload the firmware. Use `scp -O` for systems without an SFTP server. The current directory should contain the verified firmware:

```powershell
scp -O root@192.168.9.1:/tmp/sk-d840n-config-backup.tar.gz .
scp -O .\immortalwrt-sk-d840n-sysupgrade.bin root@192.168.9.1:/tmp/
```

Confirm the backup is saved on the computer. Run the following in the device's SSH shell to check the file and test upgrade eligibility without writing firmware:

```sh
cd /tmp
sha256sum immortalwrt-sk-d840n-sysupgrade.bin
sysupgrade -T /tmp/immortalwrt-sk-d840n-sysupgrade.bin
```

The SHA256 must match the downloaded checksum, and `sysupgrade -T` must succeed. Stop if either check fails.

Choose **only one** of the following upgrade commands. To keep settings:

```sh
sysupgrade /tmp/immortalwrt-sk-d840n-sysupgrade.bin
```

Or, for a clean configuration:

```sh
sysupgrade -n /tmp/immortalwrt-sk-d840n-sysupgrade.bin
```

An SSH disconnect after starting the upgrade is expected. Reconnect using the retained address/password or the fresh defaults. A backup left only in the device's `/tmp` will be lost on reboot.

<a id="recovery"></a>

## Initial installation and recovery

### Stock firmware or an unknown bootloader

Do not directly apply the sysupgrade procedure above. This repository does not guarantee a no-disassembly installation path for every stock firmware version. First verify the board, U-Boot, NAND/ECC, partitions and device-specific data, and prepare full backups and a verified rescue method.

The [early installation record](docs/05-openwrt-flash-procedure.md) and [flashing notes](docs/06-flash-howto.md) describe specific devices, older images and historical layouts. They contain destructive operations and are not a general tutorial for current firmware. See [U-Boot/FIT compatibility](docs/UBOOT-COMPAT.md) for validation limits.

### RAM trials and recovery images

The initramfs release's `sk-d840n-initramfs.itb` is for a verified U-Boot RAM boot path; `trial-set.tgz` is for a running system with the required kexec support. Neither automatically replaces installed firmware, but the boot path, memory ranges, watchdog handling and rollback method must be verified beforehand. The kexec executable and its dependencies are not included in the release; extracting the trial package alone is insufficient.

The RAM image includes `zrec`. Once booted and confirmed reachable as that RAM system, open `http://192.168.9.1/` for its recovery page. Running in RAM does not itself write flash; flashing actions on the page do. Independently verify the matching maintenance bundle and partitions. Normal flash images exclude `zrec`. See the [trial script](templates/trial-kexec.sh) and [RAM diagnostic script](trial-files/etc/rc.d/S15trial); diagnostic images include automatic reboot logic and are not intended for daily use.

### Devices with resident Bootweb already installed

Only if the device has a matching resident version installed and verified: normal power-on without Reset boots the system. For the accepted native241 version, hold Reset while powering on and keep it held for 5 seconds to enter `http://192.168.9.1/`. Set the computer to `192.168.9.2/24` if needed; do not rely on the recovery page providing DHCP. Upload a matching system package as required by that version's page, and confirm writing only after validation succeeds.

A stock device without resident Bootweb will not gain a recovery page from this button sequence. A working recovery page does not guarantee repair of a damaged U-Boot. BTL rescue and web-based U-Boot self-update have no general acceptance claim; the self-update entry remains locked.

Public Bootweb CI candidates target only the specified stage2 hash of original `U-Boot 2021.01-svn308776`. They are not for devices with another resident version already installed and are not complete boot-partition images. Successful candidate validation does not authorize installation. See the [Bootweb guide](bootweb/README.md), [CI candidate limits](bootweb/docs/CI-RELEASE.md) and [hardware tests and rollback](bootweb/docs/HARDWARE-TESTS-AND-ROLLBACK.md) for requirements.

<a id="build"></a>

## Building your own images

System firmware builds on GitHub Actions Ubuntu runners; Bootweb uses an independent Windows job. You do not need a full local OpenWrt toolchain on Windows.

1. Fork this repository and enable Actions in your fork.
2. Open **Actions → ZX279133 SK-D840N Build → Run workflow** and select your branch.
3. Choose `mode`, adjust the two options below as needed, and run:

| mode | Output |
| --- | --- |
| `all` (default) | initramfs, flash and the independent Bootweb job |
| `both` | initramfs and flash; no Bootweb |
| `initramfs` | RAM image and trial bundle |
| `flash` | System upgrade image and maintenance bundle |
| `bootweb` | Only the offline Bootweb candidate and toolkit |

- `clean_cache` defaults to `false`. Set it to `true` to ignore the `dl/` download cache.
- `publish_release` defaults to `true`. Disable it to skip Release publication; successful jobs still provide Artifacts. It can be disabled for comparative RAM testing.
- Release publication requires repository permission for the workflow to write contents. Check Actions settings and logs if publication fails; a failed packaging step does not provide usable firmware.
- Download from your fork's Releases or that run's Actions Artifacts after success. For failures, use logs for diagnosis, not incomplete images. Pushes affecting certain paths also trigger builds.

The [main workflow](.github/workflows/build-sk-d840n.yml) defines pinned sources and dependencies, overlays, checks and packaging. The external kernel directory still contains `linux-6.18.38` in its name; this is not the final kernel version. Consult the version configuration and release provenance manifest.

<a id="faq"></a>

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Cannot reach `192.168.9.1` | Check cable, computer address and selected port. An upgrade retaining settings may retain a different address. A reassigned WAN port is no longer management LAN; try an unchanged port such as LAN2/LAN3. Check VPN or other-adapter routes overlapping the subnet |
| Upgrade rejects layout or capacity | Stop. Check the live 240/241 MiB layout against the target release; do not force past validation |
| SCP reports a missing SFTP subsystem | Use `scp -O` as shown above; do not disable validation to upload firmware |
| System boots but fiber service does not work | Automatic PON authentication and the complete OMCI path remain unaccepted; saving authentication parameters in a UI does not establish working service |
| Acceleration UI exists but no hardware flows appear | A management interface or successful compilation does not establish offloaded traffic. Check real LAN↔WAN forwarding and retain the unverified status |
| The 2.5G port reports 1G | Check the peer, cable and actual negotiation. A physical 2.5G rating does not establish the current link rate or tested throughput |
| System no longer boots | Use only recovery methods previously verified on this device. Without usable Bootweb, serial or a programmer, do not keep blindly writing partitions |

The included `kmod-mtd-rw` is not auto-loaded. It unlocks read-only partitions including boot and device parameters; loading it is not a normal-upgrade prerequisite. Do not load it to bypass errors, use a module from another kernel or force module loading.

<a id="references"></a>

## Maintenance references and sources

| Reference | Contents |
| --- | --- |
| [ONU management](docs/100-sk-d840n-onu-management-port.md) | UI, identity migration, OMCI and acceptance boundaries |
| [Bootweb](bootweb/README.md) | Recovery program, dependencies, offline tests and device procedures |
| [U-Boot compatibility](docs/UBOOT-COMPAT.md) | FIT validation, session binding and layout restrictions |
| [Test notes](tests/README.md) | Historical local rehearsal suites and fixture requirements; contains old paths and outdated counts, not a complete current CI inventory |
| [Main workflow](.github/workflows/build-sk-d840n.yml) | Current CI test commands, dependencies, build inputs and publication rules |
| [Historical handoff](docs/HANDOFF.md) | An early snapshot; check its status, port conclusions and old commands against later records |

`overlays/` contains OpenWrt packages and board configuration. `kernel-overlays/` and `kernel-patches/` contain kernel support. `scripts/` configures the build tree, while `tools/` contains validation and experimental utilities. Some tools change device state or erase/write NAND; do not run them in bulk as a “self-test.”

Main upstreams and references:

- [cnjn/ImmortalWrt](https://github.com/cnjn/ImmortalWrt): ImmortalWrt base and the ZX279133 build target.
- [cnjn/linux-mainline-zte-zxslc-sr1010](https://github.com/cnjn/linux-mainline-zte-zxslc-sr1010): External ZX279133 kernel and driver base.
- [pbs05/ponwrt](https://github.com/pbs05/ponwrt) and [openwrt-pon-userspace](https://github.com/pbs05/openwrt-pon-userspace): Build-pattern and PON userspace references; drivers for other platforms are not automatically usable on the SK-D840N.
- [huxiangjs/SK-D840N-OpenWRT](https://github.com/huxiangjs/SK-D840N-OpenWRT): Community firmware, bootloader and layout references.

Use each component under its accompanying license and provenance declarations. Full NAND backups, personal credentials, calibration data and device identities are not distributed as public firmware.
