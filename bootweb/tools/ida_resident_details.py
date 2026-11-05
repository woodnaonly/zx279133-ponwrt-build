"""Read-only follow-up query of a saved exact-baseline IDA database."""
import hashlib
import json
import struct
from pathlib import Path
import ida_auto
import ida_bytes
import ida_funcs
import ida_hexrays
import ida_ua
import idautils
import idc


def main():
    stage=ida_bytes.get_bytes(0x88000000,0x160000)
    if not stage or hashlib.sha256(stage).hexdigest()!="5b084490813b540c1a82ddf2e2d4de46080e6cfe47b15b2e6575bdf3fe198fe9":
        raise ValueError("unknown saved IDA stage2")
    addresses={0x88056968,0x88056970,0x88056978,0x88056cb8,0x88056e6c,
               0x880256c4,0x8802564c,0x88025824,0x88025b90,0x88025c58,0x88025f1c,
               0x88053554,0x8805355c,0x88053564,0x8805356c,0x88053918,0x88053738,
               0x8805370c,0x8804df40,0x8804de9c,0x88000b7c,
               0x88056788,0x88017a78,0x880178ec,0x88013cbc,
               0x88018cc0,0x88018dfc,0x88025824,0x88025fa0,0x88026220,
               0x88026678,0x88026608,0x88026558,0x8804dd24,0x8804df40,
               0x8804dd00,0x8804dcc4,0x8802788c,0x880279b8}
    addresses.update((0x88065630,0x88000cc4,0x88000cbc,0x88015904,0x88015b90))
    addresses.update((0x8800a098,0x8800a270,0x88009084))
    addresses.update((0x880522e4,0x88052354,0x880523ac,0x88052464,0x88052570,0x880525e8,0x880529c8,0x88052cb0))
    # Raw pointer tables in the vendor image are not all typed in this IDB.
    gpio_pointer=struct.pack("<Q",0x8804dd24)
    table_at=stage.find(gpio_pointer)
    if table_at<0:raise ValueError("GPIO input callback table missing")
    for at in range(max(0,table_at-32),table_at+32,8):
        target=struct.unpack_from("<Q",stage,at)[0]
        if 0x8804d000<=target<0x8804e000:addresses.add(target)
    for item in idautils.Functions():
        if any(s in idc.get_func_name(item) for s in ("nand_erase_core","nand_erase_block","spi_ecc_config")):
            addresses.add(item)
    strings=[]
    for item in idautils.Strings():
        value=str(item)
        if any(s in value.lower() for s in ("sky_gpio_get_value","random bytes","get_random","trng","rng","reset button","restore key","pinctrl-zx","gpio_request_enable")):
            refs=list(idautils.DataRefsTo(item.ea))
            strings.append({"address":hex(item.ea),"value":value,"refs":[hex(ea) for ea in refs]})
            for ref in refs:
                owner=ida_funcs.get_func(ref)
                if owner:addresses.add(owner.start_ea)
    for ea in addresses:
        ida_ua.create_insn(ea);ida_funcs.add_func(ea)
    ida_auto.auto_wait();ida_hexrays.init_hexrays_plugin()
    functions={}
    # Limited direct-call closure: native SPI implementations and GPIO/RNG.
    pending=sorted(addresses);visited=set()
    while pending and len(visited)<100:
        ea=pending.pop(0)
        owner=ida_funcs.get_func(ea)
        if not owner or owner.start_ea in visited:continue
        visited.add(owner.start_ea);calls=set()
        for at in idautils.FuncItems(owner.start_ea):
            if idc.print_insn_mnem(at)=="BL":calls.update(idautils.CodeRefsFrom(at,False))
        code=ida_bytes.get_bytes(owner.start_ea,owner.end_ea-owner.start_ea)
        try:pseudo=str(ida_hexrays.decompile(owner.start_ea))
        except Exception as error:pseudo=str(error)
        functions[hex(owner.start_ea)]={"end":hex(owner.end_ea),"sha256":hashlib.sha256(code).hexdigest(),
            "calls":[hex(a) for a in sorted(calls)],"pseudocode":pseudo,
            "disassembly":[[hex(at),idc.generate_disasm_line(at,0)] for at in idautils.FuncItems(owner.start_ea)]}
        for call in sorted(calls):
            if 0x88020000<=call<0x88060000 and call not in visited:pending.append(call)
    refs={hex(ea):[{"address":hex(at),"hex":ida_bytes.get_bytes(max(0x88000000,at-32),96).hex()} for at in idautils.DataRefsTo(ea)]
          for ea in (0x8804dd24,0x8804df40,0x88026678,0x88026558,0x88025fa0)}
    refs["raw_gpio_table"]={"input_pointer_at":hex(0x88000000+table_at),"nearby_hex":stage[table_at-48:table_at+48].hex()}
    Path(idc.ARGV[1]).write_text(json.dumps({"strings":strings,"functions":functions,"data_refs":refs},indent=2),encoding="utf-8")
    idc.qexit(0)


try:main()
except Exception as error:
    print("resident details:",repr(error));idc.qexit(1)
