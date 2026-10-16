#!/usr/bin/env python3
# 本地排演套件（local rehearsal suite）：依赖 staging/ 下的真实构建产物，
# 在只 clone 本仓库的环境里跑不起来，也**没有**接进 CI。
# 为什么、怎么跑、缺的那个夹具从哪来：同目录 README.md。
"""Which PON tables does the prepared mainline tree program, and which does it not?

Rewrite of an earlier version of this file, after it produced two false positives that
would have carried a wrong conclusion into the write-up:

  * `t_cont` matched `reset_conT_CONTrol`, so the PON SERDES PHY driver scored 8 hits on
    "tcont" when it programs no T-CONT at all;
  * `\\bcos\\b` matched `SMCT_COS_TH0`, which is the select-block priority threshold -
    Ethernet QoS, not a PON T-CONT.

Every pattern is therefore anchored to a word boundary or to an identifier shape, and the
PPU vocabulary is confirmed non-zero in the one file that is supposed to talk about the
PPU. A zero printed next to that confirmation is a searched absence; a zero printed
without it is just an unchecked assumption.
"""
import os
import re
import sys

BASE = '/mnt/c/ugit/SK-D840N4/staging/diag-20261014/kernelsrc'

TARGETS = [
    ('NPPT MAC driver', os.path.join(BASE, 'drivers_net_ethernet_zte_zx279133-np.c'), True),
    ('NPPT probe', os.path.join(BASE, 'drivers_net_ethernet_zte_zx279133-main.c'), False),
    ('NPPT MAC core', os.path.join(BASE, 'drivers_net_ethernet_zte_zx279133-mac.c'), False),
    ('PON SERDES PHY', os.path.join(BASE, 'drivers_phy_zte_phy-zx279133-pon-serdes.c'), False),
    ('the dtb source', os.path.join(BASE, 'arch_arm64_boot_dts_zte_zx279133.dtsi'), False),
]

# Anchored. `tcont` as a word or inside an identifier; never `t_cont`, which is a substring
# of reset_control. `gemport` likewise, so gem_port and gemport both count and nothing else.
NEEDS = [
    ('LLID', r'\bLLID\b|\bllid\b|_llid|llid_'),
    ('T-CONT', r'\bTCONT\b|\bTCont\b|\btcont\b|_tcont|Tcont'),
    ('GEM port', r'\bgem_?port\b|\bGemPort\b|\bGEM_PORT\b'),
    ('alloc id', r'\balloc_?id\b|\bAllocId\b|\bALLOC_ID\b'),
    ('LPM', r'\bLPM\b|\blpm\b|_lpm'),
    ('pkt type table', r'\bpkt_?type\b|\bPktType\b|\bPKT_TYPE\b'),
    ('dup table', r'\bdup\b|\bDUP\b|dup_entry|_dup_'),
    ('PLOAM', r'\bPLOAM\b|\bploam\b'),
    ('OMCI', r'\bOMCI\b|\bomci\b'),
    ('OpState', r'Op ?State Machine|\bop_?state\b'),
]

PPU = re.compile(r'\bppu[A-Z_]|\bPPU\b|ZX279133_PPU', re.I)


def main():
    missing = []
    for label, path, is_loader in TARGETS:
        if not os.path.isfile(path):
            print('%-16s MISSING' % label)
            continue
        text = open(path, encoding='utf-8', errors='replace').read()
        ppu = len(PPU.findall(text))
        print('%-16s %7d B   ppu vocabulary: %d%s'
              % (label, len(text), ppu,
                 '   <- the microcode loader, so its zeros below are real' if is_loader else ''))
        if is_loader and ppu == 0:
            print('   CALIBRATION FAILED: the loader does not mention the PPU at all')
            return 1
        for name, pat in NEEDS:
            hits = re.findall(pat, text)
            print('   %s%-16s %d' % ('   ' if hits else ' ! ', name, len(hits)))
            if not hits:
                missing.append((label, name))
        print()
    print('=== per file: how many of the checked vocabularies are absent ===')
    for label, _, _ in TARGETS:
        absent = [n for l, n in missing if l == label]
        if absent:
            print('  %-16s %d absent: %s' % (label, len(absent), ', '.join(absent)))
    print('=== vocabulary with no hit in any file ===')
    print('  %s' % ', '.join(sorted({m[1] for m in missing})))
    return 0


if __name__ == '__main__':
    sys.exit(main())
