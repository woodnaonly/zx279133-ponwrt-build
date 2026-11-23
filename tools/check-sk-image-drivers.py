#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that the kernel image really carries this board's drivers.

    python3 tools/check-sk-image-drivers.py \\
        --image flash-uImage \\
        --manifest configs/sk-d840n-73-image-drivers.json \\
        --tree linux-7.3.4 \\
        --board-layer kernel-overlays-73

Why this exists
---------------
The 7.3 line has three gates that say the drivers are there, and none of them
looked at the bytes that go on the board:

  * the merged-config gate asks whether CONFIG_ZTE_ZX279133_ETH=y and friends
    are in the kernel's .config - that is a statement about the configuration,
  * the host-compiler step asks whether clang can parse the files - that is a
    statement about the sources,
  * the Kbuild wiring is what decides whether a file is compiled at all, and
    the object list is a few lines in a Makefile that a patch can move.

"In the tree", "compiles", "configured as built-in" and "in the image that will
be flashed" are four different claims, and only the last one is the one that
matters when a board comes up with no network port.  This gate asks the last
one, on the artifact itself.

How it decides
--------------
Each probe names a string that exists in one of our own sources and has to
exist in the inflated image as well.  The two halves are checked separately:

  * the needle must still be in the source file it is attributed to.  If the
    source was renamed or the message was reworded, the probe is stale and the
    gate says so instead of quietly passing on a string that now means
    something else,
  * the needle must be in the image.  A missing one means the code was not
    compiled into the kernel that is about to be flashed.

The probe list is not written into this tool: it lives in a manifest next to
the line it belongs to, so that the 7.3 line and the release line can carry
different lists - they are different kernels with different drivers in them.

Exit status: 0 when every probe holds, 1 otherwise.
"""
import argparse
import gzip
import io
import json
import re
import struct
import sys
from pathlib import Path

UIMAGE_MAGIC = 0x27051956
UIMAGE_HEADER_LEN = 64


def inflate_uimage(path: Path) -> bytes:
    """Return the inflated payload of a legacy uImage."""
    raw = path.read_bytes()
    if len(raw) < UIMAGE_HEADER_LEN:
        raise SystemExit("%s: too short to be a uImage (%d bytes)" % (path, len(raw)))
    magic, _hcrc, itime, dsize = struct.unpack(">4I", raw[:16])
    if magic != UIMAGE_MAGIC:
        raise SystemExit("%s: not a legacy uImage (magic %08x)" % (path, magic))
    name = raw[32:64].split(b"\0")[0].decode("latin1", "replace")
    body = raw[UIMAGE_HEADER_LEN:UIMAGE_HEADER_LEN + dsize] if dsize else raw[UIMAGE_HEADER_LEN:]
    if body[:2] != b"\x1f\x8b":
        raise SystemExit("%s: the uImage body is not gzip (%r)" % (path, body[:4]))
    # A gzip stream can hold several members; gzip.decompress() reads the
    # first one only, and the rest of the kernel would be missing without a
    # word about it.
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=io.BytesIO(body)) as gz:
        while True:
            chunk = gz.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    return out.getvalue()


def kernel_version(image: bytes) -> str:
    """The kernel's own banner, if the image carries one."""
    m = re.search(rb"Linux version (\S+)", image)
    return m.group(1).decode("latin1") if m else "(no banner)"


def load_manifest(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit("%s: cannot read the probe manifest: %s" % (path, exc))
    if not isinstance(data, dict) or not isinstance(data.get("probes"), list):
        raise SystemExit("%s: the probe manifest has no probes list" % path)
    return data


def resolve_source(source: dict, roots: dict):
    """The path of a probe's source, or None when its root is not given.

    A root that was not passed on the command line is not an error: the
    probes list the tree first and the repository's own copies second, so a
    run without a tree still has something to check the probe against.
    """
    root_name = source.get("root", "repo")
    rel = source.get("path", "")
    root = roots.get(root_name)
    if root is None:
        return None
    return root / rel


def check_probe(probe: dict, image: bytes, roots: dict) -> tuple:
    """Return (ok, lines) for one probe."""
    lines = []
    needle = probe.get("needle", "")
    label = probe.get("label", needle)
    if not needle:
        return False, ["%s: the probe has no needle" % label]

    sources = probe.get("sources") or []
    if not sources:
        return False, ["%s: the probe names no source file" % label]

    found = None
    for source in sources:
        candidate = resolve_source(source, roots)
        if candidate is not None and candidate.is_file():
            found = candidate
            break
    if found is None:
        wanted = ", ".join("%s/%s" % (s.get("root", "repo"), s.get("path", ""))
                           for s in sources)
        return False, ["%s: no source file found (looked for %s)" % (label, wanted)]

    text = found.read_text(encoding="utf-8", errors="replace")
    if needle not in text:
        return False, ["%s: the probe is stale - %r is not in %s any more"
                       % (label, needle, found)]

    hits = image.count(needle.encode())
    if hits == 0:
        why = probe.get("why", "")
        return False, ["%s: the image does not carry %r (%s)"
                       % (label, needle, why or "no reason given")]
    lines.append("%s: HIT %r x%d (source: %s)"
                 % (label, needle, hits, found))
    return True, lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that a kernel image carries this board's drivers.")
    parser.add_argument("--image", required=True, type=Path,
                        help="the uImage that is about to be flashed")
    parser.add_argument("--manifest", required=True, type=Path,
                        help="the probe manifest of this line")
    parser.add_argument("--tree", type=Path,
                        help="the kernel tree the image was built from")
    parser.add_argument("--board-layer", type=Path,
                        help="the board layer directory (kernel-overlays-73)")
    parser.add_argument("--report", type=Path,
                        help="write the verdict as JSON here")
    args = parser.parse_args(argv)

    if not args.image.is_file():
        raise SystemExit("%s: no such image" % args.image)
    manifest = load_manifest(args.manifest)

    repo_root = args.manifest.resolve().parent.parent
    roots = {"repo": repo_root}
    if args.tree:
        roots["tree"] = args.tree
    if args.board_layer:
        roots["board"] = args.board_layer

    image = inflate_uimage(args.image)
    print("image: %s" % args.image)
    print("  inflated: %d bytes, kernel %s" % (len(image), kernel_version(image)))
    print("probes: %d (manifest %s)" % (len(manifest["probes"]), args.manifest))

    failures = []
    passed = 0
    for probe in manifest["probes"]:
        ok, lines = check_probe(probe, image, roots)
        for line in lines:
            print("  %s" % line)
        if ok:
            passed += 1
        else:
            failures.extend(lines)

    print("")
    if failures:
        print("FAIL: %d of %d probe(s) failed - the image is not this board's kernel"
              % (len(failures), len(manifest["probes"])))
        for line in failures:
            print("  %s" % line)
    else:
        print("OK: all %d probe(s) hold - the image carries every driver it claims"
              % passed)

    if args.report:
        args.report.write_text(json.dumps({
            "image": str(args.image),
            "kernel": kernel_version(image),
            "probes": len(manifest["probes"]),
            "passed": passed,
            "failed": len(failures),
        }, indent=2) + "\n", encoding="utf-8")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
