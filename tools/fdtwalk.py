#!/usr/bin/env python3
"""Minimal flattened-device-tree reader, with a self-test built in.

Why this exists as a shared tool: the LED wiring was checked three times in one session by
three separate ad-hoc parsers, and two of them were broken while reporting confidently. One
looked for `led` with `want in path_l` - an exact list membership test - against a path element
named `leds`, concluded "the panel beacon cannot work", and was wrong. The third found the
seven LEDs and was validated against a node known to be present.

So the parser carries its own calibration and refuses to answer without it:

  python3 tools/fdtwalk.py <dtb>                       # every node and property
  python3 tools/fdtwalk.py <dtb> --leds                # the /leds subtree, decoded
  python3 tools/fdtwalk.py <dtb> --node /soc/gpio@...  # one node

The self-test runs on every invocation: the walker must find the FDT root and must be able to
see at least one child node. A walker that sees nothing reports that fact instead of an answer.
"""
import struct
import sys

FDT_BEGIN_NODE = 1
FDT_END_NODE = 2
FDT_PROP = 3
FDT_NOP = 4
FDT_END = 9


class FDT:
    def __init__(self, blob):
        self.blob = blob
        if len(blob) < 40:
            raise ValueError('blob is %d bytes - too short to be an FDT' % len(blob))
        h = struct.unpack('>10I', blob[:40])
        self.magic = h[0]
        if self.magic != 0xd00dfeed:
            raise ValueError('bad FDT magic 0x%08x' % self.magic)
        self.totalsize = h[1]
        self.off_struct = h[2]
        self.off_strings = h[3]
        self.version = h[5]
        self.size_strings = h[8]
        self.size_struct = h[9]
        self.strings = blob[self.off_strings:self.off_strings + self.size_strings]
        self.S = blob[self.off_struct:self.off_struct + self.size_struct]
        self.nodes = []          # list of (path, props dict)
        self._walk()

    def s_at(self, o):
        e = self.strings.index(b'\x00', o)
        return self.strings[o:e].decode('utf-8', 'replace')

    def _walk(self):
        p = 0
        stack = []
        cur = None
        while p < len(self.S):
            (tok,) = struct.unpack('>I', self.S[p:p + 4])
            p += 4
            if tok == FDT_BEGIN_NODE:
                e = self.S.index(b'\x00', p)
                nm = self.S[p:e].decode('utf-8', 'replace')
                p = (e + 4) & ~3
                stack.append(nm)
                path = '/' + '/'.join(x for x in stack if x)
                cur = {'path': path, 'props': {}}
                self.nodes.append(cur)
            elif tok == FDT_END_NODE:
                stack.pop()
                cur = None
            elif tok == FDT_PROP:
                plen, noff = struct.unpack('>II', self.S[p:p + 8])
                p += 8
                val = self.S[p:p + plen]
                p = (p + plen + 3) & ~3
                if cur is not None:
                    cur['props'][self.s_at(noff)] = val
            elif tok == FDT_NOP:
                pass
            elif tok == FDT_END:
                break
            else:
                raise ValueError('unknown FDT token %d at struct offset %d' % (tok, p - 4))

    def selftest(self):
        """Refuse to answer unless the walk is demonstrably working."""
        paths = [n['path'] for n in self.nodes]
        problems = []
        if '/' not in paths:
            problems.append('no root node - the walk is broken')
        if len(paths) < 2:
            problems.append('only %d node(s) - a device tree has a root and children' % len(paths))
        if not any('/' in p for p in paths):
            problems.append('no node below the root - the walk is broken')
        return paths, problems


def decode_str(b):
    return b.rstrip(b'\x00').decode('utf-8', 'replace')


def show(fdt, path):
    for n in fdt.nodes:
        if n['path'] == path:
            print('  %s' % path)
            for k, v in n['props'].items():
                if k in ('gpios', 'interrupts', 'clocks', 'reg', 'phys', 'reset-names'):
                    print('    %-20s  %s' % (k, ' '.join('%02x' % b for b in v[:36])))
                else:
                    print('    %-20s  %r' % (k, decode_str(v)))
            return True
    return False


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    try:
        fdt = FDT(open(argv[1], 'rb').read())
    except Exception as e:
        print('cannot read %s: %s' % (argv[1], e), file=sys.stderr)
        return 1

    paths, problems = fdt.selftest()
    print('=== self-test ===')
    print('  FDT totalsize=%d version=%d nodes=%d' % (fdt.totalsize, fdt.version, len(paths)))
    for p in problems:
        print('  FAILED: %s' % p)
    if problems:
        print('  refusing to report a verdict from a walker that does not work', file=sys.stderr)
        return 1
    print('  walker works (root and %d descendant nodes seen)' % (len(paths) - 1))

    arg = argv[2] if len(argv) > 2 else '--all'

    if arg == '--all':
        print()
        for n in fdt.nodes:
            print('  NODE %s' % n['path'])
            for k, v in n['props'].items():
                print('        .%-22s = %s' % (k, ' '.join('%02x' % b for b in v[:24])))
        return 0

    if arg == '--leds':
        # Substring match on the path, which is what the broken version got wrong: "led" is a
        # substring of "leds" and of "led-power", but is not equal to either.
        leds = [n for n in fdt.nodes if '/led' in n['path'] or n['path'].endswith('leds')]
        print()
        print('=== the /leds subtree ===')
        if not leds:
            print('  none - the panel beacon cannot work')
            return 1
        phandles = {}
        for n in fdt.nodes:
            ph = n['props'].get('phandle')
            if ph is not None and len(ph) == 4:
                phandles[struct.unpack('>I', ph)[0]] = n['path']
        print('  GPIO controllers this tree defines: %s'
              % ', '.join('%s=%s' % (hex(k), v) for k, v in sorted(phandles.items())))
        print()
        for n in leds:
            pr = n['props']
            print('  %s' % n['path'])
            if 'label' in pr:
                print('    %-16s %s' % ('label', decode_str(pr['label'])))
            if 'default-state' in pr:
                print('    %-16s %s' % ('default-state', decode_str(pr['default-state'])))
            if 'gpios' in pr:
                v = pr['gpios']
                if len(v) % 12 == 0:
                    for i in range(len(v) // 12):
                        ph, pin, flags = struct.unpack('>III', v[i * 12:(i + 1) * 12])
                        who = phandles.get(ph, 'PHANDLE 0x%x (UNRESOLVED)' % ph)
                        print('    %-16s [%d] pin=%-3d %-12s -> %s'
                              % ('gpios', i, pin,
                                 'active-low' if flags & 1 else 'active-high', who))
                else:
                    print('    %-16s %d bytes (not a 12-byte triple list)' % ('gpios', len(v)))
        return 0

    if arg == '--compat':
        # Every node's status and full compatible list, NUL-separated inside the property, so
        # a multi-string compatible is shown as separate entries rather than one blob. The CI
        # driver gate branches on the presence of a non-SoC fallback string, so collapsing them
        # here would hide exactly the thing that gate keys on.
        print()
        print('=== node | status | compatible (in order) ===')
        rows = 0
        for n in fdt.nodes:
            pr = n['props']
            if 'compatible' not in pr:
                continue
            status = decode_str(pr.get('status', b'')) or '(no status = okay)'
            names = [s.decode('utf-8', 'replace') for s in pr['compatible'].split(b'\x00') if s]
            for k, nm in enumerate(names):
                print('  %-44s %-8s [%d] %s' % (n['path'], status, k, nm))
                rows += 1
        print()
        print('  %d compatible strings' % rows)
        return 0

    if arg.startswith('--node='):
        target = arg.split('=', 1)[1]
        print()
        return 0 if show(fdt, target) else 1

    print('unknown option %r' % arg, file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
