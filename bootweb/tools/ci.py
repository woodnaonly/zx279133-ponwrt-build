#!/usr/bin/env python3
"""Prepare public dependencies and build OFFLINE native241 release candidates.

No device transport, installer, backup or user authorization is used here.
Only the explicitly selected files below may leave the build directory.
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from audit_ram_elf import verify_payload
from ci_profile import PROFILE, load_ci_profile, public_boot
from host.core import BASE, Core
from host.image import regions
from package import make_uboot
from verify import dependency, inputs

CANDIDATE = "sk-d840n-bootweb-native241-candidate.tar"
TOOLKIT = "sk-d840n-bootweb-native241-tools.tgz"
CHECKSUMS = "sha256sums-bootweb"
RELEASE_FILES = (CANDIDATE, TOOLKIT, CHECKSUMS)
PREFIX = "sk-d840n-bootweb-native241/"
SOURCE_DIRS = ("adapter", "host", "include", "src", "tools", "tests", "profiles", "vendor", "web", "LICENSES")
DEBUG_FILES = ("bootweb-resident-native.elf", "bootweb-resident-native.bin", "resident-bootstrap.elf",
               "resident-bootstrap.bin", "resident-stage0.elf", "resident-stage0.bin", "resident-tail-ram-trial.bin")
TOOLKIT_REQUIRED = ("README.md", "manifest.json", "resident-native-policy.json", "offline-verification.json",
                    "instruction-audits.json", "source-provenance.json", "web/index.html", "LICENSES/lwip.txt", "LICENSES/GPL-2.0-only.txt",
                    "source/bootweb/tools/ci.py", "source/bootweb/tools/dependencies.lock.json", "source/lwip/COPYING",
                    "toolkit-sha256sums", *("debug/" + name for name in DEBUG_FILES))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def run(command, log):
    print("Running:", " ".join(map(str, command)), flush=True)
    result = subprocess.run(list(map(str, command)), cwd=ROOT.parent, capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
    with Path(log).open("a", encoding="utf-8", newline="\n") as target:
        target.write("\n$ " + " ".join(map(str, command)) + "\n" + result.stdout + result.stderr)
    if result.returncode:
        print((result.stdout + result.stderr)[-12000:], file=sys.stderr)
        raise RuntimeError(f"{Path(command[0]).name} failed ({result.returncode}); see {log}")
    return result.stdout.strip()


def download(url, destination, digest):
    destination = Path(destination)
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=120) as source:
            with destination.open("xb") as target:
                while block := source.read(1024 * 1024):
                    target.write(block)
    if sha(destination.read_bytes()) != digest:
        raise ValueError("downloaded/cached dependency SHA256 mismatch: " + destination.name)


def check_lwip(path, lock):
    actual = dependency(Path(path).resolve())
    if any(actual[k] != lock["lwip"][k] for k in
           ("version", "source_and_headers_sha256", "license_sha256", "hashed_file_count")):
        raise ValueError("lwIP source/license differs from dependencies.lock.json")
    return actual


def prepare(work, reports):
    lock = read_json(ROOT / "tools/dependencies.lock.json")
    deps = ROOT / ".build/ci-deps"
    toolchain = ROOT / ".build/toolchain"
    archive = toolchain / lock["zig"]["windows_archive"]
    download("https://ziglang.org/download/0.14.1/" + archive.name, archive, lock["zig"]["archive_sha256"])
    with zipfile.ZipFile(archive) as source:
        source.extractall(toolchain)
    log = reports / "prepare.log"
    zig = toolchain / "zig-x86_64-windows-0.14.1/zig.exe"
    if run([zig, "version"], log) != lock["zig"]["version"]:
        raise ValueError("unexpected Zig version")
    repository = deps / "uboot-an758x"
    if not (repository / ".git").is_dir():
        repository.mkdir(parents=True, exist_ok=False)
        run(["git", "init", repository], log)
        run(["git", "-C", repository, "remote", "add", "origin", lock["lwip"]["repository"]], log)
    run(["git", "-C", repository, "config", "core.autocrlf", "false"], log)
    run(["git", "-C", repository, "config", "core.eol", "lf"], log)
    if run(["git", "-C", repository, "remote", "get-url", "origin"], log) != lock["lwip"]["repository"]:
        raise ValueError("cached lwIP repository is not the locked upstream")
    run(["git", "-C", repository, "sparse-checkout", "set", "lib/lwip/lwip"], log)
    run(["git", "-C", repository, "fetch", "--depth=1", "--filter=blob:none", "origin", lock["lwip"]["revision"]], log)
    run(["git", "-C", repository, "checkout", "--detach", lock["lwip"]["revision"]], log)
    lwip = repository / lock["lwip"]["source_path"]
    check_lwip(lwip, lock)
    reference = deps / "public-boot.bin"
    boot = lock["public_boot"]
    download(boot["repository"].replace("https://github.com/", "https://raw.githubusercontent.com/") +
             "/" + boot["revision"] + "/" + boot["path"], reference, boot["sha256"])
    public_boot(reference.read_bytes(), lock)
    write_json(work / "dependencies.json", {"zig": str(zig.resolve()), "lwip_dir": str(lwip.resolve()),
                                           "reference_boot": str(reference.resolve())})


def check_candidate(package, reference, candidate_boot, manifest, core=None):
    core = core or Core()
    public_boot(reference)
    before, after = regions(reference), regions(candidate_boot)
    if any(before[name] != after[name] for name in ("stage1", "environment", "tail")):
        raise ValueError("candidate changed a protected boot region")
    digest = sha(after["stage2"])
    policy = manifest.get("payload_build_policy", {})
    if (manifest.get("source_installed_sha256") != BASE or manifest.get("installed_sha256") != digest or
            manifest.get("boot_sha256") != sha(candidate_boot) or manifest.get("ci_offline_build") is not True or
            manifest.get("hardware_acceptance_complete") is not False or
            manifest.get("installation_authorized") is not False or manifest.get("flashable_release") is not False or
            manifest.get("native241_service") is not True or manifest.get("reset_cold_entry_enabled") is not True or
            manifest.get("native_image_format_offline_pass") is not True or manifest.get("reset_verified") is not False or
            manifest.get("reset_physical_evidence") is not None or manifest.get("user_directed_unverified_reset") is not None or
            policy.get("ci_offline_build") is not True or policy.get("web_uboot_enabled") is not False or
            policy.get("periodic_budget_verified") is not False or policy.get("timing_ledger", {}).get("TIMING_PROVEN") != 0 or
            policy.get("layout") != "native241" or policy.get("power_mode") != "continuous_required"):
        raise ValueError("candidate manifest is not a public-base offline native241 build")
    identity = core.resident_identity(before["stage2"])
    result = core.validate_identity(package, identity)
    if (result.kind != 2 or result.count != 1 or
            (result.writes[0].offset, result.writes[0].capacity) != (0x20000, 0x160000) or
            result.writes[0].sha256.decode() != digest):
        raise ValueError("candidate package leaves the fixed stage2 write window")
    with tarfile.open(fileobj=io.BytesIO(package), mode="r:") as archive:
        members = archive.getmembers()
        if [m.name for m in members] != ["CONTROL", "uboot.bin", "sha256sums"] or any(not m.isfile() for m in members):
            raise ValueError("candidate package must contain exactly three regular files")
        if archive.extractfile("uboot.bin").read() != after["stage2"]:
            raise ValueError("candidate package payload differs from the assembled stage2")
    return result.report()


def archive_bytes(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size, member.mode, member.mtime = len(data), 0o644, 0
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


def toolkit_files(payload, resident, reports, lwip):
    files = {}
    for folder, names in ((payload, ("bootweb-resident-native.elf", "bootweb-resident-native.bin")),
                          (resident, ("resident-bootstrap.elf", "resident-bootstrap.bin", "resident-stage0.elf",
                                      "resident-stage0.bin", "resident-tail-ram-trial.bin"))):
        for name in names:
            files["debug/" + name] = (folder / name).read_bytes()
    for name, path in (("manifest.json", resident / "manifest.json"),
                       ("resident-native-policy.json", payload / "resident-native-policy.json"),
                       ("offline-verification.json", reports / "offline-verification.json"),
                       ("instruction-audits.json", reports / "instruction-audits.json"),
                       ("source-provenance.json", reports / "source-provenance.json"),
                       ("README.md", ROOT / "docs/CI-RELEASE.md"),
                       ("web/index.html", ROOT / "web/index.html"),
                       ("LICENSES/GPL-2.0-only.txt", ROOT / "LICENSES/GPL-2.0-only.txt"),
                       ("LICENSES/lwip.txt", ROOT / "LICENSES/lwip.txt")):
        files[name] = path.read_bytes()
    # Source-only allowlist: no .build, hardware reports, firmware dumps or configs.
    for folder in SOURCE_DIRS:
        for path in sorted((ROOT / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                files["source/bootweb/" + path.relative_to(ROOT).as_posix()] = path.read_bytes()
    files["source/bootweb/README.md"] = (ROOT / "README.md").read_bytes()
    linked_sources = set(dependency(lwip)["linked_sources"])
    for path in sorted(lwip.rglob("*")):
        if path.is_file() and (path.suffix == ".h" or path.relative_to(lwip).as_posix() in linked_sources):
            files["source/lwip/src/" + path.relative_to(lwip).as_posix()] = path.read_bytes()
    files["source/lwip/COPYING"] = (lwip.parent / "COPYING").read_bytes()
    files["toolkit-sha256sums"] = "".join(sha(data) + "  " + name + "\n" for name, data in sorted(files.items())).encode()
    return {PREFIX + name: data for name, data in files.items()}


def validate_dist(dist):
    dist = Path(dist)
    if set(p.name for p in dist.iterdir()) != set(RELEASE_FILES) or any(
            not (dist / name).is_file() or (dist / name).is_symlink() for name in RELEASE_FILES):
        raise ValueError("release outputs are missing or contain unexpected files")
    expected = "".join(sha((dist / name).read_bytes()) + "  " + name + "\n" for name in (CANDIDATE, TOOLKIT))
    if (dist / CHECKSUMS).read_text(encoding="utf-8") != expected:
        raise ValueError("release checksum manifest mismatch")
    with tarfile.open(dist / CANDIDATE, "r:") as archive:
        members = archive.getmembers()
        if [m.name for m in members] != ["CONTROL", "uboot.bin", "sha256sums"] or any(not m.isfile() for m in members):
            raise ValueError("invalid candidate archive members")
        payload = archive.extractfile("uboot.bin").read()
        control = archive.extractfile("CONTROL").read()
        expected_control = ("FORMAT=sk-d840n-uboot-v1\nBOARD=skyworth_sk-d840n\n" +
                            f"BASE_SHA256={BASE}\nPAYLOAD_SHA256={sha(payload)}\nPAYLOAD_BYTES=1441792\n").encode()
        if (len(payload) != 0x160000 or control != expected_control or
                archive.extractfile("sha256sums").read() !=
                (sha(control) + "  CONTROL\n" + sha(payload) + "  uboot.bin\n").encode()):
            raise ValueError("candidate payload/base/internal checksum mismatch")
    with tarfile.open(dist / TOOLKIT, "r:gz") as archive:
        members = archive.getmembers()
        names = [m.name for m in members]
        if len(names) != len(set(names)) or any(not m.isfile() or not m.name.startswith(PREFIX) or
                ".." in PurePosixPath(m.name).parts or "\\" in m.name or ".build" in PurePosixPath(m.name).parts
                for m in members):
            raise ValueError("unsafe toolkit archive member")
        files = {m.name.removeprefix(PREFIX): archive.extractfile(m).read() for m in members}
        for name in TOOLKIT_REQUIRED:
            if name not in files:
                raise ValueError("missing toolkit output: " + name)
        for name in files:
            if name not in TOOLKIT_REQUIRED and not name.startswith(("source/bootweb/", "source/lwip/")):
                raise ValueError("unexpected toolkit output: " + name)
        expected = "".join(sha(data) + "  " + name + "\n" for name, data in sorted(files.items()) if name != "toolkit-sha256sums")
        if files["toolkit-sha256sums"] != expected.encode():
            raise ValueError("toolkit checksum mismatch")
        verification = json.loads(files["offline-verification.json"])
        manifest = json.loads(files["manifest.json"])
        policy = json.loads(files["resident-native-policy.json"])
        if (verification.get("offline_pass") is not True or verification["tests"]["run"] <= 0 or
                any(verification["tests"][key] != 0 for key in ("failed", "errors", "skipped")) or
                manifest.get("installed_sha256") != sha(payload) or manifest.get("ci_offline_build") is not True or
                manifest.get("flashable_release") is not False or manifest.get("hardware_acceptance_complete") is not False or
                manifest.get("installation_authorized") is not False or manifest.get("source_installed_sha256") != BASE or
                manifest.get("payload_build_policy") != policy or policy.get("ci_offline_build") is not True or
                policy.get("web_uboot_enabled") is not False or policy.get("layout") != "native241" or
                policy.get("power_mode") != "continuous_required" or policy.get("timing_ledger", {}).get("TIMING_PROVEN") != 0):
            raise ValueError("release verification/manifest does not permit offline publication")
    return {name: sha((dist / name).read_bytes()) for name in RELEASE_FILES}


def build(work, reports, zig, lwip, reference):
    lock = read_json(ROOT / "tools/dependencies.lock.json")
    public_boot(reference.read_bytes(), lock)
    profile = load_ci_profile(PROFILE, reference.read_bytes())
    check_lwip(lwip, lock)
    log = reports / "build.log"
    if run([zig, "version"], log) != lock["zig"]["version"]:
        raise ValueError("unexpected Zig version")
    payload, resident, dist = (work / name for name in ("payload", "resident", "dist"))
    if any(path.exists() for path in (payload, resident, dist, reports / "offline-verification.json")):
        raise ValueError("build outputs already exist; choose a fresh --work-dir")
    run([sys.executable, "-X", "utf8", ROOT / "tools/verify.py", "--zig", zig, "--lwip-dir", lwip,
         "--output", reports / "offline-verification.json"], log)
    verification = read_json(reports / "offline-verification.json")
    if (verification.get("offline_pass") is not True or verification["source_inputs"] != inputs() or
            any(verification["tests"][k] != 0 for k in ("failed", "errors", "skipped"))):
        raise ValueError("offline verification failed, skipped tests, or used different sources")
    run([sys.executable, "-X", "utf8", ROOT / "tools/build_native_resident.py", "--build-dir", payload,
         "--reference-boot", reference, "--ci-offline-profile", PROFILE, "--native241-continuous",
         "--zig", zig, "--lwip-dir", lwip], log)
    run([sys.executable, "-X", "utf8", ROOT / "tools/build_resident.py", "--payload-dir", payload,
         "--build-dir", resident, "--boot-dump", reference, "--vendor-reference", reference,
         "--ci-offline-profile", PROFILE, "--native241-service", "--resident-format", "--device-entropy-trial",
         "--zig", zig], log)
    manifest = read_json(resident / "manifest.json")
    audits = {
        "payload": verify_payload((payload / "bootweb-resident-native.elf").read_bytes(),
                                  (payload / "bootweb-resident-native.bin").read_bytes(), limit=0x89100000),
        "bootstrap": verify_payload((resident / "resident-bootstrap.elf").read_bytes(),
                                    (resident / "resident-bootstrap.bin").read_bytes(), base=0x89100000, limit=0x891e0000),
        "stage0": verify_payload((resident / "resident-stage0.elf").read_bytes(),
                                 (resident / "resident-stage0.bin").read_bytes(), base=0x88100000, limit=0x88160000)}
    candidate_boot = (resident / "resident-boot-development.bin").read_bytes()
    package = make_uboot(reference.read_bytes(), candidate_boot)
    package_report = check_candidate(package, reference.read_bytes(), candidate_boot, manifest)
    if verification["source_inputs"] != inputs():
        raise ValueError("source changed during candidate build")
    write_json(reports / "instruction-audits.json", audits)
    write_json(reports / "source-provenance.json", {"schema": "sk-d840n-bootweb-ci-provenance-v1", **profile,
        "source_commit": run(["git", "rev-parse", "HEAD"], log),
        "worktree_dirty": bool(run(["git", "status", "--porcelain"], log)),
        "source_inputs": verification["source_inputs"], "dependencies": lock,
        "toolchain": verification["toolchain"], "candidate_check": package_report,
        "nand_written": False, "web_uboot_enabled": False, "flashable_release": False})
    files = toolkit_files(payload, resident, reports, lwip)
    for name, digest in verification["source_inputs"]["files"].items():
        if sha(files[PREFIX + "source/bootweb/" + name]) != digest:
            raise ValueError("packaged source differs from offline verification: " + name)
    dist.mkdir(parents=True, exist_ok=False)
    (dist / CANDIDATE).write_bytes(package)
    (dist / TOOLKIT).write_bytes(gzip.compress(archive_bytes(files), mtime=0))
    (dist / CHECKSUMS).write_text("".join(sha((dist / name).read_bytes()) + "  " + name + "\n"
                                         for name in (CANDIDATE, TOOLKIT)), encoding="utf-8", newline="\n")
    checks = validate_dist(dist)
    write_json(reports / "publish-validation.json", {"validated": True, "offline_only": True,
        "hardware_acceptance_complete": False, "installation_authorized": False, "files": checks})
    print(f"Validated OFFLINE native241 candidate: {dist}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "build", "validate-dist"))
    parser.add_argument("--work-dir", type=Path, default=ROOT / ".build/ci-native241")
    parser.add_argument("--zig", type=Path)
    parser.add_argument("--lwip-dir", type=Path)
    parser.add_argument("--reference-boot", type=Path)
    args = parser.parse_args()
    work = args.work_dir.resolve()
    reports = work / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    try:
        if args.command == "prepare":
            prepare(work, reports)
        elif args.command == "validate-dist":
            validate_dist(work / "dist")
        else:
            dependencies = read_json(work / "dependencies.json") if (work / "dependencies.json").is_file() else {}
            values = [getattr(args, k) or dependencies.get(k) for k in ("zig", "lwip_dir", "reference_boot")]
            if not all(values):
                raise ValueError("run prepare or supply --zig --lwip-dir --reference-boot")
            build(work, reports, *(Path(value).resolve() for value in values))
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError, tarfile.TarError, zipfile.BadZipFile) as error:
        write_json(reports / "failure.json", {"validated": False, "command": args.command, "error": str(error),
            "device_contacted": False, "nand_written": False, "installation_authorized": False})
        print("bootweb CI:", error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
