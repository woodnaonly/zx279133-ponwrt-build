#!/usr/bin/env python3
# Extract the Monitor netlink event ids the modules actually send.
#
# The vendor kernel's Monitor subsystem is the only kernel->userspace channel this 4.19
# BSP has, and its message ids are an enum that exists nowhere in a string table - only
# as immediates at the call sites. So disassemble every MontorSendMsg* call in a .ko and
# read the register back through the movs that fed it.
#
#	python tools/ponevents.py pon/vendor-modules/gpondrv_xg.ko
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ponelf import Ko, sec_off                                    # noqa: E402
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN       # noqa: E402

SENDERS = ('MontorSendMsg', 'MontorSendPriorMsg', 'MontorSendAnyMsg',
           'OtherMontorSendMsg', 'OmciMontorSendMsgBroad')
# MontorSendMsgInner's frame: nlmsghdr, then {u16 source, u16 event, u16 prio, u16 len}
ARG = {0: 'source', 1: 'portid', 2: 'event', 3: 'data', 4: 'len', 5: 'prio'}


def imm_before(insns, idx, reg):
    """Walk back over the register's last `mov wN, #imm`; None if it is not a constant."""
    for j in range(idx, max(-1, idx - 24), -1):
        i = insns[j]
        if i.mnemonic in ('mov', 'orr'):
            m = re.match(r'^%s, #0x([0-9a-f]+)$' % reg, i.op_str)
            if m:
                return int(m.group(1), 16)
            m = re.match(r'^%s, #(\d+)$' % reg, i.op_str)
            if m:
                return int(m.group(1))
        if i.mnemonic == 'movk':
            m = re.match(r'^%s, #0x[0-9a-f]+, lsl' % reg)
            if m:
                return None            # built up in pieces; not worth modelling
        if i.mnemonic in ('bl', 'blr'):
            return None
    return None


def main(path):
    ko = Ko(path)
    rel = {}
    for r in ko.relocs():
        if r['rsec'] == '.text':
            rel[r['off']] = r
    funcs = {f['value']: f['name'] for f in ko.funcs() if f['sec'] == '.text'}
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    text = ko.section('.text')
    print('# %s' % os.path.basename(path))
    print('# Monitor event ids read out of the call sites. The frame on the wire is')
    print('#   struct nlmsghdr { nlmsg_type = 25 } + { u16 source, u16 event, u16 prio,')
    print('#   u16 payload_len } + payload.')
    print('# bind proto 25 with nl_pid = 0x48a3 and the kernel flushes whatever it has')
    print('# cached to that portid the moment you bind.')
    print('%-10s %-38s %-8s %s' % ('sender', 'called from', 'event', 'source/prio/len'))
    rows = []
    for f in ko.funcs():
        if f['sec'] != '.text' or not f['size']:
            continue
        code = ko.d[text['off'] + f['value']:text['off'] + f['value'] + f['size']]
        insns = list(md.disasm(code, f['value']))
        for n, i in enumerate(insns):
            r = rel.get(i.address)
            if not r or i.mnemonic not in ('bl', 'b') or r['sym'] not in SENDERS:
                continue
            args = {}
            for k, name in ARG.items():
                v = imm_before(insns, n, 'w%d' % k)
                if v is not None:
                    args[name] = v
            rows.append((r['sym'], f['name'], args.get('event'), args))
    for sender, fn, event, args in sorted(rows, key=lambda x: (x[2] is None, x[2] or 0)):
        extra = ' '.join('%s=%s' % (k, v) for k, v in sorted(args.items())
                         if k not in ('event',))
        print('%-10s %-38s %-8s %s' % (sender.replace('Montor', 'Montor'), fn,
                                        event if event is not None else '?', extra))
    print('# %d call sites, %d with a constant event id' %
          (len(rows), len([x for x in rows if x[2] is not None])))


if len(sys.argv) < 2:
    sys.exit('usage: ponevents.py <file.ko> [...]')
for p in sys.argv[1:]:
    main(p)
