# SK-D840N ImmortalWrt Firmware

[简体中文](README.md) | English

ImmortalWrt firmware for the Skyworth SK-D840N (ZX279133), with RAM recovery images and a separate Bootweb recovery project. This repository contains board support, driver overlays, build scripts and tests. GitHub Actions fetches pinned upstream sources to compile the images; this is not a complete OpenWrt source tree where you can run `make` directly.

The repository has two build lines: the Linux 7.3.4 line is the release line, and the Linux 6.18.60 line only builds and uploads its artifacts. Both lines use the same userspace files, so both ship the second WAN on a panel jack and the "WAN port" page.

[Download firmware](https://github.com/woodnaonly/zx279133-ponwrt-build/releases) · [Build runs](https://github.com/woodnaonly/zx279133-ponwrt-build/actions/workflows/build-sk-d840n-73.yml)

> Before flashing, back up configuration and device-specific data, verify the model, bootloader and partition layout, and disable scheduled power cuts such as smart-plug timers. Stop if validation fails; do not force an upgrade. A normal system upgrade neither installs Bootweb nor migrates the partition layout.

## Contents

- [Project and support status](#status)
- [The two build lines](#lines)
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

The release line is based on ImmortalWrt 25.12.1, pinned to base commit `1b50aaf4`. The 7.3 line's kernel is vanilla linux-7.3.4 with the ZX279133 driver patch series from `kernel-patches/7.3/`; the 6.18 line is 6.18.38 with official stable patches through Linux **6.18.60**. See the [kernel version configuration](configs/sk-d840n-stable.json) and the [7.3 version configuration](configs/sk-d840n-73.json) for versions and patch checksums, and each release's `kernel-provenance.json` and `kernel-provenance-73.json` for its exact sources.

| Component | Current status and limits |
| --- | --- |
| Boot, system installation and management UI | Records document installation of build #337, normal cold boot, default LAN and LuCI access on the test device on 2026-11-05. On 2026-11-25 and 26 the 7.3.4 image was installed on the same device, its kernel, dtb and rootfs were compared byte for byte with the artifacts of that build, and three board measurements were recorded: LAN egress, transport VID 63 and the TCP checksum ([docs/103](docs/103-the-lan-egress-verified-on-the-board-20261125.md), [docs/105](docs/105-the-transport-vid-63-and-the-dead-egress-20261125.md), [docs/106](docs/106-the-tcp-checksum-is-half-there-20261126.md)). This does not validate every later build on hardware |
| A panel jack as the second WAN | The "WAN port" page and its three programs are in the images of both lines, and on 2026-11-26 they matched the #94 artifacts byte for byte on the test device. The adaptive port search and the failover between the two WANs have not been exercised on the device, and the adaptive daemon is not enabled by default; see [A panel jack as the second WAN](#wan2) |
| Partition layout | Current images use `native241`: a 241 MiB root, no separate scratch/recovery partition; root is `mtd5` and the kernel partition is 8 MiB |
| Ethernet | Code supports the four panel LAN ports; LAN1 is the physical 2.5G port. All four panel ports can be chosen as the second WAN, but rated speed or CI success does not establish negotiated speed, 2.5G throughput, cold-boot behavior or long-term stability |
| LuCI management | Includes Argon, ONU management pages and firewall4; the "WAN port" page is a separate page in the ONU menu, and having a UI does not establish working line service |
| PON / OMCI | Driver and management interfaces and an OMCI engine are integrated, but automatic optical activation, RX demultiplexing and the OMCI integrity-key lifecycle remain incomplete; this is not firmware with completed ISP registration support |
| Hardware acceleration | Build checks cover kernel/tool availability; hardware offload of real traffic has not completed acceptance testing. The ONU acceleration page is read-only; offload options on the native firewall page require careful operator testing |
| Bootweb | A specific test device has resident installation and physical Reset re-entry records; public CI packages remain offline candidate prereleases, not universally flashable U-Boot images |

See the [#337 acceptance report](bootweb/reports/2026-11-05-native241-337-acceptance.json) and [ONU management notes](docs/100-sk-d840n-onu-management-port.md) for evidence and PON limitations. Passing CI confirms that build and the checks it ran, not optical registration, unbricking or hardware performance.

<a id="lines"></a>

## The two build lines

| Line | Workflow | Kernel | Publication | Outputs |
| --- | --- | --- | --- | --- |
| 7.3 line (the release line) | `ZX279133 SK-D840N Build (Linux 7.3 line)`, `build-sk-d840n-73.yml` | vanilla linux-7.3.4 + `kernel-patches/7.3/` | Publishes Releases tagged `skd840n_73_flash_<run_number>_<run_id>` and `skd840n_73_initramfs_<run_number>_<run_id>` | Actions artifacts `sk-d840n-73-images-flash`, `sk-d840n-73-images-initramfs`, and `sk-d840n-73-logs` on failure |
| 6.18 line | `ZX279133 SK-D840N Build`, `build-sk-d840n.yml` | 6.18.38 with official stable patches through 6.18.60 | No longer publishes; the 6.18 releases still on the Releases page are the last set this line published and are kept as an archive | Actions artifacts `sk-d840n-flash`, `sk-d840n-sysupgrade`, `sk-d840n-initramfs`, `sk-d840n-bootweb-native241`, and `logs-<mode>` on failure |

Things that are easy to get wrong:

- The two lines count their runs separately, so the same number in each line is a different build. That is why the tag prefixes differ: the old `skd840n_…` is the 6.18 line, the new `skd840n_73_…` is the 7.3 line.
- Both lines share `overlays/` (userspace, including the second-WAN feature); the 7.3 line adds `overlays-73/` and `kernel-overlays-73/`. A change under `overlays/` changes the image of both lines.
- The image gate of the 7.3 line is `tools/check-sk-image-drivers.py`: it probes every driver the board needs inside the image it just built and stops if one is missing.
- The 7.3 line has been installed on the test device and compared byte for byte with its artifacts; see [docs/103](docs/103-the-lan-egress-verified-on-the-board-20261125.md), [docs/105](docs/105-the-transport-vid-63-and-the-dead-egress-20261125.md), [docs/106](docs/106-the-tcp-checksum-is-half-there-20261126.md) and [docs/107](docs/107-the-adaptive-wan-and-the-second-wan-20261126.md).
- The 6.18 line still builds and still produces artifacts, but it no longer publishes Releases. To get 6.18 firmware take it from that line's Actions artifacts, and remember that those artifacts predate the 7.3 line.

<a id="download"></a>

## Download and verification

### Choose the correct file

Firmware now comes from two places: the Releases page carries what the 7.3 line published (one tag per mode), and Actions Artifacts carry the build outputs of both lines (in a ZIP wrapper, with a limited retention period). Do not simply download the newest entry: flash, initramfs and Bootweb are three different products.

7.3 line release (tag `skd840n_73_flash_<run_number>_<run_id>`):

| File | Purpose | Upload directly to LuCI system upgrade? |
| --- | --- | --- |
| `immortalwrt-sk-d840n-sysupgrade.bin` | Preferred upgrade file for an already compatible system | Yes; use the file as downloaded, without unpacking |
| `flash-sysupgrade.bin` | Byte-identical compatibility filename for the preferred upgrade file | Yes, under the same compatibility conditions |
| `flash-uImage`, `flash-dtb.bin`, `flash-rootfs.jffs2` | Partition-level maintenance images; all must come from the same build | No |
| `sha256sums` | SHA256 for the files above | No |
| `sysupgrade-report.json` | The upgrade package's own validation report (fwtool metadata, CRC, board match, member sizes, JFFS2 magic, and the inflated uImage compared byte for byte with the built Image) | No |

7.3 line release (tag `skd840n_73_initramfs_<run_number>_<run_id>`):

| File | Purpose | Upload directly to LuCI system upgrade? |
| --- | --- | --- |
| `immortalwrt-zte-zx279133-skyworth_sk-d840n-initramfs.itb` | RAM trial/recovery image | No |
| `immortalwrt-zte-zx279133-rootfs.cpio.gz` | The same rootfs in cpio form, for offline inspection | No |
| `sha256sums` | SHA256 for the files above | No |

Actions artifacts (both lines: `sk-d840n-73-images-flash` and `sk-d840n-73-images-initramfs` for the 7.3 line, `sk-d840n-flash`, `sk-d840n-sysupgrade`, `sk-d840n-initramfs` and `sk-d840n-bootweb-native241` for the 6.18 line):

| Contents | Notes |
| --- | --- |
| The same firmware files as in the Release, plus `packages/`, `*.manifest`, `profiles.json`, `version.buildinfo` | What the 7.3 line's artifact carries is described in the migration surface of [docs/101](docs/101-the-7-3-4-baseline-and-the-migration-surface-20261122.md) |
| `flash-set.tgz`, `sk-d840n-initramfs.itb`, `trial-set.tgz` | The 6.18 line's maintenance and trial bundles |
| `sk-d840n-bootweb-native241-candidate.tar`, `sk-d840n-bootweb-native241-tools.tgz`, `sha256sums-bootweb` | The offline Bootweb candidate and its tools |

The rules:

- Release Assets are direct file downloads. Actions Artifacts arrive inside a ZIP wrapper that must be extracted first.
- The `.bin` internally uses the sysupgrade tar format as part of the firmware protocol; do not unpack it again.
- **The 7.3 line's Actions artifacts are kept for 14 days** and then they are gone. Download them in time and check the `sha256sums` inside the archive yourself.
- The last 6.18 releases are still on the Releases page and are kept as an archive; they are not the current release line.

### Verify SHA256 on Windows

The 7.3 line release ships one `sha256sums` file. Save the firmware files and it into one folder, then open PowerShell in that folder:

```powershell
Get-Content .\sha256sums | ForEach-Object {
    $parts = $_ -split '\s+', 2
    $want = $parts[0].Trim().ToLower()
    $name = $parts[1].Trim()
    $got = (Get-FileHash -LiteralPath $name -Algorithm SHA256).Hash.ToLower()
    if ($want -notmatch '^[0-9a-f]{64}$' -or $got -ne $want) { throw "SHA256 mismatch: $name" }
    "ok  $name"
}
```

The 6.18 line's `sk-d840n-sysupgrade` artifact also carries a `.sha256` file, which checks that single file:

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

| Panel connection | System device | Default role | Can be the second WAN |
| --- | --- | --- | --- |
| LAN1 (physical 2.5G port) | `lan1` | LAN member of `br-lan` | Yes |
| LAN2 / LAN3 / LAN4 | `lan2` / `lan3` / `lan4` | LAN members of `br-lan` | Yes |
| PON-side MAC | `eth0` | Target of the default WAN configuration; not a panel RJ45 port | No: the optical port is always WAN1 |
| Internal interfaces | `lan0`, `lan-cpu0` | Chip-internal port and CPU conduit, not jacks | No |

All four RJ45 ports default to LAN. To assign one as WAN, **use the [A panel jack as the second WAN](#wan2) page below**; it takes the port out of the bridge, reads the committed file back to confirm the change really landed, and puts the old state back if the change turns out wrong. The manual method is only needed when you do not use the page.

To assign a port as WAN by hand, using LAN4 as an example:

1. Connect the management computer to LAN2 or LAN3, keeping a management port outside the change.
2. Under **Network → Interfaces → Devices**, edit `br-lan` and remove `lan4` from its bridge ports. Keep the remaining LAN and internal interfaces.
3. Edit WAN, select `lan4` as its device, and choose DHCP client, PPPoE or a static address as required by the uplink. Check the WAN firewall zone and LAN-to-WAN forwarding. If using IPv6, also check the WAN6 binding.
4. Save and apply. Verify management still works through a remaining LAN port, then connect the uplink to LAN4.

LAN4 is no longer a management LAN port. Do not assign the same port to both `br-lan` and WAN. The ONU Internet and IPTV/multicast pages edit existing WAN/bridge configuration; they do not automatically create a complete network topology.

<a id="wan2"></a>

### A panel jack as the second WAN

The optical port (`eth0`, PON side) is always WAN1; lan1..lan4 are the panel ports that can be switched. The page lives in **Administration → ONU → WAN port** (route `admin/onu/wanport`).

What the page offers:

| Control | Meaning |
| --- | --- |
| WAN2 device | LAN1–LAN4, or "not used (optical port only)" |
| Protocol | DHCP / IPoE, PPPoE, static address |
| Primary | Optical port (WAN1) first, or panel jack (WAN2) first |
| Adaptive | On: the board finds the panel jack that has an uplink by itself |
| Excluded ports | Ports that must never be taken, for example one with a machine of its own behind it running a DHCP server |
| Status card | Device, protocol and link state of WAN1/WAN2, the current primary, the adaptive switch and the excluded ports, the ports inside the bridge, the NPPT port types, and the management port |

Rules worth knowing before you change anything:

- **Switching a port is a transaction.** After the change is committed, the board reads the committed file back to confirm it really landed, then starts a 90-second watchdog; if nobody confirms within that window, the old state comes back on its own. Pressing "keep it" on the page cancels the rollback.
- **The port type follows the protocol.** A PPPoE WAN port is turned into a WAN-type port in the NPPT (the hardware handles the PPPoE header); a DHCP or static WAN port stays LAN-type (ordinary Ethernet frames). The driver's `wan_type` parameter is written to 0 explicitly, so only userspace changes the port type.
- **The adaptive mode probes with DHCP.** The port leaves the bridge for a moment and sends one DHCP request; whoever answers becomes the second WAN, and a port nobody answers is put back. The management port and the ports you excluded are never taken. The board retries in the background, so a cable plugged in later is still found.
- **The two WANs fail over to each other; mwan3 is not used.** When the primary stops working the other one takes over, and when the primary comes back the traffic returns to it (with hysteresis: three good rounds and three bad rounds before it moves).
- The settings live in `/etc/config/sk_wan` (`auto`, `primary`, `exclude`). The `network.wan2` interface, the `firewall.wan2` zone and the `firewall.lan_wan2` forwarding are created by the first-boot script; `network.wan.metric` is set to 10 and `network.wan2.metric` to 20, so the optical port is preferred while both WANs are up.

**The adaptive mode needs a daemon that is running**, and the image does not ship its boot link by default. Enable it before using the adaptive mode for the first time:

```sh
/etc/init.d/sk-wan-adapt enable
/etc/init.d/sk-wan-adapt start
```

Without it the page still works: choosing a panel jack as WAN2 by hand works just as well, there is only no automatic port search and no failover.

<a id="upgrade"></a>

## System upgrades

These steps apply only to devices already running a compatible system from this project, with a bootloader and partition layout matching the target image. They are not a guaranteed direct upgrade path from stock or other community firmware.

### Checks and backups before upgrading

- Disable all scheduled power cuts and maintain continuous power. Have a recovery method appropriate to this device ready. Writes run root → dtb → kernel; power loss or a bad kernel can still leave the system unbootable.
- The root partition is a 241 MiB one-way partition with no A/B: a bad write has no way back except reflashing or a programmer. Get the configuration backup onto the computer before upgrading.
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

Public Bootweb CI candidates target only the specified stage2 hash of original `U-Boot 2021.01-svn308776`. They are not for devices with another resident version already installed and are not complete boot-partition images. The candidates are now distributed only through the 6.18 line's Actions artifact (`sk-d840n-bootweb-native241`) and no longer have a Release of their own; successful candidate validation does not authorize installation. See the [Bootweb guide](bootweb/README.md), [CI candidate limits](bootweb/docs/CI-RELEASE.md) and [hardware tests and rollback](bootweb/docs/HARDWARE-TESTS-AND-ROLLBACK.md) for requirements.

<a id="build"></a>

## Building your own images

System firmware builds on GitHub Actions Ubuntu runners; Bootweb uses an independent Windows job. You do not need a full local OpenWrt toolchain on Windows.

1. Fork this repository and enable Actions in your fork.
2. Open **Actions → ZX279133 SK-D840N Build (Linux 7.3 line) → Run workflow** (the 7.3 line) or **Actions → ZX279133 SK-D840N Build → Run workflow** (the 6.18 line), and select the branch to build.
3. Choose `mode`, adjust the options below as needed, and run:

7.3 line:

| mode | Output |
| --- | --- |
| `initramfs` (default) | RAM image; publishes the release `skd840n_73_initramfs_<run_number>_<run_id>` and uploads the artifact `sk-d840n-73-images-initramfs` |
| `flash` | System upgrade file and partition images; publishes the release `skd840n_73_flash_<run_number>_<run_id>` and uploads the artifact `sk-d840n-73-images-flash` |

- `clean_cache` defaults to `false`. Set it to `true` to ignore the `dl/` download cache.
- `publish_release` defaults to `true`. Disable it to skip Release publication; successful jobs still provide Artifacts. It can be disabled for comparative RAM testing.
- The 7.3 line's artifacts are kept for 14 days.

6.18 line:

| mode | Output |
| --- | --- |
| `all` (default) | initramfs, flash and the independent Bootweb job |
| `both` | initramfs and flash; no Bootweb |
| `initramfs` | RAM image and trial bundle |
| `flash` | System upgrade image and maintenance bundle |
| `bootweb` | Only the offline Bootweb candidate and toolkit |

- `clean_cache` defaults to `false`. Set it to `true` to ignore the `dl/` download cache.
- This line only builds and uploads artifacts; it does not publish Releases.

Notes that apply to both:

- Release publication requires repository permission for the workflow to write contents. Check Actions settings and logs if publication fails; a failed packaging step does not provide usable firmware.
- Download from your fork's Releases or that run's Actions Artifacts after success. For failures, use logs for diagnosis, not incomplete images. Pushes affecting certain paths also trigger builds; a change to a `.md` file such as this README triggers a full 7.3 line build.
- The two lines count their runs separately and their tag prefixes differ (`skd840n_73_…` and the old `skd840n_…`), so do not mix the run numbers of the two lines.

The [7.3 line workflow](.github/workflows/build-sk-d840n-73.yml) and the [main workflow](.github/workflows/build-sk-d840n.yml) define pinned sources and dependencies, overlays, checks and packaging. The external kernel directory still contains `linux-6.18.38` in its name; this is not the final kernel version. Consult the version configuration and release provenance manifest.

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
| The "WAN port" page is missing | It is a separate page in the ONU menu (`admin/onu/wanport`); check that the image is from this repository. `uci show sk_wan` shows the configuration itself |
| The adaptive mode picks no port | Nothing answered DHCP on that port, or it is in the excluded list, or it is the management port you are connected through. Start the daemon first with `/etc/init.d/sk-wan-adapt enable && /etc/init.d/sk-wan-adapt start` |
| A port is LAN again after an upgrade | The root and the configuration did not survive; go back through [System upgrades](#upgrade) and check the backup and the partitions. Do not keep reflashing root: it is a one-way partition |

The included `kmod-mtd-rw` is not auto-loaded. It unlocks read-only partitions including boot and device parameters; loading it is not a normal-upgrade prerequisite. Do not load it to bypass errors, use a module from another kernel or force module loading.

<a id="references"></a>

## Maintenance references and sources

| Reference | Contents |
| --- | --- |
| [ONU management](docs/100-sk-d840n-onu-management-port.md) | UI, identity migration, OMCI and acceptance boundaries |
| [The 7.3.4 baseline and the migration surface](docs/101-the-7-3-4-baseline-and-the-migration-surface-20261122.md) | Where the 7.3 line starts, how it differs from 6.18, and what a migration touches |
| [The LAN egress verified on the board](docs/103-the-lan-egress-verified-on-the-board-20261125.md) | The LAN egress conclusion measured on the board |
| [Transport VID 63](docs/105-the-transport-vid-63-and-the-dead-egress-20261125.md) | The transport VID fix for the 2.5G port |
| [The TCP checksum](docs/106-the-tcp-checksum-is-half-there-20261126.md) | Why the outbound TCP checksum has to be computed in software |
| [The adaptive WAN and the second WAN](docs/107-the-adaptive-wan-and-the-second-wan-20261126.md) | The adaptive port search, the second WAN, the port types and the page |
| [The LuCI module has to be a class](docs/108-the-luci-module-has-to-be-a-class-20261126.md) | The loading contract of a page module |
| [Bootweb](bootweb/README.md) | Recovery program, dependencies, offline tests and device procedures |
| [U-Boot compatibility](docs/UBOOT-COMPAT.md) | FIT validation, session binding and layout restrictions |
| [Test notes](tests/README.md) | Historical local rehearsal suites and fixture requirements; contains old paths and outdated counts, not a complete current CI inventory |
| [The 7.3 line workflow](.github/workflows/build-sk-d840n-73.yml) | The steps, gates and publication rules of the current release line |
| [The main workflow](.github/workflows/build-sk-d840n.yml) | The build steps and checks of the 6.18 line |
| [Historical handoff](docs/HANDOFF.md) | An early snapshot; check its status, port conclusions and old commands against later records |

`overlays/` contains OpenWrt packages and board configuration (including the second-WAN feature), `kernel-overlays/` and `kernel-patches/` contain kernel support, `overlays-73/` and `kernel-overlays-73/` are what the 7.3 line adds on top, `scripts/` configures the build tree, while `tools/` contains validation and experimental utilities. Some tools change device state or erase/write NAND; do not run them in bulk as a "self-test."

Main upstreams and references:

- [cnjn/ImmortalWrt](https://github.com/cnjn/ImmortalWrt): ImmortalWrt base and the ZX279133 build target.
- [cnjn/linux-mainline-zte-zxslc-sr1010](https://github.com/cnjn/linux-mainline-zte-zxslc-sr1010): External ZX279133 kernel and driver base.
- [pbs05/ponwrt](https://github.com/pbs05/ponwrt) and [openwrt-pon-userspace](https://github.com/pbs05/openwrt-pon-userspace): Build-pattern and PON userspace references; drivers for other platforms are not automatically usable on the SK-D840N.
- [huxiangjs/SK-D840N-OpenWRT](https://github.com/huxiangjs/SK-D840N-OpenWRT): Community firmware, bootloader and layout references.

Use each component under its accompanying license and provenance declarations. Full NAND backups, personal credentials, calibration data and device identities are not distributed as public firmware.
