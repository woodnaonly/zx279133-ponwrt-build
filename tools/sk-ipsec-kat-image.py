#!/usr/bin/env python3
"""Configure and gate a manually requested SK-D840N RAM diagnostic image."""

import argparse
import hashlib
import json
import re
import stat
import struct
import zlib
from pathlib import Path

from itbcheck import fdt_props, find_initramfs, kernel_image


CMDLINE = ("console=ttyAMA0,115200n8 rdinit=/bin/sh clk_ignore_unused "
           "pd_ignore_unused=1 loglevel=8 zx279133_ipsec_kat.enable=1")
PROVIDER_CMDLINE = CMDLINE + " zx279133_ipsec_kat.provider=1"
ON = ("CRYPTO_HW", "CRYPTO_DEV_ZX279133_IPSEC_KAT", "CMDLINE_FORCE", "BLK_DEV_INITRD")
OFF = ("KEXEC", "KEXEC_FILE", "KEXEC_CORE", "CRASH_DUMP", "SUSPEND", "HIBERNATION")
DTS_PATH = "arch/arm64/boot/dts/zte/zx279133-sk-d840n.dts"
DTS_ENABLE = ('\n&ipsec { status = "okay"; };\n&ipsec_reserved { status = "okay"; };\n'
              '&spifc { status = "disabled"; };\n')
ARTIFACT = "sk-d840n-73-ipsec-kat-ram-only.itb"
RAM_LOAD = 0x89000000


def set_config(path, prefix="", provider=False):
    settings = {**dict.fromkeys(ON, "y"), **dict.fromkeys(OFF, None),
                "CMDLINE": f'"{PROVIDER_CMDLINE if provider else CMDLINE}"'}
    symbols = {f"CONFIG_{prefix}{name}": value for name, value in settings.items()}
    pattern = re.compile(r"^(?:# )?(CONFIG_[A-Z0-9_]+)(?:=| is not set$)")
    lines = path.read_text(encoding="utf-8").splitlines()
    lines = [line for line in lines if not ((m := pattern.match(line)) and m[1] in symbols)]
    lines.extend(f"{name}={value}" if value is not None else f"# {name} is not set"
                 for name, value in symbols.items())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def prepare(tree, openwrt, provider=False):
    dts = tree / DTS_PATH
    text = dts.read_text(encoding="utf-8")
    if ("ipsec_reserved: buffer@90000000" not in text or
            "memory-region = <&ipsec_reserved>;" not in text or
            not (tree / "drivers/crypto/zx279133-ipsec-kat.c").is_file()):
        raise ValueError("diagnostic requires the prepared 7.3 kernel with its board overlay")
    config = openwrt / ".config"
    if "CONFIG_TARGET_ROOTFS_INITRAMFS=y" not in config.read_text(encoding="utf-8").splitlines():
        raise ValueError("diagnostic requires the resolved OpenWrt initramfs configuration")
    set_config(openwrt / "target/linux/zte/zx279133/config-7.3", provider=provider)
    set_config(config, "KERNEL_", provider=provider)
    if not text.endswith(DTS_ENABLE):
        dts.write_text(text.rstrip() + "\n" + DTS_ENABLE, encoding="utf-8", newline="\n")


def verify_config(config, provider=False):
    lines = set(config.read_text(encoding="utf-8").splitlines())
    cmdline = PROVIDER_CMDLINE if provider else CMDLINE
    required = {f"CONFIG_{name}=y" for name in ON} | {f'CONFIG_CMDLINE="{cmdline}"'}
    if provider:
        required |= {"CONFIG_CRYPTO_LIB_AES=y", "CONFIG_CRYPTO_SKCIPHER=y"}
    if required - lines:
        raise ValueError(f"diagnostic kernel config is missing {sorted(required - lines)}")
    enabled = sorted(line for line in lines if line.split("=", 1)[0] in
                     {f"CONFIG_{name}" for name in OFF} and not line.endswith("=n"))
    if enabled:
        raise ValueError(f"diagnostic kernel has unsafe power/restart config: {enabled}")


def boot_manifest(blob, fit):
    conf = "/configurations/config-1"
    if (fit["/configurations:default"] != b"config-1\0" or
            fit[conf + ":kernel"] != b"kernel-1\0" or fit[conf + ":fdt"] != b"fdt-1\0"):
        raise ValueError("FIT configuration does not match the measured U-Boot bootm flow")
    hashes = []
    for node in ("/images/kernel-1", "/images/fdt-1"):
        data = fit[node + ":data"]
        expected = [(b"crc32\0", struct.pack(">I", zlib.crc32(data))),
                    (b"sha1\0", hashlib.sha1(data).digest())]
        algorithms = [key for key in fit if key.startswith(node + "/") and key.endswith(":algo")]
        if algorithms != [f"{node}/hash-{index}:algo" for index in (1, 2)]:
            raise ValueError(f"unexpected FIT hash nodes for {node}")
        for index, (algorithm, digest) in enumerate(expected, 1):
            child = f"{node}/hash-{index}"
            if (fit[child + ":algo"] != algorithm or fit[child + ":value"] != digest or
                    child + ":ignore" in fit):
                raise ValueError(f"incorrect or disabled FIT hash: {child}")
            hashes.append(dict(node=node, algorithm=algorithm[:-1].decode("ascii"), digest=digest.hex()))
    return dict(bytes=len(blob), crc32=f"{zlib.crc32(blob):08x}",
                sha256=hashlib.sha256(blob).hexdigest(), hashes=hashes)


def verify_wiring(dt, engine):
    reg = struct.pack(">8I", 0, 0x12000000, 0, 0x1000000,
                      0, 0x13000000, 0, 0x1000000)
    if dt.get(engine + ":reg") != reg:
        raise ValueError("unexpected IPsec engine/TRNG register windows")
    irqs = b"".join(struct.pack(">3I", 0, spi, 4) for spi in range(174, 185))
    if dt.get(engine + ":interrupts") != irqs or engine + ":interrupts-extended" in dt:
        raise ValueError("unexpected IPsec GIC SPI cells")
    gic = "/interrupt-controller@500000"
    parent = engine
    while parent != "/" and parent + ":interrupt-parent" not in dt:
        parent = parent.rsplit("/", 1)[0] or "/"
    if (dt.get(gic + ":compatible") != b"arm,gic-v3\0" or
            dt.get(gic + ":#interrupt-cells") != struct.pack(">I", 3) or
            not dt.get(gic + ":phandle") or
            dt.get(parent + ":interrupt-parent") != dt[gic + ":phandle"]):
        raise ValueError("IPsec interrupts do not resolve to the board GIC")
    topcrm, = [key.rsplit(":", 1)[0] for key, val in dt.items()
               if key.endswith(":compatible") and b"zte,zx279133-topcrm" in val.split(b"\0")]
    clock = dt[topcrm + ":phandle"]
    if (len(clock) != 4 or dt.get(topcrm + ":#clock-cells") != struct.pack(">I", 1) or
            dt.get(engine + ":clocks") != b"".join(clock + struct.pack(">I", i) for i in range(28, 32)) or
            dt.get(engine + ":clock-names") != b"wclk\0aclk\0sec_hclk\0sec_wclk\0"):
        raise ValueError("unexpected IPsec TOPCRM clock references")
    reset = "/soc/reset-controller@10e10060"
    phandle = dt.get(reset + ":phandle", b"")
    if (dt.get(reset + ":compatible") != b"zte,zx296718-reset\0" or
            dt.get(reset + ":reg") != struct.pack(">4I", 0, 0x10e10060, 0, 4) or
            dt.get(reset + ":#reset-cells") != struct.pack(">I", 1) or len(phandle) != 4 or
            dt.get(engine + ":resets") != phandle + struct.pack(">I", 13) or
            dt.get(engine + ":reset-names") != b"ipsec\0"):
        raise ValueError("unexpected IPsec TOPCRM reset reference")


def verify_load_range(image, load, entry, fit_size):
    if len(image) < 64 or image[56:60] != b"ARM\x64":
        raise ValueError("diagnostic kernel has no ARM64 Image header")
    text_offset, image_size = struct.unpack_from("<QQ", image, 8)
    base = load - text_offset
    if not image_size or entry != load or base < 0x80000000 or base % 0x200000:
        raise ValueError("invalid ARM64 Image size, load alignment or entry point")
    if load + max(len(image), image_size) > RAM_LOAD or RAM_LOAD + fit_size > 0x90000000:
        raise ValueError("FIT staging or ARM64 Image load range overlaps another RAM region")


def verify_fit(blob, provider=False):
    fit = fdt_props(blob)
    manifest = boot_manifest(blob, fit)
    fdt_node = "/images/fdt-1"
    kernel_node = "/images/kernel-1"
    chosen_kernel, image = kernel_image(blob)
    cmdline = PROVIDER_CMDLINE if provider else CMDLINE
    if (chosen_kernel != kernel_node + ":data" or cmdline.encode("ascii") + b"\0" not in image or
            b"AES-128 ECB encrypt/decrypt KAT passed" not in image):
        raise ValueError("default FIT configuration does not contain the diagnostic kernel")
    if provider and b"IRQ KAT passed; registered ecb-aes-zx279133" not in image:
        raise ValueError("default FIT configuration does not contain the ECB provider")
    load, = struct.unpack(">I", fit[kernel_node + ":load"])
    entry, = struct.unpack(">I", fit[kernel_node + ":entry"])
    verify_load_range(image, load, entry, len(blob))
    archive = find_initramfs(image)
    entries = {name.removeprefix("./"): (mode, data) for name, mode, data in archive[1]} if archive else {}
    mode, data = entries.get("bin/sh", (0, b""))
    if stat.S_ISLNK(mode) and data.rstrip(b"\0") in (b"busybox", b"/bin/busybox"):
        mode, _ = entries.get("bin/busybox", (0, b""))
    if not stat.S_ISREG(mode) or not mode & 0o111:
        raise ValueError("diagnostic kernel has no executable /bin/sh in its initramfs")
    dt = fdt_props(fit[fdt_node + ":data"])
    if b"skyworth,sk-d840n" not in dt["/:compatible"].split(b"\0"):
        raise ValueError("default FIT configuration does not target SK-D840N")
    engine, = [key.rsplit(":", 1)[0] for key, val in dt.items()
               if key.endswith(":compatible") and val == b"zte,zx279133-ipsec\0"]
    verify_wiring(dt, engine)
    flash, = [key.rsplit(":", 1)[0] for key, val in dt.items()
              if key.endswith(":compatible") and val == b"zte,zx279133-sfc\0"]
    if dt.get(flash + ":status") != b"disabled\0":
        raise ValueError("diagnostic FIT must not expose the board's SPI NAND controller")
    memory = "/reserved-memory/buffer@90000000"
    if (dt.get(engine + ":status") != b"okay\0" or dt.get(memory + ":status") != b"okay\0" or
            dt.get(memory + ":no-map") != b"" or memory + ":reusable" in dt or
            dt.get(memory + ":reg") != struct.pack(">4I", 0, 0x90000000, 0, 0x200000) or
            dt[engine + ":memory-region"] != dt[memory + ":phandle"]):
        raise ValueError("FIT DTB does not enable the engine with its dedicated no-map reservation")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    prep = modes.add_parser("prepare")
    prep.add_argument("tree", type=Path)
    prep.add_argument("openwrt", type=Path)
    prep.add_argument("--provider", action="store_true")
    gate = modes.add_parser("gate")
    gate.add_argument("config", type=Path)
    gate.add_argument("fit", type=Path)
    gate.add_argument("out", type=Path)
    gate.add_argument("--provider", action="store_true")
    args = parser.parse_args()
    try:
        if args.mode == "prepare":
            prepare(args.tree, args.openwrt, args.provider)
        else:
            verify_config(args.config, args.provider)
            blob = args.fit.read_bytes()
            manifest = verify_fit(blob, args.provider)
            args.out.mkdir()
            (args.out / ARTIFACT).write_bytes(blob)
            checksum = manifest["sha256"]
            (args.out / "sha256sums").write_text(f"{checksum}  {ARTIFACT}\n", encoding="ascii")
            (args.out / "boot-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                                          encoding="ascii", newline="\n")
            print(f"Verified RAM-only diagnostic FIT: {checksum}")
    except (OSError, ValueError, KeyError, struct.error) as exc:
        parser.exit(1, f"IPsec KAT image: {exc}\n")


if __name__ == "__main__":
    main()
