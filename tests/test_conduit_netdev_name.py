#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Negative controls for the conduit netdev name gate.

The gate resolves the netdev name the way the kernel does, and compares the
result with the name userland reads.  A gate that only greps for the format
string would pass on a driver that the kernel refuses to register at all, so
every way of getting this wrong has to turn the gate red:

  * a name without a format specifier is published as it stands,
  * a name with two format specifiers is refused with -EINVAL,
  * a name with a specifier the kernel does not know is refused with -EINVAL,
  * a driver that never registers a netdev publishes nothing,
  * a driver that names the netdev twice is ambiguous,
  * a unit id that is already taken moves the name to the next free one.

The controls run against the vendored patch that creates the driver, so the
thing under test is the artefact that is compiled, not a copy of it.
"""
import argparse
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / 'tools/check-conduit-netdev-name.py'
PATCH = ROOT / 'kernel-patches/7.3/390-net-zx279133-lan-conduit.patch'
SEED = 'strscpy(ndev->name, "lan-cpu%d", IFNAMSIZ);'

spec = importlib.util.spec_from_file_location('conduit_gate', GATE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def run(source, expected=None, taken=()):
    """Run the gate over a source text; return (rc, output)."""
    with tempfile.TemporaryDirectory(prefix='sk-conduit-') as tmp:
        path = Path(tmp) / 'driver.c'
        path.write_text(source, encoding='utf-8', newline='\n')
        command = [sys.executable, str(GATE), '--source', str(path)]
        if expected is not None:
            command += ['--expected', expected]
        for name in taken:
            command += ['--taken', name]
        result = subprocess.run(command, capture_output=True, text=True)
        return result.returncode, result.stdout + result.stderr


def driver(replacements=(), extra='', drop_register=False):
    """A minimal driver, shaped like the one the tree carries."""
    body = SEED
    for old, new in replacements:
        body = body.replace(old, new)
    text = (
        '/* SPDX-License-Identifier: GPL-2.0-only */\n'
        '#include <linux/netdevice.h>\n'
        '\n'
        'static int probe(struct platform_device *pdev)\n'
        '{\n'
        '\tstruct net_device *ndev;\n'
        '\n'
        '\tndev = devm_alloc_etherdev_mqs(pdev->dev, 0, 1, 1);\n'
        '\tSET_NETDEV_DEV(ndev, &pdev->dev);\n'
        f'\t{body}\n'
        + (extra or '')
        + ('\tret = devm_register_netdev(&pdev->dev, ndev);\n'
           if not drop_register else '')
        + '\treturn 0;\n'
        '}\n'
    )
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    failures = []

    def check(name, condition, detail=''):
        if condition:
            print(f'PASS: {name}')
        else:
            print(f'FAIL: {name}: {detail}')
            failures.append(name)

    # The vendored patch that creates the driver, as it is committed.
    text, kind = gate.source_text(PATCH)
    check('the vendored patch is read as a patch', kind == 'patch', kind)
    rc, output = run(text)
    check('the vendored patch publishes lan-cpu0', rc == 0, output)
    check('the gate says which name it resolved', 'lan-cpu0' in output, output)

    # The same driver spelled the other way round.
    rc, output = run(driver(replacements=(('strscpy(ndev->name, "lan-cpu%d", IFNAMSIZ);',
                                           'snprintf(ndev->name, IFNAMSIZ, "lan-cpu%d", 0);'),)))
    check('snprintf() with an explicit unit is accepted', rc == 0, output)

    # The name the kernel would publish, computed the kernel's way.
    check('the kernel resolves lan-cpu%d to lan-cpu0',
          gate.resolve_published_name('lan-cpu%d') == 'lan-cpu0')
    check('a taken unit id moves the name on',
          gate.resolve_published_name('lan-cpu%d', ['lan-cpu0']) == 'lan-cpu1')
    check('a name without a format specifier is published as it stands',
          gate.resolve_published_name('lan-cpu') == 'lan-cpu')

    # The format strings the kernel refuses outright, with -EINVAL, before
    # any name is published.  These have to be refused rather than published
    # as something else: the driver would not register at all, and the board
    # would have no conduit.
    for bad in ('lan-cpu%d%d', 'lan-cpu%u', 'lan-cpu%d%s', 'lan-cpu%'):
        try:
            gate.resolve_published_name(bad)
        except gate.NameRejected:
            check(f'the kernel refuses {bad}', True)
        else:
            check(f'the kernel refuses {bad}', False,
                  'the gate accepted a name the kernel would refuse')

    # Negative controls: each of these must turn the gate red.
    controls = [
        ('a name without a format specifier',
         driver(replacements=(('"lan-cpu%d"', '"lan-cpu"'),)), None, ()),
        ('two format specifiers',
         driver(replacements=(('"lan-cpu%d"', '"lan-cpu%d%d"'),)), None, ()),
        ('a specifier the kernel does not know',
         driver(replacements=(('"lan-cpu%d"', '"lan-cpu%u"'),)), None, ()),
        ('a driver that never registers the netdev',
         driver(drop_register=True), None, ()),
        ('a driver that names the netdev twice',
         driver(extra='\tstrscpy(ndev->name, "lan-cpu1", IFNAMSIZ);\n'), None, ()),
        ('a unit id that is already taken',
         driver(), 'lan-cpu0', ('lan-cpu0',)),
        ('an expected name nobody reads',
         driver(), 'lan-cpu9', ()),
    ]
    for name, source, expected, taken in controls:
        rc, output = run(source, expected, taken)
        check(f'the gate rejects {name}', rc != 0, output)

    # And the gate must not be able to be satisfied by an empty file.
    rc, output = run('')
    check('the gate rejects an empty source', rc != 0, output)

    if failures:
        print(f'{len(failures)} control(s) failed')
        return 1
    print('PASS: the conduit netdev name gate has teeth')
    return 0


if __name__ == '__main__':
    sys.exit(main())
