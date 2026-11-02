#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Apply checksum-pinned official stable updates to the proven ZX279133 tree.

The wrapper's submodule name is a source location, NOT the resulting version.
Never refresh the custom driver branch while doing a stable-only upgrade.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import lzma
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "configs/sk-d840n-stable.json"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_lock(path=LOCK):
    lock = json.loads(path.read_text(encoding="utf-8"))
    series, start = lock["base_version"].rsplit(".", 1)
    end_series, end = lock["version"].rsplit(".", 1)
    require(series == end_series and int(end) > int(start), "not a stable-series upgrade")
    expected = [f"patch-{series}.{n}-{n + 1}.xz" for n in range(int(start), int(end))]
    require([p[0] for p in lock["patches"]] == expected, "missing/reordered stable patch")
    require(all(re.fullmatch(r"[0-9a-f]{64}", p[1]) for p in lock["patches"]), "invalid patch SHA256")
    for field in ("kernel_sha", "stable_base_sha", "stable_sha"):
        require(re.fullmatch(r"[0-9a-f]{40}", lock[field]), f"invalid {field}")
    require(lock["url"] == "https://cdn.kernel.org/pub/linux/kernel/v6.x/incr/", "unexpected patch origin")
    return lock


def kernel_version(tree):
    source = (tree / "Makefile").read_text(encoding="utf-8")
    values = []
    for key in ("VERSION", "PATCHLEVEL", "SUBLEVEL", "EXTRAVERSION"):
        hits = re.findall(r"^" + key + r"[ \t]*=[ \t]*([^\n]*)", source, re.M)
        require(len(hits) == 1, f"ambiguous kernel {key}")
        values.append(hits[0].strip())
    return ".".join(values[:3]) + values[3]


def changed_paths(patch):
    paths = set()
    # Rename-only and mode-only changes need not have ---/+++ headers.
    for match in re.finditer(rb"^diff --git a/(.+) b/(.+)$", patch, re.M):
        for encoded in match.groups():
            name = encoded.decode("utf-8")
            require(not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts,
                    "unsafe stable diff path")
            paths.add(name)
    for match in re.finditer(rb"^(?:---|\+\+\+) ([^\t\n]+)", patch, re.M):
        name = match[1].decode("utf-8")
        if name == "/dev/null":
            continue
        require(name.startswith(("a/", "b/")), f"unexpected diff path: {name}")
        name = name[2:]
        require(not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts,
                "unsafe stable diff path")
        paths.add(name)
    require(paths, "empty/unrecognized stable patch")
    return paths


def check_overlay_overlap(patches, overlays):
    owned = {p.relative_to(overlays).as_posix() for p in overlays.rglob("*") if p.is_file()}
    touched = set().union(*(changed_paths(patch) for patch in patches))
    overlap = sorted(owned & touched)
    require(not overlap, "overlays would undo official stable fixes; rebase explicitly: " + ", ".join(overlap))
    return touched


def download_patch(item, cache, url):
    name, expected = item
    destination = cache / name
    if destination.exists():
        data = destination.read_bytes()
        require(hashlib.sha256(data).hexdigest() == expected, f"cached patch SHA256 mismatch: {name}")
        return lzma.decompress(data)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url + name, timeout=60) as response:
                data = response.read()
            break
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2)
    require(hashlib.sha256(data).hexdigest() == expected, f"download SHA256 mismatch: {name}")
    decoded = lzma.decompress(data)
    destination.write_bytes(data)
    print(f"Verified {name}: {len(decoded)} decoded bytes", flush=True)
    return decoded


def download_all(lock, cache):
    cache.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(lambda item: download_patch(item, cache, lock["url"]), lock["patches"]))


def git(tree, *args):
    return subprocess.check_output(["git", "-c", "core.autocrlf=false", "-C", str(tree), *args], text=True).strip()


def adapted_patch(name, payload, lock):
    for rebase in lock.get("rebases", []):
        if rebase["patch"] != name:
            continue
        replacement = (ROOT / rebase["replacement"]).read_bytes()
        require(hashlib.sha256(replacement).hexdigest() == rebase["sha256"], "rebased patch SHA256 mismatch")
        path = rebase["path"]
        require(changed_paths(replacement) == {path}, "rebase changes unexpected paths")
        sections = re.split(rb"(?=^diff --git )", payload, flags=re.M)
        header = f"diff --git a/{path} b/{path}\n".encode()
        matches = [i for i, section in enumerate(sections) if section.startswith(header)]
        require(len(matches) == 1, "upstream rebase section disappeared or duplicated")
        def changes(section):
            return [line for line in section.splitlines() if line.startswith((b"+", b"-"))
                    and not line.startswith((b"+++", b"---"))]
        require(changes(sections[matches[0]]) == changes(replacement),
                "context-only rebase changed the official fix")
        sections[matches[0]] = replacement
        payload = b"".join(sections)
        print(f"Explicitly rebased {name}: {path} (official changes retained)", flush=True)
    return payload


def apply_preparations(tree, name, lock):
    for preparation in lock.get("preparations", []):
        if preparation["before_patch"] != name:
            continue
        path = ROOT / preparation["patch"]
        payload = path.read_bytes()
        require(hashlib.sha256(payload).hexdigest() == preparation["sha256"], "prerequisite patch SHA256 mismatch")
        require(changed_paths(payload) == {preparation["path"]}, "prerequisite changes unexpected paths")
        git(tree, "apply", "--check", "--whitespace=nowarn", str(path))
        git(tree, "apply", "--whitespace=nowarn", str(path))
        print(f"Prepared custom code for unchanged official fix: {path.name}", flush=True)


def apply_updates(tree, lock, patches, overlays):
    require(kernel_version(tree) == lock["base_version"], "source is not the locked base kernel version")
    require(git(tree, "rev-parse", "HEAD") == lock["kernel_sha"], "custom kernel commit changed")
    require(not git(tree, "status", "--porcelain", "--untracked-files=no"), "kernel source is already modified")
    touched = check_overlay_overlap(patches, overlays)
    with tempfile.TemporaryDirectory(prefix="sk-stable-") as tmp:
        patch_file = Path(tmp) / "stable.patch"
        for (name, _), patch in zip(lock["patches"], patches):
            apply_preparations(tree, name, lock)
            patch = adapted_patch(name, patch, lock)
            patch_file.write_bytes(patch)
            # No --reject, ignored failures, fuzzy matching, or old whole-file overlays.
            git(tree, "apply", "--check", "--whitespace=nowarn", str(patch_file))
            git(tree, "apply", "--whitespace=nowarn", str(patch_file))
            print(f"Applied {name}", flush=True)
    require(kernel_version(tree) == lock["version"], "stable patches did not produce the target version")
    return touched


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


def update_openwrt_version(openwrt, lock):
    series, sublevel = lock["version"].rsplit(".", 1)
    path = openwrt / "target/linux/generic" / ("kernel-" + series)
    source = path.read_text(encoding="utf-8")
    pattern = r"^LINUX_VERSION-" + re.escape(series) + r"[ \t]*=.*$"
    require(len(re.findall(pattern, source, re.M)) == 1, "missing/ambiguous OpenWrt kernel version")
    source = re.sub(pattern, f"LINUX_VERSION-{series} = .{sublevel}", source, flags=re.M)
    source = re.sub(r"^LINUX_KERNEL_HASH-" + re.escape(lock["version"]) + r"[ \t]*=.*\n?", "", source, flags=re.M)
    source = source.rstrip() + f"\nLINUX_KERNEL_HASH-{lock['version']} = {lock['source_tar_sha256']}\n"
    path.write_text(source, encoding="utf-8", newline="\n")
    print(f"Pinned OpenWrt metadata to Linux {lock['version']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path)
    parser.add_argument("--cache", type=Path, default=ROOT / "work/stable-patches")
    parser.add_argument("--overlays", type=Path, default=ROOT / "kernel-overlays")
    parser.add_argument("--version", help="workflow KVER; must agree with the lock")
    parser.add_argument("--report", type=Path, default=Path("kernel-provenance.json"))
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--check-built", type=Path)
    parser.add_argument("--rootfs", type=Path)
    parser.add_argument("--openwrt", type=Path)
    args = parser.parse_args()
    lock = load_lock()
    require(not args.version or args.version == lock["version"], "workflow KVER differs from stable lock")
    if args.check_built:
        check_built(args.check_built, lock["version"], args.rootfs)
        return
    patches = download_all(lock, args.cache)
    touched = check_overlay_overlap(patches, args.overlays)
    if args.download_only:
        print(f"Verified {len(patches)} patches, {len(touched)} changed paths; no overlay overlap")
        return
    require(args.tree is not None, "--tree is required")
    apply_updates(args.tree.resolve(), lock, patches, args.overlays)
    if args.openwrt:
        update_openwrt_version(args.openwrt, lock)
    report = dict(lock, lock_sha256=hashlib.sha256(LOCK.read_bytes()).hexdigest(),
                  source_directory=args.tree.as_posix(), changed_paths=len(touched))
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared Linux {lock['version']} from custom commit {lock['kernel_sha']}")


if __name__ == "__main__":
    main()
