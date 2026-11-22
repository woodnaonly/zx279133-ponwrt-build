#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Prepare the Linux 7.3 tree this board builds against.

The 6.18 line got its driver from cnjn's kernel fork, so the tree was a
submodule and the only thing to do was to move it forward with official
stable patches.  The 7.3 line is different: cnjn ships the ZX279133 NPU
driver as a patch series (target/linux/zte/patches-7.3) that is applied to a
VANILLA kernel, and OpenWrt's own zte target refuses to build it from an
external tree.  So this tool does the whole preparation itself:

  1. fetch linux-7.3.4.tar.xz and refuse it unless its sha256 matches the
     pinned value (the mirrors are interchangeable; the hash is not),
  2. apply the vendored patch series, one patch at a time, with
     `git apply --check` first and no fuzz, no rejects and no whole-file
     overlays,
  3. rewrite OpenWrt's kernel metadata so it names 7.3.4 and the tarball's
     hash, instead of the 7.3.1 its tree was pinned to.

The result is the tree the build hangs on: `CONFIG_EXTERNAL_KERNEL_TREE`
points at it, and OpenWrt symlinks it as its linux directory without
touching it.  Every step is recorded in the provenance file.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "configs/sk-d840n-73.json"
MARKER_NAME = ".sk-series-applied.json"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_lock(path=LOCK):
    lock = json.loads(path.read_text(encoding="utf-8"))
    require(re.fullmatch(r"\d+\.\d+\.\d+", lock["version"]), "invalid kernel version")
    require(re.fullmatch(r"[0-9a-f]{64}", lock["source_tar_sha256"]), "invalid tarball SHA256")
    require(lock["mirrors"], "no mirror configured")
    manifest_path = ROOT / lock["manifest"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest["upstream_commit"] == lock["upstream"]["commit"],
            "manifest and lock disagree about the upstream commit")
    require(manifest["kernel_patchver"] == lock["line"],
            "manifest and lock disagree about the patch series")
    patches = []
    for item in manifest["patches"]:
        name = item["file"]
        require(name.endswith(".patch") and "/" not in name and ".." not in name,
                f"unsafe patch name: {name}")
        payload = (ROOT / lock["patches_dir"] / name).read_bytes()
        require(len(payload) == item["bytes"], f"patch size changed: {name}")
        require(hashlib.sha256(payload).hexdigest() == item["sha256"],
                f"patch SHA256 changed: {name}")
        patches.append((name, payload))
    require(patches, "empty patch series")
    lock["patches"] = patches
    lock["manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    # Our own patches, applied after the vendored series.  They are optional:
    # a tree that only carries cnjn's series is still a tree this lock
    # describes, and the difference is recorded in the provenance file.
    local = []
    local_manifest_path = ROOT / lock["local_manifest"]
    if local_manifest_path.exists():
        local_manifest = json.loads(local_manifest_path.read_text(encoding="utf-8"))
        require(local_manifest["base"]["version"] == lock["version"],
                "the local series targets a different kernel version")
        require(local_manifest["base"]["source_tar_sha256"] == lock["source_tar_sha256"],
                "the local series targets a different base tarball")
        for item in local_manifest["patches"]:
            name = item["file"]
            require(name.endswith(".patch") and "/" not in name and ".." not in name,
                    f"unsafe patch name: {name}")
            payload = (ROOT / lock["local_patches_dir"] / name).read_bytes()
            require(len(payload) == item["bytes"], f"local patch size changed: {name}")
            require(hashlib.sha256(payload).hexdigest() == item["sha256"],
                    f"local patch SHA256 changed: {name}")
            local.append((name, payload))
        lock["local_manifest_sha256"] = hashlib.sha256(
            local_manifest_path.read_bytes()).hexdigest()
    lock["local_patches"] = local
    return lock


def kernel_version(tree):
    source = (tree / "Makefile").read_text(encoding="utf-8")
    values = []
    for key in ("VERSION", "PATCHLEVEL", "SUBLEVEL", "EXTRAVERSION"):
        hits = re.findall(r"^" + key + r"[ \t]*=[ \t]*([^\n]*)", source, re.M)
        require(len(hits) == 1, f"ambiguous kernel {key}")
        values.append(hits[0].strip())
    return ".".join(values[:3]) + values[3]


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_tarball(lock, cache):
    """Download the pinned tarball, or return the verified cached copy."""
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / lock["source_tar"]
    if destination.exists() and destination.stat().st_size == lock["source_tar_bytes"]:
        digest = sha256_file(destination)
        require(digest == lock["source_tar_sha256"], "cached tarball SHA256 mismatch")
        print(f"Verified cached {lock['source_tar']}", flush=True)
        return destination
    last = None
    for base in lock["mirrors"]:
        url = base.rstrip("/") + "/" + lock["source_tar"]
        for attempt in range(3):
            try:
                print(f"Fetching {url}", flush=True)
                with urllib.request.urlopen(url, timeout=180) as response:
                    data = response.read()
                break
            except OSError as exc:
                last = exc
                data = None
                time.sleep(3)
        if data is None:
            continue
        require(len(data) == lock["source_tar_bytes"],
                f"downloaded size differs from the lock: {url}")
        require(hashlib.sha256(data).hexdigest() == lock["source_tar_sha256"],
                f"downloaded SHA256 differs from the lock: {url}")
        destination.write_bytes(data)
        print(f"Verified {lock['source_tar']}: {len(data)} bytes from {url}", flush=True)
        return destination
    raise ValueError(f"no mirror delivered the pinned tarball: {last}")


def extract(tarball, tree, version):
    """Extract the tarball so that <tree>/Makefile is the kernel's."""
    tree = tree.resolve()
    if (tree / "Makefile").exists():
        require(kernel_version(tree) == version, f"existing tree is not Linux {version}")
        print(f"Reusing existing tree {tree}", flush=True)
        return tree
    tree.parent.mkdir(parents=True, exist_ok=True)
    staging = tree.parent / (tree.name + ".unpack")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    print(f"Extracting {tarball.name}", flush=True)
    with tarfile.open(tarball, "r:xz") as archive:
        for member in archive.getmembers():
            name = PurePosixPath(member.name)
            require(not name.is_absolute() and ".." not in name.parts,
                    f"unsafe path in tarball: {member.name}")
        archive.extractall(staging)
    source = staging / ("linux-" + version)
    require(source.is_dir(), f"tarball did not contain linux-{version}/")
    shutil.move(str(source), str(tree))
    shutil.rmtree(staging, ignore_errors=True)
    require((tree / "Makefile").exists(), "extracted tree has no Makefile")
    return tree


def apply_series(tree, series, marker_payload):
    """Apply the series, one patch at a time, refusing anything that does not
    apply cleanly.  A partially applied driver is worse than no driver: the
    tree would still compile and the board would still boot.

    Idempotency is decided by a marker file written after a complete run, not
    by asking git whether each patch reverses: two patches in this series
    touch the same file (450 and 460 both edit nf_flow_table_offload.c), so
    the later one stops the earlier one from reversing cleanly and the tree
    would look half-applied when it is complete."""
    marker = tree / MARKER_NAME
    if marker.exists():
        try:
            state = json.loads(marker.read_text(encoding="utf-8"))
        except ValueError:
            state = {}
        require(state.get("manifest_sha256") == marker_payload["manifest_sha256"],
                "the tree carries a series marker from a different manifest")
        require(state.get("local_manifest_sha256") == marker_payload["local_manifest_sha256"],
                "the tree carries a series marker from a different local manifest")
        require(state.get("version") == marker_payload["version"],
                "the tree carries a series marker for a different kernel version")
        print(f"Series already applied in {tree} (marker {MARKER_NAME})", flush=True)
        return [], [name for _, name, _ in series]

    # A tree that already carries the driver but no marker was prepared by
    # something else.  Applying the series on top of it would half-work, so
    # refuse instead.
    driver = tree / "drivers/net/ethernet/zte/zx279133.h"
    if driver.exists():
        raise ValueError(
            f"{tree} already carries the ZX279133 driver but no {MARKER_NAME}; "
            "it was prepared by another tool - refusing to apply the series twice")

    applied = []
    for origin, name, payload in series:
        path = tree / ".sk-series" / origin / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        check = subprocess.run(
            ["git", "-c", "core.autocrlf=false", "-C", str(tree), "apply",
             "--check", "--whitespace=nowarn", str(path)],
            capture_output=True, text=True)
        if check.returncode != 0:
            raise ValueError(f"{origin}/{name} does not apply cleanly:\n{check.stderr.strip()}")
        run = subprocess.run(
            ["git", "-c", "core.autocrlf=false", "-C", str(tree), "apply",
             "--whitespace=nowarn", str(path)],
            capture_output=True, text=True)
        if run.returncode != 0:
            raise ValueError(f"{origin}/{name} failed to apply:\n{run.stderr.strip()}")
        applied.append(f"{origin}/{name}")
        print(f"Applied {origin}/{name}", flush=True)

    # A positive check, not just "git said OK": the series is supposed to
    # leave a driver, a board tree and the DSA tagger behind, and a tree
    # without them is not the tree this lock describes.
    for required in ("drivers/net/ethernet/zte/zx279133.h",
                     "drivers/net/ethernet/zte/zx279133-host.c",
                     "drivers/net/ethernet/zte/zx279133-native.c",
                     "drivers/net/ethernet/zte/zx279133-offload.c",
                     "arch/arm64/boot/dts/zte/zx279133.dtsi",
                     "net/dsa/tag_zx279133_rtl8372n.c"):
        require((tree / required).exists(), f"the applied series did not produce {required}")
    marker.write_text(json.dumps(marker_payload, indent=2) + "\n", encoding="utf-8")
    return applied, []


def update_openwrt_version(openwrt, lock):
    """Point OpenWrt's metadata at the version this tree actually is."""
    spec = lock["openwrt"]
    path = openwrt / spec["version_file"]
    require(path.exists(), f"OpenWrt tree has no {spec['version_file']}")
    source = path.read_text(encoding="utf-8")
    series, sublevel = lock["version"].rsplit(".", 1)
    pattern = r"^" + re.escape(spec["version_line"]) + r"[ \t]*=.*$"
    require(len(re.findall(pattern, source, re.M)) == 1,
            "missing/ambiguous OpenWrt kernel version line")
    source = re.sub(pattern, f"{spec['version_line']} = .{sublevel}", source, flags=re.M)
    source = re.sub(r"^" + re.escape(spec["hash_line"]) + r"[ \t]*=.*\n?", "", source, flags=re.M)
    source = source.rstrip() + f"\n{spec['hash_line']} = {lock['source_tar_sha256']}\n"
    path.write_text(source, encoding="utf-8", newline="\n")
    print(f"Pinned OpenWrt metadata to Linux {lock['version']}", flush=True)


def check_built(tree, version, rootfs=None):
    require(kernel_version(tree) == version, "built Makefile has a different version")
    release = (tree / "include/config/kernel.release").read_text(encoding="utf-8").strip()
    require(re.fullmatch(re.escape(version) + r"(?:[+-].*)?", release), "built kernel release differs")
    image = (tree / "arch/arm64/boot/Image").read_bytes()
    require(re.search(rb"Linux version " + re.escape(release.encode()) + rb"[ \x00]", image),
            "raw Image banner does not match kernel.release")
    if rootfs is not None:
        modules = rootfs / "lib/modules"
        require(modules.is_dir(), "rootfs has no module directory")
        require({p.name for p in modules.iterdir() if p.is_dir()} == {release},
                "rootfs module directory does not match the built kernel")
    print(f"Verified actual Image and module release: {release}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path,
                        help="directory that will hold the prepared kernel tree")
    parser.add_argument("--cache", type=Path, default=ROOT / "work/linux-7.3")
    parser.add_argument("--version", help="workflow KVER; must agree with the lock")
    parser.add_argument("--report", type=Path, default=Path("kernel-provenance-73.json"))
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--check-built", type=Path)
    parser.add_argument("--rootfs", type=Path)
    parser.add_argument("--openwrt", type=Path)
    args = parser.parse_args()

    lock = load_lock()
    require(not args.version or args.version == lock["version"],
            "workflow KVER differs from the 7.3 lock")

    if args.check_built:
        check_built(args.check_built, lock["version"], args.rootfs)
        return

    tarball = fetch_tarball(lock, args.cache)
    if args.download_only:
        print(f"Verified tarball and {len(lock['patches'])} patches; no tree touched")
        return

    require(args.tree is not None, "--tree is required")
    tree = extract(tarball, args.tree, lock["version"])
    require(kernel_version(tree) == lock["version"],
            f"tree is Linux {kernel_version(tree)}, expected {lock['version']}")
    series = ([("vendored", name, payload) for name, payload in lock["patches"]] +
              [("local", name, payload) for name, payload in lock["local_patches"]])
    marker_payload = {
        "version": lock["version"],
        "manifest_sha256": lock["manifest_sha256"],
        "local_manifest_sha256": lock.get("local_manifest_sha256"),
        "patches": ["%s/%s" % (origin, name) for origin, name, _ in series],
    }
    applied, skipped = apply_series(tree, series, marker_payload)
    if args.openwrt:
        update_openwrt_version(args.openwrt, lock)

    report = {
        "line": lock["line"],
        "version": lock["version"],
        "source_tar": lock["source_tar"],
        "source_tar_sha256": lock["source_tar_sha256"],
        "source_tar_bytes": lock["source_tar_bytes"],
        "upstream": lock["upstream"],
        "manifest": lock["manifest"],
        "manifest_sha256": lock["manifest_sha256"],
        "local_manifest": lock["local_manifest"],
        "local_manifest_sha256": lock.get("local_manifest_sha256"),
        "patches_applied": applied,
        "patches_already_present": skipped,
        "source_directory": tree.as_posix(),
    }
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared Linux {lock['version']} with {len(applied)} patches"
          + (f" ({len(skipped)} already present)" if skipped else ""))


if __name__ == "__main__":
    main()
