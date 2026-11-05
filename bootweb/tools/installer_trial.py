#!/usr/bin/env python3
"""One-owner natural-cycle RAM installer. PREFLIGHT is the safe default.

Re-inspect native ABI and RAM, run/restore the minimal command probe, then
XMODEM/CRC the distinct installer. Real execution additionally requires the
explicit --execute-native-install option and rehashed exact final evidence.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.uart import AliasHook, Console, Span, TrialError, open_serial, parse_md, validate_placement
from ram_probe import BASE_HASH, build_probe, field, relocated_record, runtime_map,clean_natural_anchor
from build import RAM_PINS, SPI_PINS, NAND_WRITE_PINS
from audit_ram_elf import verify_payload
from host.core import Core
from host.resident_runtime import command_record


def final_evidence(manifest):
    """Reopen immutable artifact/evidence bindings; never grant hardware facts."""
    from build_installer import real_evidence,user_directed_evidence,native241_request_evidence
    if manifest.get("mode")!="install" or manifest.get("native_write_enabled") is not True:
        raise TrialError("real execution requires the separate accepted install build")
    token=manifest.get("authorization")
    if not isinstance(token,str) or not re.fullmatch(r"[0-9a-f]{64}",token):raise TrialError("missing exact one-use native authorization")
    sources=manifest["source_paths"];resident=Path(sources["resident_dir"])/"manifest.json"
    raw=resident.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    if digest!=manifest["resident_manifest_sha256"]:raise TrialError("final resident manifest changed")
    def evidence(item):
        raw=Path(item["path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=item["sha256"]:raise TrialError("acceptance report changed after compilation")
        return json.loads(raw)
    files=manifest["evidence_files"]
    if manifest.get("acceptance",{}).get("native241_requested_installation") is True:
        accepted=native241_request_evidence(json.loads(raw),digest,evidence(files["native241_install_request"]))
    elif manifest.get("acceptance",{}).get("user_directed_unaccepted_installation") is True:
        accepted=user_directed_evidence(json.loads(raw),digest,evidence(files["user_directed_install_policy"]))
    else:
        accepted=real_evidence(json.loads(raw),digest,[evidence(item) for item in files["ram_acceptance"]],
            *[evidence(files[key]) for key in ("cold_acceptance","reset_evidence","installer_preflight","timing_evidence")])
    if accepted!=manifest["acceptance"]:raise TrialError("compiled acceptance no longer matches source reports")
    backup=Path(sources["current_backup"]).read_bytes()
    if len(backup)!=0x10000000 or hashlib.sha256(backup).hexdigest()!=manifest["backup_sha256"]:raise TrialError("current full recovery backup changed")
    config=Path(sources["config_report"]).parent/"current-config.tar.gz"
    if hashlib.sha256(config.read_bytes()).hexdigest()!=manifest["configuration_sha256"]:raise TrialError("current saved configuration changed")
    return token


def installation_evidence(text,manifest):
    # Reuse only the common protected/RAM reconstruction checks below, never
    # the preflight success path: real readback must name the C core comparison.
    for phase in ("before","after"):
        for i,(off,n) in enumerate(((0,0x20000),(0x180000,0x20000),(0x1a0000,0x60000),(0xb00000,0x400000))):
            mark=f"BW_INSTALL_PROTECTED phase={phase} off={off:x} bytes={n:x} sha256={manifest['protected_sha256'][i]} matches_backup=1"
            if text.count(mark)!=1:raise TrialError("actual installation protected audit missing or changed")
    candidate=manifest["candidate_sha256"]
    if not all(mark in text for mark in ("BW_INSTALL_ENTRY mode=real ","BW_INSTALL_EXECUTE rc=0 error=",
        f"BW_INSTALL_READBACK sha256={candidate} bytes=1441792 compared_by_core=1",
        "BW_INSTALL_EXIT rc=0","stack_ok=1 authorization_consumed=1")):
        raise TrialError("real execution/full ECC readback did not complete")
    ready=re.search(r"BW_INSTALL_RAM_READY original_sha256=([0-9a-f]{64}) candidate_sha256=([0-9a-f]{64}) package_sha256=([0-9a-f]{64}) package_bytes=(\d+) bound_ms=(\d+) uptime_ms=(\d+)",text)
    if not ready or ready.group(1,2,3)!=tuple(manifest[k] for k in ("original_sha256","candidate_sha256","package_sha256")) or int(ready[4])!=manifest["package_bytes"]:
        raise TrialError("real candidate/recovery/package identity was not bound")
    released=re.search(r"BW_INSTALL_NAND_RELEASE rc=0 reads=(\d+) corrected_reads=(\d+) erases=11 pages=(\d+) skipped_ff=(\d+) max_read_ms=(\d+) max_bad_ms=(\d+) max_erase_ms=(\d+) max_page_ms=(\d+) failed=0",text)
    if not released or int(released[1])<96 or int(released[3])+int(released[4])!=704:raise TrialError("actual native operation totals failed")
    operations=re.findall(r"BW_NAND_REAL_END rc=0 kind=(erase|page) offset=([0-9a-f]+) wren=1 loads=([01]) executes=1 hardware_mutations=1",text)
    erases=[int(off,16) for kind,off,loads in operations if kind=="erase" and loads=="0"]
    pages=[int(off,16) for kind,off,loads in operations if kind=="page" and loads=="1"]
    if erases!=list(range(0x20000,0x180000,0x20000)) or len(pages)!=int(released[3]) or len(set(pages))!=len(pages) or any(not 0x20000<=off<=0x17f800 or off%2048 for off in pages):raise TrialError("actual erase/page log leaves or incompletely covers the fixed window")
    if not re.search(r"BW_BOUNDED_SPI_DETACHED rc=0 .* denied=0 pointer_restored=1",text):raise TrialError("real bounded SPI restoration failed")
    if re.search(r"BW_(?:INSTALL_REFUSED|INSTALL_ECC_ERROR|BOUNDED_SPI_ERROR|BOUNDED_SPI_DENIED|NAND_PREFLIGHT_DENIED)",text):raise TrialError("real installation reported a native failure")
    return {"actual_write_and_readback_pass":True,"readback_sha256":candidate,"hardware_mutations":len(operations),
            "real_erases":len(erases),"real_program_pages":len(pages),"skipped_ff_pages":int(released[4]),
            "corrected_reads":int(released[2]),"protected_audit_pass":True}


def preflight_evidence(text, manifest):
    protected = {}
    for phase, off, size, digest in re.findall(
            r"BW_INSTALL_PROTECTED phase=(before|after) off=([0-9a-f]+) bytes=([0-9a-f]+) sha256=([0-9a-f]{64}) matches_backup=1", text):
        key = phase + "/" + off
        if key in protected:
            raise TrialError("duplicate protected-region evidence")
        protected[key] = (int(size, 16), digest)
    for index, (off, size) in enumerate(((0, 0x20000), (0x180000, 0x20000), (0x1a0000, 0x60000), (0xb00000, 0x400000))):
        for phase in ("before", "after"):
            if protected.get(phase + "/" + format(off, "x")) != (size, manifest["protected_sha256"][index]):
                raise TrialError("missing/mismatched protected-region evidence")
    ready = re.search(r"BW_INSTALL_RAM_READY original_sha256=([0-9a-f]{64}) candidate_sha256=([0-9a-f]{64}) package_sha256=([0-9a-f]{64}) package_bytes=(\d+) bound_ms=(\d+) uptime_ms=(\d+)", text)
    if not ready or ready.group(1, 2, 3) != tuple(manifest[k] for k in ("original_sha256", "candidate_sha256", "package_sha256")) or int(ready[4]) != manifest["package_bytes"]:
        raise TrialError("RAM candidate/package/recovery reconstruction was not verified")
    release = re.search(r"BW_INSTALL_NAND_RELEASE rc=0 reads=(\d+) corrected_reads=(\d+) erases=11 pages=(\d+) skipped_ff=(\d+) max_read_ms=(\d+) max_bad_ms=(\d+) max_erase_ms=(\d+) max_page_ms=(\d+) failed=0", text)
    if not release or int(release[1]) < 90 or int(release[3]) + int(release[4]) != 704:
        raise TrialError("full native NAND ECC/read/erase/page preflight did not complete")
    erases = re.findall(r"BW_NAND_DRY_END rc=0 kind=erase offset=([0-9a-f]+) wren=1 loads=0 executes=1 hardware_mutations=0", text)
    if [int(x, 16) for x in erases] != list(range(0x20000, 0x180000, 0x20000)):
        raise TrialError("native erase preflight did not cover exactly the fixed 11 blocks")
    required = ("BW_INSTALL_PREFLIGHT_CURRENT sha256=" + manifest["original_sha256"] + " unchanged=1 candidate_readback_claimed=0",
                "BW_INSTALL_EXIT rc=0", "stack_ok=1 authorization_consumed=0", "writes=0")
    if not all(s in text for s in required) or not re.search(r"BW_BOUNDED_SPI_DETACHED rc=0 .* denied=0 pointer_restored=1", text):
        raise TrialError("installer/SPI return or unchanged original was not verified")
    if re.search(r"BW_(?:INSTALL_REFUSED|INSTALL_ECC_ERROR|BOUNDED_SPI_ERROR|BOUNDED_SPI_DENIED|NAND_PREFLIGHT_DENIED|NAND_REAL)", text):
        raise TrialError("native failure/real mutation marker in preflight")
    return {"protected_hashes": protected, "native_ecc_reads": int(release[1]), "corrected_reads": int(release[2]),
            "emulated_pages": int(release[3]), "skipped_ff_pages": int(release[4]),
            "observed_max_ms": dict(zip(("read", "bad", "erase", "page"), map(int, release.group(5, 6, 7, 8)))),
            "budget_bound_ms": int(ready[5]), "timing_bound_verified":False,
            "candidate_readback_verified":False,"pre_erase_uptime_ms": int(ready[6]), "hardware_mutations": 0}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--build-dir", type=Path, required=True)
    p.add_argument("--next-natural-after", type=Path,help="required periodic20m anchor; not used by the separately confirmed continuous-power mode")
    p.add_argument("--confirm-continuous-power",action="store_true",help="operator confirms smart-switch cutoff is PAUSED and power stays on; never infer this from a build flag")
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--port", default="COM3")
    p.add_argument("--execute-native-install",action="store_true",help="DESTRUCTIVE: use ONLY the separately accepted final installer; no retries or active reboot")
    a = p.parse_args()
    a.output_dir.mkdir(parents=True, exist_ok=False)
    report = {"schema": "sk-d840n-native-installer-preflight-v1", "utc": datetime.now(timezone.utc).isoformat(),
              "nand_written": False, "installer_preflight_pass": False, "events": []}
    if a.execute_native_install:report["schema"]="sk-d840n-native-installer-actual-v1"
    started = time.monotonic(); port = console = hook = None; cache_touched = False; status = 1;authorization=None
    with (a.output_dir / "rx.bin").open("xb") as raw, (a.output_dir / "events.jsonl").open("x", encoding="utf-8", newline="\n") as log:
        lines = bytearray()
        def emit(event, **fields):
            if authorization and "text" in fields:fields["text"]=fields["text"].replace(authorization,"<one-use-native-authorization>")
            item = {"event": event, "elapsed": round(time.monotonic() - started, 3), **fields}
            report["events"].append(item); line = json.dumps(item)
            log.write(line + "\n"); log.flush(); print(line, flush=True)
        def record(data):
            raw.write(data); raw.flush(); lines.extend(data)
            while b"\n" in lines:
                line, _, rest = lines.partition(b"\n"); lines[:] = rest
                if line.startswith((b"BW_INSTALL_", b"BW_BOUNDED_", b"BW_NAND_")):
                    emit("native_progress", text=line.strip(b"\r").decode("ascii", errors="replace"))
            if len(lines) > 8192: del lines[:-8192]
        def cache(name, on):
            console._exchange(name + (" on" if on else " off"), 10)
            if ("Cache is ON" if on else "Cache is OFF") not in console.inspect(name):
                raise TrialError("cache state/readback mismatch")
        try:
            manifest_file = a.build_dir / "manifest.json"
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            continuous=manifest.get("power_mode")=="continuous_required"
            if continuous!=a.confirm_continuous_power:
                raise TrialError("continuous-power build and explicit operator power confirmation must both be present")
            if continuous and a.next_natural_after:
                raise TrialError("continuous mode requires a newly observed boot, not an old periodic session")
            if a.execute_native_install:authorization=final_evidence(manifest)
            elif manifest.get("mode") != "preflight" or manifest.get("native_write_enabled") is not False:
                raise TrialError("default mode supports only immutable nonwriting preflight")
            payload = (a.build_dir / "native-installer.bin").read_bytes()
            elf = (a.build_dir / "native-installer.elf").read_bytes()
            if hashlib.sha256(payload).hexdigest() != manifest["binary_sha256"] or hashlib.sha256(elf).hexdigest() != manifest["elf_sha256"]:
                raise TrialError("installer build fingerprint mismatch")
            report["instruction_audit"] = verify_payload(elf, payload, base=0x89400000, limit=0x89500000)
            report.update({k: manifest[k] for k in ("candidate_sha256", "original_sha256", "package_sha256", "resident_manifest_sha256")})
            report["installer_manifest_sha256"] = hashlib.sha256(manifest_file.read_bytes()).hexdigest()
            consumed=a.build_dir/"installation-authorization-consumed.json"
            if a.execute_native_install and consumed.exists():raise TrialError("one-use installation authorization already consumed; no retry")
            sources=manifest["source_paths"];backup=Path(sources["current_backup"]).read_bytes()
            if len(backup)!=0x10000000 or hashlib.sha256(backup).hexdigest()!=manifest["backup_sha256"]:
                raise TrialError("bound full current backup changed")
            source=backup[:0x200000];identity=Core().resident_identity(source[0x20000:0x180000])
            if identity.installed_sha256.decode()!=manifest["original_sha256"]:
                raise TrialError("installer source is not the bound current image")
            command_name,alias=command_record(source,0x9ff3c000,resident_dir=sources.get("current_resident_dir"))
            if (manifest.get("runtime_command_name",command_name)!=command_name or
                    manifest.get("runtime_command_record_sha256",hashlib.sha256(alias).hexdigest())!=hashlib.sha256(alias).hexdigest()):
                raise TrialError("current resident command ABI binding changed")
            not_before=None
            if not continuous:
                if not a.next_natural_after:raise TrialError("periodic mode requires its natural-cycle anchor")
                old = json.loads(a.next_natural_after.read_text(encoding="utf-8"))
                boot = next(e for e in old["events"] if e["event"] == "boot_detected")
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(old["utc"])).total_seconds() - boot["elapsed"]
                if not clean_natural_anchor(old) or not 0 <= age < 1230: raise TrialError("invalid natural-cycle anchor")
                not_before=time.monotonic()+max(0,1230-age-3)
                emit("natural_cycle_guard",seconds_until_earliest_boot=round(max(0,1230-age-3),3),queued_commands=False)
            else:
                emit("continuous_power_confirmed",operator_acknowledged=True,queued_commands=False,new_boot_required=True)
            port = open_serial(a.port); console = Console(port, emit=emit, raw=record,continuous_power_confirmed=continuous)
            console.wait_boot_prompt(1260, not_before=not_before)
            observed = next(e for e in report["events"] if e["event"] == "boot_detected")
            report["natural_boot_utc"] = (datetime.fromisoformat(report["utc"]) + timedelta(seconds=observed["elapsed"])).isoformat()
            _, _, board = console.identify(); reloc = field(board, "relocaddr")
            if reloc != 0x9ff3c000 or field(board, "reloc off") != 0x17f3c000: raise TrialError("unknown runtime relocation")
            banks, reserved = runtime_map(board)
            reserved += [Span(0x90000000, 0x1000000, "DMA-low-guard"), Span(0x93000000, 0x1000000, "DMA-high-guard")]
            allocations = [Span(0x89400000, 0x100000, "installer-code-scratch-stack"), Span(0x8a000000, 0x200000, "package"),
                           Span(0x8a200000, 0x160000, "original-recovery"), Span(0x89000000, 0x10000, "minimal-probe")]
            validate_placement(banks, reserved, allocations); report["ram_map"] = [vars(s) for s in [*banks, *reserved, *allocations]]
            def read(at, n): return parse_md(console.inspect(f"md.b {at:x} {n:x}", timeout=15), at, n)
            for off, n in sorted(set(RAM_PINS + SPI_PINS + NAND_WRITE_PINS + [(0x9084, 0xa0), (0xa098, 16), (0xa270, 0x84)])):
                if read(reloc + off, n) != source[0x20000 + off:0x20000 + off + n]: raise TrialError("runtime pin differs: " + hex(off))
            address = reloc + 0x96c50
            if read(address, 56) != alias or struct.unpack_from("<Q", read(field(board, "TLB addr"), 32), 16)[0] != 0x80000711:
                raise TrialError("command ABI/translation root differs")
            def write_pointer(at, data):
                if at != address + 24 or len(data) != 8: raise TrialError("non-alias RAM write rejected")
                console._exchange(f"mw.q {at:x} {struct.unpack('<Q', data)[0]:x} 1", 10)
            for name in ("dcache", "icache"):
                if "Cache is ON" not in console.inspect(name): raise TrialError("unmeasured initial cache state")
            cache_touched = True; cache("dcache", False); cache("icache", False)
            probe = build_probe(a.output_dir, source, reloc, 0x89000000,expected_record=alias,command_name=command_name)
            console.loadx(probe, allocations[3], banks=banks, reserved=reserved, other_allocations=allocations[:3])
            hook = AliasHook(console, address, alias, read_memory=read, write_pointer=write_pointer); hook.install(0x89000000)
            text = console._exchange(command_name, 15)
            if "BW_RAM_PROBE v1 " not in text or "argc=1 RETURNING" not in text: raise TrialError("minimal probe ABI return failed")
            hook.restore(); report["ram_probe_pass"] = True
            console.loadx(payload, allocations[0], banks=banks, reserved=reserved, other_allocations=allocations[1:])
            if not continuous and time.monotonic() - console.window.started >= 220: raise TrialError("fresh cycle too old; no installer execution")
            hook.install(0x89400000)
            if a.execute_native_install:
                with consumed.open("x",encoding="utf-8",newline="\n") as ledger:
                    json.dump({"utc":datetime.now(timezone.utc).isoformat(),"candidate_sha256":manifest["candidate_sha256"],
                        "installer_manifest_sha256":report["installer_manifest_sha256"],
                        "authorization_sha256":hashlib.sha256(authorization.encode()).hexdigest(),
                        "natural_boot_utc":report["natural_boot_utc"],"operation":"one-use native installation; never replay"},ledger,indent=2)
                    ledger.write("\n");ledger.flush()
                report["nand_written"]=None;report["native_install_command_issued"]=True
                text=console._exchange(command_name+" install "+authorization+(" continuous-power" if continuous else ""),900)
                report["native_console"]=text.replace(authorization,"<one-use-native-authorization>")
                report.update(installation_evidence(text,manifest));report["nand_written"]=True
            else:
                text = console._exchange(command_name+" preflight", 180)
                report["native_console"] = text; report.update(preflight_evidence(text, manifest)); report["native_preflight_pass"] = True
        except (Exception, KeyboardInterrupt) as error:
            report["error"] = str(error) or type(error).__name__; emit("installer_preflight_stopped", reason=report["error"])
        finally:
            try:
                if hook and hook.armed: hook.restore(); report["alias_restored"] = True
                if cache_touched:
                    if not console or not console.identified or not console.prompt: raise TrialError("session lost; restoration replay forbidden")
                    cache("icache", True); cache("dcache", True); report["cache_restored"] = True
            except (OSError, TrialError) as error: report["cleanup_error"] = str(error); emit("cleanup_failed", reason=str(error))
            if port: port.close()
            report["installer_preflight_pass"] = all(report.get(k) is True for k in ("native_preflight_pass", "ram_probe_pass", "alias_restored", "cache_restored"))
            report["installer_actual_pass"]=a.execute_native_install and all(report.get(k) is True for k in ("actual_write_and_readback_pass","ram_probe_pass","alias_restored","cache_restored"))
            if report["installer_preflight_pass"] or report["installer_actual_pass"]: status = 0
            (a.output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return status


if __name__ == "__main__": raise SystemExit(main())
