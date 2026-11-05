#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Read SK-D840N LED evidence; opt in to a restored, fixed PHY LED experiment.

No UCI, network, GPIO, SerDes or image changes. The device-side guard attempts
and verifies restoration of the saved LED words if this process/SSH goes away.
Physical lamp identity is an observation, never inferred from a software ACK.

The channel experiment inverts one candidate polarity bit from upstream RTL8221B
support (Linux commit d3aae4d954f9), not a verified brightness field on this board.
That experiment does not clear hardware link/activity settings. The separately
authorized --hold-phy-led-level test clears only channel-0 link events (d032,
mask 0027) and activity (d040, bit 0), then selects d044 bit 0. It restores all
three original words and remains an observation, not a permanent LED backend.
The board has no LEDs in its RJ45 sockets; observe the front-panel lamps only.
"""
import argparse
from datetime import datetime
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
HELPER = Path(__file__).with_name('sk-led-phy-probe.sh')
ACKS = {
    'snapshot': r'SNAPSHOT verified=1',
    'arm': r'ARMED deadline=30 verified=1',
    'off': r'OFF value=0x0000 verified=1',
    'on': r'ON value=0x00ff verified=1',
    'restore': r'RESTORED value=0x[0-9a-f]{4} verified=1',
    'arm-level-0': r'ARMED deadline=60 channel=0 fields=d032,d040,d044 verified=1',
}
for level in ('off', 'on'):
    ACKS[f'level-{level}'] = (
        rf'LEVEL_CANDIDATE channel=0 level={level.upper()} '
        r'd032=0x[0-9a-f]{4} d040=0x[0-9a-f]{4} d044=0x[0-9a-f]{4} verified=1')
RESTORED_FIELDS = (r'RESTORED_FIELDS d032=0x[0-9a-f]{4} '
                   r'd040=0x[0-9a-f]{4} d044=0x[0-9a-f]{4} verified=1')
for channel in range(3):
    ACKS[f'arm-hold-{channel}'] = rf'ARMED deadline=60 channel={channel} verified=1'
    for phase in ('baseline', 'inverted'):
        ACKS[f'polarity-{channel}-{phase}'] = (
            rf'POLARITY channel={channel} phase={phase.upper()} '
            rf'mask=0x{1 << channel:04x} value=0x[0-9a-f]{{4}} verified=1')


class ProbeError(RuntimeError):
    pass


def acknowledged(action, output):
    """Require exactly one complete protocol line, not a substring of a log."""
    return sum(bool(re.fullmatch(ACKS[action], line))
               for line in output.splitlines()) == 1


def run_probe(transport, force=False, emit=print, pause=time.sleep,
              polarity_channel=None, hold=False):
    if polarity_channel is not None and (
            type(polarity_channel) is not int or polarity_channel not in range(3)):
        raise ProbeError('Candidate polarity channel must be 0, 1 or 2')
    if force and polarity_channel is not None:
        raise ProbeError('Factory and single-bit experiments cannot be combined')
    if hold and (polarity_channel is None or force):
        raise ProbeError('A held observation requires one candidate polarity channel')
    snapshot = transport.action('snapshot')
    emit(snapshot)
    if not acknowledged('snapshot', snapshot):
        raise ProbeError('Snapshot was not verified; no force experiment started')
    if not force and polarity_channel is None:
        emit('READ_ONLY: no PHY/GPIO/LED configuration writes were requested')
        return
    originals = re.findall(r'^ORIGINAL_LED_WORD (0x[0-9a-f]{4})$', snapshot, re.MULTILINE)
    if len(originals) != 1 or originals[0] in ('0xffff', '0xdead'):
        raise ProbeError('No reliable unique original LED word; no experiment started')
    phases = ('off', 'on')
    expected = {}
    if polarity_channel is not None:
        mask = 1 << polarity_channel
        prefix = f'polarity-{polarity_channel}'
        phases = (prefix + '-baseline', prefix + '-inverted')
        for action, value in zip(phases, (int(originals[0], 16),
                                         int(originals[0], 16) ^ mask)):
            phase = action.rsplit('-', 1)[1].upper()
            expected[action] = (f'POLARITY channel={polarity_channel} phase={phase} '
                                f'mask=0x{mask:04x} value=0x{value:04x} verified=1')
        emit(f'CANDIDATE_ONLY channel={polarity_channel} mask=0x{mask:04x}; '
             'BASELINE/INVERTED are not OFF/ON, nor proof of brightness control')
    attempted, armed, released_to_guard, problem = False, False, False, None
    arm_action = f'arm-hold-{polarity_channel}' if hold else 'arm'
    try:
        attempted = True  # The remote guard may start even if SSH loses its reply.
        output = transport.action(arm_action)
        emit(output)
        if not acknowledged(arm_action, output):
            raise ProbeError('Guard arming was not verified')
        armed = True
        if hold:
            rounds = range(1, 2)
            actions = (phases[-1],)
            emit('OBSERVE: panel LAN1 and other panel lamps; one held candidate state, '
                 'device guard restores at 60s from arming; not proof of brightness')
        else:
            rounds = range(1, 4)
            actions = phases
            label = 'OFF/ON' if force else 'BASELINE/INVERTED'
            emit(f'OBSERVE: panel LAN1 and other panel lamps; 3 {label} rounds start in 3 seconds')
            pause(3)
        for cycle in rounds:
            for action in actions:
                emit(f'OBSERVE cycle={cycle} phase={action.upper()} duration=2s')
                output = transport.action(action)
                emit(output)
                if not acknowledged(action, output):
                    raise ProbeError(f'{action} write/readback was not verified')
                if action in expected and expected[action] not in output.splitlines():
                    raise ProbeError(f'{action} acknowledgement changed other register bits')
        if hold:
            emit('CANDIDATE_HELD: fresh write/readback verified; restoration PENDING '
                 'in independent 60s device guard, or earlier on link change; '
                 'observe now, no UCI changes')
            released_to_guard = True
    except BaseException as error:
        problem = error
    finally:
        if attempted and (not released_to_guard or problem is not None):
            try:
                output = transport.action('restore')
                emit(output)
                not_armed = output.splitlines().count('NOT_ARMED verified=1') == 1
                if not acknowledged('restore', output) and not (not armed and not_armed):
                    raise ProbeError('Missing complete restoration acknowledgement')
                if acknowledged('restore', output) and (
                        f'RESTORED value={originals[0]} verified=1' not in output.splitlines()):
                    raise ProbeError('Restoration acknowledgement differs from the saved word')
            except BaseException as error:
                raise ProbeError('RESTORE NOT VERIFIED: inspect the device-side guard '
                                 'and its state directory; do not claim restoration') from error
    if problem is not None:
        raise problem
    if not released_to_guard:
        emit('SOFTWARE_EXPERIMENT_COMPLETE: register restored; physical identity still needs observation')


class SSHTransport:
    def __init__(self, args, emit):
        self.emit = emit
        self.state = None
        self.ssh = [args.ssh, '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                    '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2',
                    '-o', 'StrictHostKeyChecking=yes', '-o', 'UpdateHostKeys=no']
        known_hosts = getattr(args, 'known_hosts', None)
        if known_hosts is not None:
            self.ssh += ['-o', f'UserKnownHostsFile={known_hosts}',
                         '-o', f'GlobalKnownHostsFile={os.devnull}']
        if args.bind:
            self.ssh += ['-b', args.bind]
        self.ssh += ['-i', str(args.identity), f'root@{args.host}']

    def call(self, command, payload=None):
        try:
            # Binary stdin is essential on Windows: TextIOWrapper would turn
            # LF into CRLF and make the device's /bin/sh reject the payload.
            result = subprocess.run(self.ssh + [command],
                                    input=payload.encode('utf-8') if payload is not None else None,
                                    stdin=subprocess.DEVNULL if payload is None else None,
                                    capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ProbeError(f'SSH action unavailable/timed out: {error}') from error
        stdout = result.stdout.decode('utf-8', errors='replace').replace('\r\n', '\n')
        stderr = result.stderr.decode('utf-8', errors='replace').replace('\r\n', '\n')
        if stderr.strip():
            self.emit(stderr.rstrip())
        if result.returncode:
            if stdout.strip():
                self.emit(stdout.rstrip())
            raise ProbeError(f'SSH action exited {result.returncode}: {command}')
        return stdout.rstrip()

    def deploy(self):
        self.state = self.call('umask 077; mktemp -d /tmp/sk-led-diag.XXXXXX').strip()
        if not re.fullmatch(r'/tmp/sk-led-diag\.[A-Za-z0-9]{6}', self.state):
            raise ProbeError('Unexpected remote state directory; refusing upload')
        self.emit(f'STATE_DIR {self.state} (retained in router /tmp for recovery/audit)')
        target = shlex.quote(self.state + '/probe.sh')
        self.call(f'umask 077; cat > {target}', HELPER.read_text(encoding='utf-8'))

    def action(self, name):
        if name not in ACKS:
            raise ProbeError(f'Unsupported probe action: {name}')
        if self.state is None:
            raise ProbeError('Probe is not deployed')
        return self.call('sh ' + shlex.quote(self.state + '/probe.sh') + ' ' +
                         shlex.quote(self.state) + ' ' + name)


def run_level_probe(transport, level, emit=print):
    """One held channel-0 level, with independently guarded three-word recovery."""
    if level not in ('off', 'on'):
        raise ProbeError('The held level must be off or on')
    snapshot = transport.action('snapshot')
    emit(snapshot)
    if not acknowledged('snapshot', snapshot):
        raise ProbeError('Snapshot was not verified; no level experiment started')
    saved = {}
    for reg in ('d032', 'd040', 'd044'):
        pattern = (rf'^REGISTER devad=0x1f reg=0x{reg} value=(0x[0-9a-f]{{4}})$'
                   if reg != 'd044' else r'^ORIGINAL_LED_WORD (0x[0-9a-f]{4})$')
        values = re.findall(pattern, snapshot, re.MULTILINE)
        if len(values) != 1 or values[0] in ('0xffff', '0xdead'):
            raise ProbeError(f'No reliable unique {reg} baseline; no experiment started')
        saved[reg] = int(values[0], 16)
    wanted = {'d032': saved['d032'] & ~0x0027,
              'd040': saved['d040'] & ~1,
              'd044': (saved['d044'] & ~1) | int(level == 'on')}

    def fields(words):
        return ' '.join(f'{reg}=0x{words[reg]:04x}' for reg in ('d032', 'd040', 'd044'))

    expected = f'LEVEL_CANDIDATE channel=0 level={level.upper()} {fields(wanted)} verified=1'
    restore_fields = f'RESTORED_FIELDS {fields(saved)} verified=1'
    attempted, armed, released, problem = False, False, False, None
    try:
        attempted = True
        output = transport.action('arm-level-0')
        emit(output)
        if not acknowledged('arm-level-0', output):
            raise ProbeError('Three-field recovery guard arming was not verified')
        armed = True
        emit(f'OBSERVE: panel LAN1 and other panel lamps; held {level.upper()} candidate, '
             'channel-0 hardware link/activity events disabled; independent 60s restoration')
        output = transport.action(f'level-{level}')
        emit(output)
        if not acknowledged(f'level-{level}', output) or expected not in output.splitlines():
            raise ProbeError('Level readback does not match the authorized channel-0 fields')
        emit('LEVEL_HELD: all three readbacks verified; restoration PENDING in independent '
             '60s device guard, earlier on link change; no UCI changes, observe now')
        released = True
    except BaseException as error:
        problem = error
    finally:
        if attempted and (not released or problem is not None):
            try:
                output = transport.action('restore')
                emit(output)
                not_armed = output.splitlines().count('NOT_ARMED verified=1') == 1
                if not armed and not_armed and not acknowledged('restore', output):
                    pass
                else:
                    field_acks = [line for line in output.splitlines()
                                  if re.fullmatch(RESTORED_FIELDS, line)]
                    if (not acknowledged('restore', output)
                            or f'RESTORED value=0x{saved["d044"]:04x} verified=1' not in output.splitlines()
                            or field_acks != [restore_fields]):
                        raise ProbeError('Missing complete three-field restoration acknowledgement')
            except BaseException as error:
                raise ProbeError('RESTORE NOT VERIFIED: inspect all three saved LED words '
                                 'and the independent guard; do not claim restoration') from error
    if problem is not None:
        raise problem


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    experiment = parser.add_mutually_exclusive_group()
    experiment.add_argument('--force-phy-led', action='store_true',
                            help='explicitly run 3 factory OFF/ON rounds on 5/1f/d044, then restore')
    experiment.add_argument('--invert-phy-led-channel', type=int, choices=range(3),
                            help='invert only one candidate d044 polarity bit (0..2), '
                                 '3 baseline/inverted rounds, then restore; not brightness control')
    experiment.add_argument('--hold-inverted-phy-led-channel', type=int, choices=range(3),
                            help='hold one candidate d044 polarity bit for observation; '
                                 'independent 60s guard restores, earlier on link change; '
                                 'returns before restoration, not brightness control')
    experiment.add_argument('--hold-phy-led-level', choices=('off', 'on'),
                            help='explicit channel-0 level candidate: clear only d032 mask 0027 '
                                 'and d040 bit 0, set d044 bit 0; independent 60s restoration '
                                 'of all three saved words; returns before recovery')
    parser.add_argument('--host', default='192.168.9.1')
    parser.add_argument('--bind', default='192.168.9.25')
    parser.add_argument('--identity', type=Path, default=Path.home() / '.ssh/id_onu')
    parser.add_argument('--ssh', default='ssh')
    parser.add_argument('--known-hosts', type=Path,
                        help='task-specific host keys verified through TTL; strict checking remains enabled')
    parser.add_argument('--output', type=Path, default=ROOT / 'work' /
                        ('led-phy-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.log'))
    args = parser.parse_args(argv)
    if args.host.startswith('-') or not re.fullmatch(r'[A-Za-z0-9.:-]+', args.host):
        parser.error('host must be an address or hostname, not an SSH option')
    output = args.output.resolve()
    if not output.is_relative_to(ROOT):
        parser.error('logs must stay inside ci-build-repo')
    output.parent.mkdir(parents=True, exist_ok=True)
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f'signal {signum}')
    # SIGTERM/HUP should take the same finally/recovery path as Ctrl-C.
    for name in ('SIGTERM', 'SIGHUP'):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), interrupted)
    # Exclusive creation never overwrites a previous diagnostic record.
    with output.open('x', encoding='utf-8', newline='\n') as log:
        def emit(message):
            print(message, flush=True)
            log.write(message + '\n')
            log.flush()
        emit(f'LOG {output}')
        transport = SSHTransport(args, emit)
        try:
            transport.deploy()
            if args.hold_phy_led_level is not None:
                run_level_probe(transport, args.hold_phy_led_level, emit)
            else:
                holding = args.hold_inverted_phy_led_channel is not None
                run_probe(transport, args.force_phy_led, emit,
                          polarity_channel=(args.hold_inverted_phy_led_channel if holding
                                            else args.invert_phy_led_channel), hold=holding)
        except KeyboardInterrupt:
            emit('INTERRUPTED: cleanup attempted; consult restoration acknowledgement above')
            return 130
        except ProbeError as error:
            emit(f'ERROR {error}')
            if transport.state:
                emit(f'RECOVERY_STATE {transport.state}; guard.log/guard.done/restored/cancelled')
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
