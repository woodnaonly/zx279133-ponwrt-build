#!/usr/bin/env python3
# Read the ABI out of the vendor PON modules we copied from the running system.
# These are 4.19 BSP binaries and nobody has their source, so the strings that
# name device nodes, ioctls, DT compatibles, firmware files and exported symbols
# ARE the documentation - this turns each .ko into that summary.
#
#	python tools/ponabi.py > pon/abi-scan.txt
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, 'pon', 'vendor-modules')

# Only printable runs of >= 5 chars, like `strings`.
RUN = re.compile(rb'[ -~]{5,}')

GROUPS = {
    'dt-compatible': re.compile(r'^[a-z0-9]+,[a-z0-9\-]+$'),
    'dev-or-proc-node': re.compile(r'(^/dev/)|(^/proc/)|(^/sys/)|gpondrv|ponsdk|switch_dev|ethdriver|pon_reg'),
    'ioctl-ish': re.compile(r'(_IOC|_IOW|_IOR|_IO\(|ioctl|IOCTL|CMD_[A-Z]|_CMD)'),
    'firmware': re.compile(r'^/lib/firmware|\.bin$|_fw|firmware'),
    'exported-symbol': re.compile(r'^[a-z][a-z0-9_]{4,}$'),
    'omci-or-me': re.compile(r'(?i)omci|mib|\bME_|me_id|mpcp|gem'),
    'module-param': re.compile(r'^parm[:=]'),
}


def strings(path):
    data = open(path, 'rb').read()
    return [m.group().decode('ascii', 'replace') for m in RUN.finditer(data)]


def classify(items):
    out = {}
    for s in items:
        if GROUPS['module-param'].match(s):
            out.setdefault('module-param', []).append(s)
        if GROUPS['omci-or-me'].search(s) and len(s) < 120:
            out.setdefault('omci-or-me', []).append(s)
        if GROUPS['dt-compatible'].match(s) and ',' in s:
            out.setdefault('dt-compatible', []).append(s)
        if GROUPS['dev-or-proc-node'].search(s) and len(s) < 100:
            out.setdefault('dev-or-proc-node', []).append(s)
        if GROUPS['ioctl-ish'].search(s) and len(s) < 100:
            out.setdefault('ioctl-ish', []).append(s)
        if GROUPS['firmware'].search(s) and len(s) < 60:
            out.setdefault('firmware', []).append(s)
    for k in out:
        out[k] = sorted(set(out[k]))
    return out


ORDER = ['np_133', 'zte_xgpon', 'gpondrv_xg', 'zx_ponreg_133', 'switch', 'ffe',
         'wan_bridge', 'netdriver', 'optical', 'multicast', 'GN28L95', 'GN28L96',
         'bspdriver', 'qos_mod', 'region_init', 'download', 'upload_optimize',
         'rlt8226b']

for name in ORDER:
    p = os.path.join(DIR, name + '.ko')
    if not os.path.isfile(p):
        print('== %s: NOT COPIED ==' % name)
        continue
    items = strings(p)
    groups = classify(items)
    print('=' * 72)
    print('%s.ko  (%d bytes, %d strings)' % (name, os.path.getsize(p), len(items)))
    for key in ['dt-compatible', 'dev-or-proc-node', 'ioctl-ish', 'omci-or-me',
                'firmware', 'module-param']:
        v = groups.get(key, [])
        if not v:
            continue
        print('  -- %s (%d)' % (key, len(v)))
        for s in v[:40]:
            print('     %s' % s[:110])
        if len(v) > 40:
            print('     ... %d more' % (len(v) - 40))
