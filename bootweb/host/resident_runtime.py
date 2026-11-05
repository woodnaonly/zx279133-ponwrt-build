"""Derive the live command record from a VERIFIED installed resident artifact.

No guessed name/usage pointers, serial IO or flash mutation is exposed here.
"""
import hashlib
import json
from pathlib import Path
import struct

from host.core import BASE,Core
from host.resident import SLOT_OFFSET,DESCRIPTOR_OFFSET
from host.uart import TrialError
from tools.audit_ram_elf import verify_payload


def command_record(current_boot,reloc,*,resident_dir=None,core=None):
    if len(current_boot)!=0x200000 or reloc!=0x9ff3c000:
        raise TrialError("command record requires the exact current boot and measured relocation")
    stage2=current_boot[0x20000:0x180000];core=core or Core()
    identity=core.resident_identity(stage2)
    record=bytearray(stage2[0x96c50:0x96c88])
    for at in (0,16,24,32,40,48):
        value=struct.unpack_from("<Q",record,at)[0]
        if value:
            struct.pack_into("<Q",record,at,value+reloc-0x88000000)
    if identity.installed_sha256.decode()==BASE:
        return "?",bytes(record)
    if resident_dir is None:
        raise TrialError("installed resident requires its exact previous ELF/manifest")
    path=Path(resident_dir);manifest=json.loads((path/"manifest.json").read_text(encoding="utf-8"))
    tail=(path/"resident-tail-ram-trial.bin").read_bytes()
    if (manifest.get("installed_sha256")!=identity.installed_sha256.decode() or
            manifest.get("tail_sha256")!=hashlib.sha256(tail).hexdigest() or
            not tail or len(tail)>DESCRIPTOR_OFFSET-SLOT_OFFSET or
            stage2[SLOT_OFFSET:SLOT_OFFSET+len(tail)]!=tail):
        raise TrialError("previous resident artifacts do not match the current installed image")
    bootstrap=(path/"resident-bootstrap.bin").read_bytes();elf=(path/"resident-bootstrap.elf").read_bytes()
    audit=verify_payload(elf,bootstrap,base=0x89100000,limit=0x891e0000)
    padded=bootstrap+bytes((-len(bootstrap))%64)
    if (manifest.get("bootstrap_audit",{}).get("payload_sha256")!=audit["payload_sha256"] or
            manifest.get("bootstrap_audit",{}).get("elf_sha256")!=audit["elf_sha256"] or tail.count(padded)!=1):
        raise TrialError("previous resident bootstrap does not match the installed tail")
    def literal(value):
        # Zig release ELFs may omit local symbols. Resolve a UNIQUE exact
        # NUL-terminated literal in the ELF-bound installed bootstrap, then
        # require the complete live record to match before any pointer write.
        # No RAM address is invented or learned from an untrusted live pointer.
        if bootstrap.count(value)!=1:
            raise TrialError("installed resident command literal is missing/ambiguous")
        return 0x89100000+bootstrap.index(value)
    struct.pack_into("<Q",record,0,literal(b"bootweb\0"))
    struct.pack_into("<I",record,8,5)
    struct.pack_into("<Q",record,24,0x89100000)
    usage=(b"resident Web; updates require native backup and timing gates\0" if manifest.get("native_callbacks_compiled") is True
           else b"resident Web development; NAND writes disabled\0")
    struct.pack_into("<Q",record,32,literal(usage))
    struct.pack_into("<Q",record,40,literal(b"[duration-ms] | trial <host-challenge> <duration-ms>\0"))
    return "bootweb",bytes(record)
