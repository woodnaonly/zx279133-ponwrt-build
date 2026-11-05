"""IDA 9.x batch script. Read-only input; results/database belong in .build/.

Seeds the measured ARM64 cmd_rep table, never invokes target code. All names
are static candidates, not verified runtime symbols. Run only on a fresh DB.
"""
import hashlib
import json
from pathlib import Path
import struct
import sys

import ida_auto
import ida_bytes
import ida_funcs
import ida_hexrays
import ida_ida
import ida_idp
import ida_nalt
import ida_name
import ida_segment
import ida_ua
import idc
import idautils

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.image import BOARD, LINK_DELTA, analyze, command_candidates, regions


def main():
    path = Path(ida_nalt.get_input_file_path())
    data = path.read_bytes()
    known = {"5b084490813b540c1a82ddf2e2d4de46080e6cfe47b15b2e6575bdf3fe198fe9",
             "250f8537cfd436a67196a263f4a39aeac36d894c27e5cf8d611c2ff856defd98"}
    if hashlib.sha256(regions(data)["stage2"]).hexdigest() not in known:
        raise ValueError("refusing unknown base")
    ida_auto.enable_auto(False)
    for ea in list(idc.Segments()) if hasattr(idc, "Segments") else []:
        ida_segment.del_segm(ea, ida_segment.SEGMOD_KILL)
    # idc.Segments is absent in some IDA versions.
    segment = ida_segment.get_first_seg()
    while segment:
        ida_segment.del_segm(segment.start_ea, ida_segment.SEGMOD_KILL)
        segment = ida_segment.get_first_seg()
    ida_ida.inf_set_app_bitness(64)
    stage1 = len(idc.ARGV) > 2 and idc.ARGV[2] == "stage1"
    start, end = (0, 0x20000) if stage1 else (0x88000000, 0x88160000)
    # Raw stage2 includes writable globals/BSS as well as instructions. Marking
    # the entire range CODE/RO makes Hex-Rays fold globals into nonsense values.
    if not ida_segment.add_segm(0, start, end, "stage2", "DATA"):
        raise RuntimeError("cannot map stage2")
    segment = ida_segment.getseg(start)
    ida_segment.set_segm_addressing(segment, 2)
    segment.perm = ida_segment.SEGPERM_READ | ida_segment.SEGPERM_WRITE | ida_segment.SEGPERM_EXEC
    ida_segment.update_segm(segment)
    ida_bytes.put_bytes(start, data[:0x20000] if stage1 else data[0x20000:0x180000])
    commands = [] if stage1 else command_candidates(data)
    if stage1 and hashlib.sha256(data[:0x20000]).hexdigest() != "d4d7a4bf90fc26cc61c76000bb272fac3113c14525ab7119c3d10fbd477231f4":
        raise ValueError("refusing unknown BTL code")
    seeds = ({0x800: "candidate_btl_start", 0x1858: "candidate_btl_main",
              0x1f54: "candidate_btl_spi_boot", 0x242c: "candidate_btl_nand_boot",
              0x21a8: "candidate_btl_nand_read", 0x2ea0: "candidate_btl_uart_receive",
              0x2aec: "candidate_btl_boot_parameter_read",
              0x9530: "candidate_btl_wps_path",
              0x4bb0: "candidate_btl_secure_selector",
              0x47ac: "candidate_btl_image_verify", 0x4bd4: "candidate_btl_cache_prepare",
              0x4c90: "candidate_btl_uart_boot", 0x40f4: "candidate_btl_printf"}
             if stage1 else {start: "candidate_stage2_start"})
    for cmd in commands:
        if cmd["name"] in {"tftpboot", "nand", "mtd", "bdinfo", "bootm", "loadx", "reset"}:
            seeds[int(cmd["handler_link_address"], 16)] = "candidate_cmd_" + cmd["name"]
            if cmd["name"] == "mtd":
                seeds[int(cmd["wrapper_link_address"], 16)] = "candidate_cmd_mtd_repeat"
    if not stage1 and hashlib.sha256(regions(data)["stage2"]).hexdigest() == "5b084490813b540c1a82ddf2e2d4de46080e6cfe47b15b2e6575bdf3fe198fe9":
        seeds.update({0x88008218: "candidate_netboot_common", 0x8801ce94: "candidate_nand_read_skip_bad",
                      0x8801cc68: "candidate_nand_write_skip_bad", 0x880192c0: "candidate_mtd_block_isbad",
                      0x8801c7fc: "candidate_nand_erase_opts", 0x88018eb4: "candidate_mtd_read_oob",
                      0x88018f5c: "candidate_mtd_write_oob", 0x8806677c: "candidate_printf",
                      0x880656dc: "candidate_udelay", 0x88008d88: "candidate_env_get",
                      0x88068fbc: "candidate_net_loop", 0x88068318: "candidate_net_init",
                      0x88067e84: "candidate_eth_halt", 0x88068140: "candidate_eth_set_current",
                      0x88067e00: "candidate_eth_init", 0x88067ee4: "candidate_eth_rx",
                      0x88065630: "candidate_get_timer"})
        seeds.update({0x8801aea0: "candidate_nand_device_by_index",
                      0x88018d34: "candidate_mtd_read_ecc",
                      0x88036f88: "candidate_board_eth_init",
                      0x88036dac: "candidate_board_eth_send",
                      0x88037364: "candidate_board_eth_recv",
                      0x88036fdc: "candidate_board_eth_halt"})
        seeds.update({0x88001958: "candidate_c_runtime_entry",
                      0x880019d8: "candidate_relocate_image",
                      0x88009fd0: "candidate_board_init_f",
                      0x8800a270: "candidate_board_init_r",
                      0x88018cc0: "candidate_mtd_erase",
                      0x88018dfc: "candidate_mtd_write_ecc",
                      0x88025824: "candidate_spi_erase_callback",
                      0x88025fa0: "candidate_spi_write_oob_callback",
                      0x880256c4: "candidate_spi_wait_ready",
                      0x8802564c: "candidate_spi_get_feature",
                      0x88025c58: "candidate_spi_program_page",
                      0x88025e80: "candidate_spi_ecc_config",
                      0x88025f1c: "candidate_spi_select_target",
                      0x8801a900: "candidate_nand_erase_block",
                      0x8801a9d4: "candidate_nand_erase_core",
                      0x88056cb8: "candidate_spi_memory_operation"})
    for ea, name in seeds.items():
        ida_name.set_name(ea, name, ida_name.SN_FORCE)
        ida_ua.create_insn(ea)
        ida_funcs.add_func(ea)
    ida_auto.enable_auto(True)
    ida_auto.auto_wait()
    # The normal NAND boot path is distinct from the UART signed-container
    # path. Discover callers/string references; do not transplant UART rules.
    if stage1:
        extra = {}
        for ea in (0x47ac, 0x4bd4, 0x4c90):
            for ref in idautils.CodeRefsTo(ea, False):
                owner = ida_funcs.get_func(ref)
                if owner and owner.start_ea not in seeds:
                    extra[owner.start_ea] = f"candidate_btl_caller_{owner.start_ea:x}"
        for item in idautils.Strings():
            if not any(word in str(item).lower() for word in ("nand", "uboot", "u-boot", "boot from", "jump")):
                continue
            for ref in idautils.DataRefsTo(item.ea):
                owner = ida_funcs.get_func(ref)
                if owner and owner.start_ea not in seeds:
                    extra[owner.start_ea] = f"candidate_btl_stringref_{owner.start_ea:x}"
        for ea, name in sorted(extra.items()):
            if len(seeds) >= 48:
                break
            ida_name.set_name(ea, name, ida_name.SN_FORCE)
            seeds[ea] = name
    report = analyze(data)
    report["ida_board"] = BOARD
    report["decompiler_available"] = bool(ida_hexrays.init_hexrays_plugin())
    pseudocode = {}
    calls = {}
    ranges = {}
    callers = {}
    if report["decompiler_available"]:
        for ea, name in seeds.items():
            try:
                function = ida_hexrays.decompile(ea)
                pseudocode[name] = str(function) if function else "no result"
            except Exception as error:
                pseudocode[name] = "decompile failed: " + str(error)
    for ea, name in seeds.items():
        owner = ida_funcs.get_func(ea)
        if owner:
            code = ida_bytes.get_bytes(owner.start_ea, owner.end_ea - owner.start_ea)
            ranges[name] = {"link_start": f"0x{owner.start_ea:x}", "link_end": f"0x{owner.end_ea:x}",
                            "static_region_sha256": hashlib.sha256(code).hexdigest() if code else None,
                            "runtime_verified": False}
        callers[name] = [f"0x{x:x}" for x in sorted(idautils.CodeRefsTo(ea, False))]
        refs = set()
        for item in idautils.FuncItems(ea):
            instruction = ida_ua.insn_t()
            if ida_ua.decode_insn(instruction, item) and instruction.get_canon_feature() & ida_idp.CF_CALL:
                refs.update(idautils.CodeRefsFrom(item, False))
        calls[name] = [f"0x{x:x}" for x in sorted(refs)]
    report["static_pseudocode"] = pseudocode
    report["static_direct_calls"] = calls
    report["static_function_regions"] = ranges
    report["static_caller_sites"] = callers
    output = Path(idc.ARGV[1]) if len(idc.ARGV) > 1 else ROOT / ".build/ida-report.json"
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    idc.qexit(0)


try:
    main()
except Exception as error:
    print("bootweb IDA audit:", repr(error))
    idc.qexit(1)
