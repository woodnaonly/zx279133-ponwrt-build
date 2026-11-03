#!/usr/bin/env python3
"""Fail-closed SK-D840N vendor U-Boot FIT/console checks; no device I/O.

The caller owns serial exclusivity, release SHA256, RAM/power checks, and the
physical boot identity. This module never enables flash writes or saves env.
"""
import re

BANNER = 'U-Boot 2021.01-svn308776 (Apr 17 2025 - 16:09:35 +0800)'
BOOT = 'bootm 0x89000000'
START = 'bootm start 0x89000000'
CLI_ERROR = re.compile(
    r'(?im)^\s*(?:Unknown command\b|##\s*Error\b|ERROR\b|Bad (?:Magic|Data|Hash|FIT|Image)\b|'
    r'Wrong Image Format\b|Could not find a valid\b|Trying to execute a command out of order\b|'
    r'Hash check failed\b|Verification failed\b|.*(?:Bad hash|hash.*mismatch).*)')
SEVERE = re.compile(
    r'\b(?:Kernel panic|Oops:|BUG:|Call Trace:|soft lockup|hard LOCKUP|Out of memory:)|'
    r'\bjffs2\b.*(?:error|corrupt|failed)', re.I)
JFFS_ISSUE = re.compile(
    r'(?i)\bjffs2\b.*(?:wrong data CRC|CLEANMARKER|Empty flash|error|corrupt|failed|Magic bitmask|CRC failed)')


def require(condition, message):
    # Safety checks must remain effective under python -O as well.
    if not condition:
        raise AssertionError(message)


def normalize(text):
    return text.replace('\r', '')


def check_response(cmd, output, complete=False, booting=False):
    text = normalize(output)
    require(not CLI_ERROR.search(text), 'U-Boot reported an error: ' + text[-2000:])
    if cmd != 'help bootm':
        require(not re.search(r'(?im)^\s*Usage:', text), 'U-Boot rejected command syntax')
    if cmd != BOOT:
        require(not re.search(r'(?i)Starting kernel|Uncompressing Kernel Image|Loading Device Tree to', text),
                'unexpected early kernel execution')
    if complete:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        require(bool(lines) and lines[0] == cmd, 'missing current command echo')
        if not booting:
            require(lines[-1] == '=>', 'current command did not return a U-Boot prompt')
    return text


def capabilities(output):
    text = check_response('help bootm', output, complete=True)
    require('Sub-commands to do part of the bootm sequence' in text, 'no staged bootm help')
    require(re.search(r'(?m)^\s*start \[addr \[arg \.\.\.\]\]', text), 'bootm start unavailable')


def fit_verified(cmd, output, image, booting=False):
    require(cmd == (BOOT if booting else START), 'unexpected FIT validation command')
    text = check_response(cmd, output, complete=True, booting=booting)
    require(re.findall(r"Using '([^']+)' configuration", text) == ['config-1', 'config-1'],
            'wrong, missing, or ambiguous FIT configuration')
    sections = list(re.finditer(r"Trying '([^']+)' (kernel|fdt) subimage", text))
    require([(m[1], m[2]) for m in sections] == [('kernel-1', 'kernel'), ('fdt-1', 'fdt')],
            'wrong FIT subimages')
    expected = image['hashes']
    require(len(expected) == 4, 'unexpected FIT hash manifest')
    reports = []
    for index, match in enumerate(sections):
        section = text[match.end():sections[index + 1].start() if index + 1 < len(sections) else len(text)]
        algorithms = re.findall(r'Hash algo:\s*(\S+)', section)
        digests = re.findall(r'Hash value:\s*([0-9a-f]+)', section)
        items = [item for item in expected if item['node'] == '/images/' + match[1]]
        wanted = {item['algorithm']: item['digest'] for item in items}
        require(len(algorithms) == len(digests) == len(wanted) == len(items) == 2, 'missing/duplicate FIT hashes')
        require(set(algorithms) == {'crc32', 'sha1'} and len(set(algorithms)) == 2, 'unexpected FIT hash algorithms')
        require(dict(zip(algorithms, digests)) == wanted, 'device FIT hash values differ from validated image')
        require(re.findall(r'Verifying Hash Integrity[^\n]*', section) ==
                ['Verifying Hash Integrity ... crc32+ sha1+ OK'], 'missing complete FIT hash verification')
        reports.append(dict(node=match[1], hashes=wanted, verified=True))
    require(len(re.findall(r'Verifying Hash Integrity', text)) == 2, 'ambiguous FIT verification count')
    if booting:
        require('Starting kernel' in text, 'kernel execution not observed')
    return reports


def crc_verified(output, image):
    size = image['bytes']
    require(isinstance(size, int) and 0 < size < 0x08000000, 'invalid FIT byte count')
    cmd = f'crc32 0x89000000 {size:x}'
    text = check_response(cmd, output, complete=True)
    matches = re.findall(r'crc32 for ([0-9a-f]+) \.\.\. ([0-9a-f]+) ==> ([0-9a-f]{8})', text)
    require(matches == [('89000000', f'{0x89000000 + size - 1:x}', image['crc32'])],
            'RAM CRC32 range or value differs from validated FIT')
    return text


def transaction_output(ticket, state, expected, booting=False):
    """Return only this transaction's completed response, or None; never tail."""
    tx = state.get('transaction')
    require(tx is not None and all(tx.get(k) == ticket.get(k) for k in ('id', 'cmd', 'start', 'boot_offset')),
            'serial transaction changed or was replayed')
    require(not tx.get('cancelled') and not tx.get('truncated'), 'serial transaction interrupted/truncated')
    require(state.get('boots') and state['boots'][-1]['offset'] == ticket['boot_offset'], 'unexpected U-Boot restart')
    require(0 <= ticket['start'] <= tx['end'] <= state['bytes_received'], 'invalid serial receive cursor')
    output = check_response(ticket['cmd'], tx['output'])
    if output:
        require(tx['end'] > ticket['start'], 'response predates current receive cursor')
    finished = 'Starting kernel' in output if booting else tx['done']
    if finished:
        check_response(ticket['cmd'], output, complete=True, booting=booting)
        require(expected in output, 'required response absent from this transaction: ' + expected)
        return output
    return None


def verified_boot(send, begin_boot, wait_boot, power_check, image):
    """Staged verification -> same RAM CRC -> power gate -> full re-verifying boot."""
    start = send(START, 'Verifying Hash Integrity', 30)
    start_images = fit_verified(START, start, image)
    crc = send(f"crc32 0x89000000 {image['bytes']:x}", '==> ' + image['crc32'])
    crc_verified(crc, image)
    power_check()
    ticket = begin_boot(BOOT)
    boot = wait_boot(ticket, 'Starting kernel', 45, booting=True)
    boot_images = fit_verified(BOOT, boot, image, booting=True)
    return dict(start_output=start, start_images=start_images, crc_output=crc,
                ticket=ticket, boot_output=boot, boot_images=boot_images)


def log_issues(logs):
    issues = {re.sub(r'^.*?(?=jffs2\b)', '', line, flags=re.I).strip()
              for line in logs.splitlines() if JFFS_ISSUE.search(line)}
    return {re.sub(r'(notice:)\s*\(\d+\)', r'\1 (<pid>)', line) for line in issues}


def verified_eof_warning(published_rootfs, live_marker):
    """Recognize only the exact EOF word in a separately SHA-verified image."""
    offset = len(published_rootfs) - 4
    require(offset > 0 and offset % 131072 == 0, 'EOF is not at an eraseblock boundary')
    require(published_rootfs[-4:] == live_marker == b'\xde\xad\xc0\xde', 'published/live JFFS2 EOF word mismatch')
    return f'jffs2: jffs2_scan_eraseblock(): Magic bitmask 0x1985 not found at 0x{offset:08x}: 0xadde instead'


def logs_clean(logs, baseline, verified_eof=None):
    require(not SEVERE.search(logs), 'new severe kernel/filesystem error')
    new = log_issues(logs) - log_issues(baseline)
    if verified_eof:
        new.discard(verified_eof)
    require(not new, 'new JFFS2 warnings: ' + repr(sorted(new)))


def boot_banners(search, absolute_start, last_offset):
    result = []
    for match in re.finditer(rb'U-Boot 20[0-9]{2}\.[0-9]{2}[^\r\n]*[\r\n]', search):
        offset = absolute_start + match.start()
        # A `version` response is not a physical boot. Require the measured
        # ROM/DDR preamble as well as the caller's later Ethernet OFF/ON proof.
        if offset > last_offset and b'ddr init ok' in search[max(0, match.start() - 200):match.start()]:
            result.append((offset, match.group().strip().decode('ascii', 'replace')))
    return result
