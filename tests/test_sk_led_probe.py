#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Offline protocol and fail-safe tests; never connect to a board."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from sk_led_probe_fixture import HelperFixture, MDIO_STUB


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'sk_led_probe_under_test', ROOT / 'tools/diagnose-sk-leds.py')
probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)
BUSYBOX_SPEC = importlib.util.spec_from_file_location(
    'sk_led_busybox_fixture', ROOT / 'tests/sk_led_probe_busybox_fixture.py')
busybox_fixture = importlib.util.module_from_spec(BUSYBOX_SPEC)
BUSYBOX_SPEC.loader.exec_module(busybox_fixture)

ACK = {
    'snapshot': 'ORIGINAL_LED_WORD 0x00a7\nSNAPSHOT verified=1\n',
    'arm': 'ARMED deadline=30 verified=1\n',
    'off': 'OFF value=0x0000 verified=1\n',
    'on': 'ON value=0x00ff verified=1\n',
    'restore': 'RESTORED value=0x00a7 verified=1\n',
}
FORCE_ACTIONS = ['snapshot', 'arm', 'off', 'on', 'off', 'on',
                 'off', 'on', 'restore']


def polarity_protocol(channel, original=0x00a7, hold=False):
    """Independent single-bit expectations, not hardware brightness claims."""
    mask = 1 << channel
    phases = [f'polarity-{channel}-{phase}' for phase in ('baseline', 'inverted')]
    responses = {
        'snapshot': f'ORIGINAL_LED_WORD 0x{original:04x}\nSNAPSHOT verified=1\n',
        'restore': f'RESTORED value=0x{original:04x} verified=1\n',
    }
    for action, phase, value in zip(phases, ('BASELINE', 'INVERTED'),
                                    (original, original ^ mask)):
        responses[action] = (f'POLARITY channel={channel} phase={phase} '
                             f'mask=0x{mask:04x} value=0x{value:04x} verified=1\n')
    if hold:
        arm = f'arm-hold-{channel}'
        responses[arm] = f'ARMED deadline=60 channel={channel} verified=1\n'
        return responses, ['snapshot', arm, phases[-1]]
    return responses, ['snapshot', 'arm', *(phases * 3), 'restore']


def level_protocol(level, saved=(0x00a7, 0x0001, 0x40fa)):
    d032, d040, d044 = saved
    return {
        'snapshot': (f'REGISTER devad=0x1f reg=0xd032 value=0x{d032:04x}\n'
                     f'REGISTER devad=0x1f reg=0xd040 value=0x{d040:04x}\n'
                     f'ORIGINAL_LED_WORD 0x{d044:04x}\nSNAPSHOT verified=1\n'),
        'arm-level-0': 'ARMED deadline=60 channel=0 fields=d032,d040,d044 verified=1\n',
        f'level-{level}': (f'LEVEL_CANDIDATE channel=0 level={level.upper()} '
                           f'd032=0x{d032 & ~0x0027:04x} d040=0x{d040 & ~1:04x} '
                           f'd044=0x{(d044 & ~1) | int(level == "on"):04x} verified=1\n'),
        'restore': (f'RESTORED_FIELDS d032=0x{d032:04x} d040=0x{d040:04x} '
                    f'd044=0x{d044:04x} verified=1\n'
                    f'RESTORED value=0x{d044:04x} verified=1\n'),
    }


class FakeTransport:
    """Model remote stdout/failures without starting SSH or a shell."""

    def __init__(self, responses=None):
        self.responses = {
            name: list(value) if isinstance(value, list) else value
            for name, value in (responses or {}).items()
        }
        self.calls = []

    def action(self, name):
        self.calls.append(name)
        if name not in probe.ACKS:
            raise AssertionError('Unexpected remote action: ' + name)
        response = self.responses[name] if name in self.responses else ACK[name]
        if isinstance(response, list):
            if not response:
                raise AssertionError('Unexpected extra action: ' + name)
            response = response.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class ProbeProtocolTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        self.pauses = []

    def run_probe(self, transport, force=False, pause=None):
        return probe.run_probe(
            transport, force=force, emit=self.messages.append,
            pause=pause or self.pauses.append)

    def assert_guard_warning(self, error):
        reported = '\n'.join([str(error), *map(str, self.messages)])
        self.assertRegex(reported, r'(?i)guard|守护')

    def test_default_is_snapshot_only(self):
        transport = FakeTransport()
        self.assertIsNone(self.run_probe(transport))
        self.assertEqual(transport.calls, ['snapshot'])
        self.assertEqual(self.pauses, [])

    def test_force_has_three_complete_rounds_and_verified_restore(self):
        transport = FakeTransport()
        self.assertIsNone(self.run_probe(transport, force=True))
        self.assertEqual(transport.calls, FORCE_ACTIONS)
        # The remote helper holds each OFF/ON phase for two seconds.
        self.assertEqual(self.pauses, [3])

    def test_observation_prompt_only_requests_fitted_panel_lamps(self):
        self.run_probe(FakeTransport(), force=True)
        prompt, = [line for line in self.messages if line.startswith('OBSERVE:')]
        self.assertIn('panel LAN1 and other panel lamps;', prompt)
        self.assertNotIn('RJ45', prompt)

    def test_diagnostic_lines_around_ack_are_accepted(self):
        transport = FakeTransport({
            name: 'diagnostic details\n' + ack + 'other details\n'
            for name, ack in ACK.items()
        })
        self.run_probe(transport, force=True)
        self.assertEqual(transport.calls, FORCE_ACTIONS)

    def test_snapshot_failure_never_arms_or_restores(self):
        for force in (False, True):
            with self.subTest(force=force):
                transport = FakeTransport({
                    'snapshot': probe.ProbeError('snapshot SSH timeout')})
                with self.assertRaises(probe.ProbeError):
                    self.run_probe(transport, force=force)
                self.assertEqual(transport.calls, ['snapshot'])

    def test_snapshot_ack_is_required_before_arm(self):
        for bad in ('', 'SNAPSHOT verified=0\n',
                    'prefix SNAPSHOT verified=1\n',
                    ACK['snapshot'] * 2):
            with self.subTest(stdout=bad):
                transport = FakeTransport({'snapshot': bad})
                with self.assertRaises(probe.ProbeError):
                    self.run_probe(transport, force=True)
                self.assertEqual(transport.calls, ['snapshot'])

    def test_force_snapshot_requires_one_saved_complete_word(self):
        for prefix in ('', 'ORIGINAL_LED_WORD 0xa7\n',
                       'ORIGINAL_LED_WORD 0xffff\n',
                       'ORIGINAL_LED_WORD 0xdead\n',
                       'ORIGINAL_LED_WORD 0x00a7\n' * 2):
            with self.subTest(original=prefix):
                transport = FakeTransport({
                    'snapshot': prefix + 'SNAPSHOT verified=1\n'})
                with self.assertRaises(probe.ProbeError):
                    self.run_probe(transport, force=True)
                self.assertEqual(transport.calls, ['snapshot'])

    def test_arm_failure_still_attempts_restore(self):
        transport = FakeTransport({
            'arm': probe.ProbeError('SSH reply lost after guard started')})
        with self.assertRaises(probe.ProbeError):
            self.run_probe(transport, force=True)
        self.assertEqual(transport.calls, ['snapshot', 'arm', 'restore'])

    def test_unconfirmed_arm_may_report_verified_not_armed(self):
        transport = FakeTransport({
            'arm': probe.ProbeError('arm rejected before ready'),
            'restore': 'NOT_ARMED verified=1\n',
        })
        with self.assertRaises(probe.ProbeError):
            self.run_probe(transport, force=True)
        self.assertEqual(transport.calls, ['snapshot', 'arm', 'restore'])

    def test_invalid_arm_ack_still_attempts_restore(self):
        for bad in ('', 'ARMED deadline=30 verified=0\n',
                    'ARMED deadline=31 verified=1\n', ACK['arm'] * 2):
            with self.subTest(stdout=bad):
                transport = FakeTransport({'arm': bad})
                with self.assertRaises(probe.ProbeError):
                    self.run_probe(transport, force=True)
                self.assertEqual(transport.calls,
                                 ['snapshot', 'arm', 'restore'])

    def test_each_off_on_transport_failure_restores_once(self):
        for action in ('off', 'on'):
            for occurrence in range(1, 4):
                with self.subTest(action=action, occurrence=occurrence):
                    responses = [ACK[action]] * (occurrence - 1)
                    responses.append(probe.ProbeError('MDIO write failed'))
                    transport = FakeTransport({action: responses})
                    with self.assertRaises(probe.ProbeError):
                        self.run_probe(transport, force=True)
                    target = [i for i, item in enumerate(FORCE_ACTIONS)
                              if item == action][occurrence - 1]
                    self.assertEqual(transport.calls,
                                     FORCE_ACTIONS[:target + 1] + ['restore'])

    def test_off_on_readback_ack_must_match_requested_value(self):
        for action, bad in (
                ('off', 'OFF value=0x0001 verified=1\n'),
                ('on', 'ON value=0x0000 verified=1\n'),
                ('off', 'OFF value=0x0000 verified=0\n'),
                ('on', 'ON value=0x00ff verified=0\n'),
                ('off', ''), ('on', ACK['on'] * 2)):
            with self.subTest(action=action, stdout=bad):
                transport = FakeTransport({action: bad})
                with self.assertRaises(probe.ProbeError):
                    self.run_probe(transport, force=True)
                target = FORCE_ACTIONS.index(action)
                self.assertEqual(transport.calls,
                                 FORCE_ACTIONS[:target + 1] + ['restore'])

    def test_keyboard_interrupt_during_observation_pause_restores(self):
        def interrupted_pause(_seconds):
            raise KeyboardInterrupt()

        transport = FakeTransport()
        with self.assertRaises(KeyboardInterrupt):
            self.run_probe(transport, force=True, pause=interrupted_pause)
        self.assertEqual(transport.calls, ['snapshot', 'arm', 'restore'])

    def test_keyboard_interrupt_during_arm_or_writes_restores(self):
        for action in ('arm', 'off', 'on'):
            with self.subTest(action=action):
                transport = FakeTransport({action: KeyboardInterrupt()})
                with self.assertRaises(KeyboardInterrupt):
                    self.run_probe(transport, force=True)
                target = FORCE_ACTIONS.index(action)
                self.assertEqual(transport.calls,
                                 FORCE_ACTIONS[:target + 1] + ['restore'])

    def test_restore_failure_is_error_and_preserves_guard_warning(self):
        transport = FakeTransport({
            'restore': probe.ProbeError('SSH timeout during restoration')})
        with self.assertRaises(probe.ProbeError) as error:
            self.run_probe(transport, force=True)
        self.assertEqual(transport.calls, FORCE_ACTIONS)
        self.assert_guard_warning(error.exception)

    def test_missing_or_ambiguous_restore_ack_cannot_succeed(self):
        for bad in ('', 'RESTORED value=0x00a7\n',
                    'RESTORED value=0x00a7 verified=0\n',
                    'RESTORED value=0x0a7 verified=1\n',
                    'prefix RESTORED value=0x00a7 verified=1\n',
                    'RESTORED value=0x0070 verified=1\n',
                    ACK['restore'] * 2):
            with self.subTest(stdout=bad):
                transport = FakeTransport({'restore': bad})
                with self.assertRaises(probe.ProbeError) as error:
                    self.run_probe(transport, force=True)
                self.assertEqual(transport.calls, FORCE_ACTIONS)
                self.assert_guard_warning(error.exception)

    def test_not_armed_is_not_restore_ack_after_confirmed_arm(self):
        transport = FakeTransport({'restore': 'NOT_ARMED verified=1\n'})
        with self.assertRaises(probe.ProbeError) as error:
            self.run_probe(transport, force=True)
        self.assertEqual(transport.calls, FORCE_ACTIONS)
        self.assert_guard_warning(error.exception)

    def test_write_and_restore_timeout_never_claims_recovery(self):
        transport = FakeTransport({
            'off': probe.ProbeError('SSH timeout: remote state is unknown'),
            'restore': probe.ProbeError('SSH restore timeout'),
        })
        with self.assertRaises(probe.ProbeError) as error:
            self.run_probe(transport, force=True)
        self.assertEqual(transport.calls,
                         ['snapshot', 'arm', 'off', 'restore'])
        self.assert_guard_warning(error.exception)


class PolarityProtocolTests(unittest.TestCase):
    def run_probe(self, transport, channel, **options):
        return probe.run_probe(transport, polarity_channel=channel,
                               emit=lambda _message: None,
                               pause=lambda _seconds: None, **options)

    def test_all_candidates_preserve_other_bits_and_restore_complete_word(self):
        for channel in range(3):
            for original in (0x0000, 0x00a7, 0x40fa, 0x8077, 0xbffe):
                with self.subTest(channel=channel, original=original):
                    responses, actions = polarity_protocol(channel, original)
                    transport = FakeTransport(responses)
                    self.run_probe(transport, channel)
                    self.assertEqual(transport.calls, actions)
                    self.assertNotIn('off', actions)
                    self.assertNotIn('on', actions)

    def test_wrong_channel_phase_mask_value_duplicate_or_missing_ack_restores(self):
        responses, actions = polarity_protocol(0, 0x40fa)
        action = 'polarity-0-inverted'
        valid = responses[action]
        for invalid in ('', valid * 2, valid.replace('channel=0', 'channel=1'),
                        valid.replace('INVERTED', 'BASELINE'),
                        valid.replace('0x0001', '0x0004'),
                        valid.replace('0x40fb', '0x00ff'),
                        valid.replace('0x40fb', '0x40fa')):
            with self.subTest(ack=invalid):
                transport = FakeTransport({**responses, action: invalid})
                with self.assertRaises(probe.ProbeError):
                    self.run_probe(transport, 0)
                self.assertEqual(transport.calls,
                                 actions[:actions.index(action) + 1] + ['restore'])

    def test_each_channel_transport_error_or_interrupt_restores(self):
        for channel in range(3):
            responses, actions = polarity_protocol(channel)
            for action in actions[1:-1][:3]:
                for error in (probe.ProbeError('SSH/read/write failure'), KeyboardInterrupt()):
                    with self.subTest(channel=channel, action=action, error=type(error)):
                        transport = FakeTransport({**responses, action: error})
                        with self.assertRaises(type(error)):
                            self.run_probe(transport, channel)
                        self.assertEqual(transport.calls,
                                         actions[:actions.index(action) + 1] + ['restore'])

    def test_invalid_channel_or_mixed_factory_experiment_never_reaches_transport(self):
        for channel, force in ((-1, False), (3, False), ('0', False), (True, False),
                                (0.0, False), (0, True)):
            with self.subTest(channel=channel, force=force):
                transport = FakeTransport()
                with self.assertRaises(probe.ProbeError):
                    self.run_probe(transport, channel, force=force)
                self.assertEqual(transport.calls, [])

    def test_missing_snapshot_or_arm_ack_cannot_start_channel_writes(self):
        for action in ('snapshot', 'arm'):
            with self.subTest(action=action):
                responses, _actions = polarity_protocol(2)
                transport = FakeTransport({**responses, action: ''})
                with self.assertRaises(probe.ProbeError):
                    self.run_probe(transport, 2)
                self.assertEqual(transport.calls, ['snapshot'] if action == 'snapshot'
                                 else ['snapshot', 'arm', 'restore'])

    def test_channel_failure_and_unknown_restore_never_claim_recovery(self):
        responses, _actions = polarity_protocol(2)
        transport = FakeTransport({**responses,
                                   'polarity-2-inverted': probe.ProbeError('lost write reply'),
                                   'restore': probe.ProbeError('lost restore reply')})
        with self.assertRaisesRegex(probe.ProbeError, 'RESTORE NOT VERIFIED'):
            self.run_probe(transport, 2)
        self.assertEqual(transport.calls[-1], 'restore')


class HeldPolarityTests(unittest.TestCase):
    def test_success_returns_while_guard_owns_recovery_and_does_not_claim_restore(self):
        for channel in range(3):
            with self.subTest(channel=channel):
                responses, actions = polarity_protocol(channel, 0x40fa, hold=True)
                transport = FakeTransport(responses)
                messages, pauses = [], []
                probe.run_probe(transport, polarity_channel=channel, hold=True,
                                emit=messages.append, pause=pauses.append)
                self.assertEqual(transport.calls, actions)
                self.assertEqual(pauses, [])
                self.assertIn('restoration PENDING', '\n'.join(messages))
                self.assertNotIn('SOFTWARE_EXPERIMENT_COMPLETE', '\n'.join(messages))
                self.assertNotIn('RESTORED value=', '\n'.join(messages))

    def test_held_guard_or_write_failure_and_interrupt_attempt_restore(self):
        responses, actions = polarity_protocol(0, hold=True)
        for action in actions[1:]:
            for failure in (probe.ProbeError('transport lost'), KeyboardInterrupt(), ''):
                with self.subTest(action=action, failure=type(failure)):
                    transport = FakeTransport({**responses, action: failure})
                    error = KeyboardInterrupt if isinstance(failure, KeyboardInterrupt) else probe.ProbeError
                    with self.assertRaises(error):
                        probe.run_probe(transport, polarity_channel=0, hold=True,
                                        emit=lambda _message: None)
                    self.assertEqual(transport.calls,
                                     actions[:actions.index(action) + 1] + ['restore'])

    def test_incorrect_held_deadline_channel_or_value_ack_cannot_release_to_guard(self):
        responses, actions = polarity_protocol(2, 0x40fa, hold=True)
        for action, bad in (('arm-hold-2', 'ARMED deadline=30 channel=2 verified=1\n'),
                            ('arm-hold-2', 'ARMED deadline=60 channel=0 verified=1\n'),
                            ('polarity-2-inverted', responses['polarity-2-inverted'].replace('0x40fe', '0x00ff'))):
            with self.subTest(action=action):
                transport = FakeTransport({**responses, action: bad})
                with self.assertRaises(probe.ProbeError):
                    probe.run_probe(transport, polarity_channel=2, hold=True,
                                    emit=lambda _message: None)
                self.assertEqual(transport.calls,
                                 actions[:actions.index(action) + 1] + ['restore'])

    def test_output_failure_before_handoff_keeps_host_cleanup(self):
        responses, _actions = polarity_protocol(0, hold=True)
        transport = FakeTransport(responses)

        def disconnected_console(message):
            if message.startswith('CANDIDATE_HELD:'):
                raise OSError('console lost before handoff completed')

        with self.assertRaises(OSError):
            probe.run_probe(transport, polarity_channel=0, hold=True,
                            emit=disconnected_console)
        self.assertEqual(transport.calls[-1], 'restore')

    def test_hold_without_valid_single_channel_never_reaches_transport(self):
        for options in ({}, {'force': True}, {'polarity_channel': 3},
                        {'polarity_channel': 0, 'force': True}):
            with self.subTest(options=options):
                transport = FakeTransport()
                with self.assertRaises(probe.ProbeError):
                    probe.run_probe(transport, hold=True, **options)
                self.assertEqual(transport.calls, [])


class HeldLevelTests(unittest.TestCase):
    def test_snapshot_transport_error_never_arms_or_attempts_hardware_cleanup(self):
        transport = FakeTransport({'snapshot': probe.ProbeError('read SSH timed out')})
        with self.assertRaises(probe.ProbeError):
            probe.run_level_probe(transport, 'off', lambda _message: None)
        self.assertEqual(transport.calls, ['snapshot'])

    def test_both_levels_preserve_unrelated_bits_and_return_before_recovery(self):
        for level in ('off', 'on'):
            for saved in ((0x0003, 0x391f, 0x40fa), (0xaba7, 0x55fb, 0x8276),
                          (0, 0, 0), (0xfffe, 0xfffe, 0xfffe)):
                with self.subTest(level=level, saved=saved):
                    transport = FakeTransport(level_protocol(level, saved))
                    messages = []
                    probe.run_level_probe(transport, level, messages.append)
                    self.assertEqual(transport.calls, ['snapshot', 'arm-level-0', f'level-{level}'])
                    self.assertIn('restoration PENDING', '\n'.join(messages))
                    self.assertNotIn('RESTORED value=', '\n'.join(messages))

    def test_snapshot_requires_all_three_reliable_unique_originals(self):
        responses = level_protocol('off')
        for target in ('REGISTER devad=0x1f reg=0xd032 value=0x00a7\n',
                       'REGISTER devad=0x1f reg=0xd040 value=0x0001\n',
                       'ORIGINAL_LED_WORD 0x40fa\n', 'SNAPSHOT verified=1\n'):
            for replacement in ('', target * 2,
                                re.sub(r'0x[0-9a-f]{4}(?=\n)', '0xffff', target),
                                re.sub(r'0x[0-9a-f]{4}(?=\n)', '0xdead', target)):
                if replacement == target:
                    continue
                with self.subTest(target=target, replacement=replacement):
                    transport = FakeTransport({**responses,
                        'snapshot': responses['snapshot'].replace(target, replacement)})
                    with self.assertRaises(probe.ProbeError):
                        probe.run_level_probe(transport, 'off', lambda _message: None)
                    self.assertEqual(transport.calls, ['snapshot'])

    def test_each_failure_or_interrupt_attempts_three_word_recovery(self):
        for action in ('arm-level-0', 'level-on'):
            for failure in (probe.ProbeError('lost SSH reply'), KeyboardInterrupt(), ''):
                with self.subTest(action=action, failure=type(failure)):
                    transport = FakeTransport({**level_protocol('on'), action: failure})
                    expected_error = KeyboardInterrupt if isinstance(failure, KeyboardInterrupt) else probe.ProbeError
                    with self.assertRaises(expected_error):
                        probe.run_level_probe(transport, 'on', lambda _message: None)
                    self.assertEqual(transport.calls[-1], 'restore')

    def test_wrong_level_any_field_or_arm_ack_restores(self):
        responses = level_protocol('on')
        for action, bad in (
                ('arm-level-0', responses['arm-level-0'].replace('60', '30')),
                ('arm-level-0', responses['arm-level-0'].replace('channel=0', 'channel=1')),
                ('level-on', responses['level-on'] * 2),
                ('level-on', responses['level-on'].replace('ON', 'OFF')),
                ('level-on', responses['level-on'].replace('d032=0x0080', 'd032=0x0000')),
                ('level-on', responses['level-on'].replace('d040=0x0000', 'd040=0x0002')),
                ('level-on', responses['level-on'].replace('d044=0x40fb', 'd044=0x00ff'))):
            with self.subTest(action=action, stdout=bad):
                transport = FakeTransport({**responses, action: bad})
                with self.assertRaises(probe.ProbeError):
                    probe.run_level_probe(transport, 'on', lambda _message: None)
                self.assertEqual(transport.calls[-1], 'restore')

    def test_incomplete_or_wrong_field_restoration_is_not_success(self):
        responses = level_protocol('off')
        for bad in ('', responses['restore'].splitlines()[-1] + '\n',
                    responses['restore'] * 2,
                    responses['restore'].replace('d032=0x00a7', 'd032=0x0080'),
                    responses['restore'].replace('d040=0x0001', 'd040=0x0000'),
                    responses['restore'].replace('d044=0x40fa', 'd044=0x40fb'),
                    probe.ProbeError('restoration timeout')):
            with self.subTest(stdout=bad):
                transport = FakeTransport({**responses, 'level-off': '', 'restore': bad})
                with self.assertRaisesRegex(probe.ProbeError, 'RESTORE NOT VERIFIED'):
                    probe.run_level_probe(transport, 'off', lambda _message: None)

    def test_output_failure_before_handoff_restores_every_field(self):
        transport = FakeTransport(level_protocol('on'))

        def lost_console(message):
            if message.startswith('LEVEL_HELD:'):
                raise OSError('output failed before guard handoff')

        with self.assertRaises(OSError):
            probe.run_level_probe(transport, 'on', lost_console)
        self.assertEqual(transport.calls[-1], 'restore')

    def test_invalid_levels_never_reach_transport(self):
        for level in ('ON', '', 0, None, True):
            with self.subTest(level=level):
                transport = FakeTransport()
                with self.assertRaises(probe.ProbeError):
                    probe.run_level_probe(transport, level)
                self.assertEqual(transport.calls, [])


class ProbeShellSafetyTests(unittest.TestCase):
    """Source-level bounds also run on Windows, without a Linux shell."""

    @classmethod
    def setUpClass(cls):
        cls.source = probe.HELPER.read_text(encoding='utf-8')

    def test_write_targets_are_fixed_phy_and_explicitly_masked_led_fields(self):
        commands = re.findall(r"printf 'm45w ([^']+)\\n'", self.source)
        self.assertEqual(commands, ['5 %s %s %s'])
        exchange = self.source.split('\nexchange() {', 1)[1].split('\nnetwork_ok() {', 1)[0]
        self.assertIn('case "$devad/$reg" in', exchange)
        self.assertIn('0x1f/0xd044) ;;', exchange)
        self.assertIn('0x1f/0xd032|0x1f/0xd040)', exchange)
        self.assertIn("level_mode || fail 'extra LED fields require explicit level-mode arming'", exchange)
        self.assertIn('(current_word ^ value) & (65535 ^ write_mask)', exchange)
        self.assertIn("*) fail 'refusing non-LED register write'", exchange)
        self.assertNotRegex(self.source, r'(?m)^\s*(?:reboot|devmem|uci\s+(?:set|commit))\b')

    def test_guard_is_detached_and_has_fixed_thirty_second_deadline(self):
        self.assertIn('\ndeadline=30\n', self.source)
        self.assertIn('[ "$elapsed" -lt "$deadline" ]', self.source)
        self.assertIn("ARMED deadline=30 verified=1", self.source)
        self.assertIn('arm|arm-hold-[012]|arm-level-0)', self.source)
        self.assertIn('elif [ "$action" != arm ]; then', self.source)
        self.assertIn('deadline=60', self.source)
        self.assertIn("(trap '' HUP; guard) </dev/null > \"$state/guard.log\" 2>&1 &",
                      self.source)
        self.assertRegex(self.source, r'exec 8>>?"\$global_lock"')
        self.assertIn('flock -n 8', self.source)
        guard = self.source.split('\nguard() {', 1)[1].split('\nboard\n', 1)[0]
        self.assertLess(guard.index('exec 9>&-'),
                        guard.index('exec 9>"$state/io.lock"'))

    def test_dirty_marker_precedes_write_and_guard_precedes_write(self):
        phase = self.source.split('\noff|on|', 1)[1].split('\nrestore)\n', 1)[0]
        writing = phase.index('exchange write 0x1f 0xd044')
        self.assertLess(phase.index('"$state/guard.ready"'), writing)
        self.assertLess(phase.index('kill -0'), writing)
        self.assertLess(phase.index('"$state/dirty"'), writing)
        self.assertIn('sleep 1', phase)
        self.assertIn('[ "$count" -lt 2 ]', phase)
        self.assertNotRegex(self.source, r'(?m)^\s*sleep\s+[0-9]+\.[0-9]+\b')

    def test_polarity_candidates_have_fixed_bits_and_recheck_deadline_before_write(self):
        phase = self.source.split('\noff|on|', 1)[1].split('\nrestore)\n', 1)[0]
        self.assertIn('polarity-[012]-baseline|polarity-[012]-inverted)', phase)
        self.assertIn('mask=$((1 << channel))', phase)
        self.assertIn('saved ^ mask', phase)
        self.assertLess(phase.index('guard deadline passed before write'),
                        phase.index('exchange write 0x1f 0xd044'))
        self.assertIn('cannot mix experiment fields in one session', phase)

    def test_private_global_lock_keeps_production_root_and_mode_checks(self):
        self.assertIn('ls -ldn "$global_dir"', self.source)
        self.assertIn('$1 == "drwx------" && $3 == 0', self.source)
        self.assertIn('[ ! -L "$global_dir" ]', self.source)
        self.assertIn('[ ! -L "$global_lock" ]', self.source)

    def test_level_mode_masks_order_and_all_field_recovery(self):
        masks = self.source.split('\nlevel_mask() {', 1)[1].split('\nsaved_field() {', 1)[0]
        self.assertIn("0xd032) printf '0x0027", masks)
        self.assertIn("0xd040|0xd044) printf '0x0001", masks)
        level = self.source.split('\nlevel-on|level-off)\n', 1)[1].split('\noff|on|', 1)[0]
        self.assertIn('level_mode || fail', level)
        self.assertIn('for level_reg in 0xd032 0xd040 0xd044;', level)
        self.assertLess(level.index('"$state/dirty"'), level.index('exchange write'))
        self.assertLess(level.index('guard deadline passed before field write'), level.index('exchange write'))
        self.assertIn('held level already applied', level)
        recovery = self.source.split('\nrestore_level_fields() {', 1)[1].split('\nrestore_word() {', 1)[0]
        self.assertIn('for restore_reg in 0xd044 0xd040 0xd032;', recovery)
        self.assertIn('restore_failed=1; continue;', recovery)
        self.assertIn('(restore_current & (65535 ^ restore_mask))', recovery)
        self.assertIn('verify_level_fields || return 1', recovery)

    def test_guard_cancellation_follows_verified_word_restoration(self):
        restoration = self.source.split('\nrestore)\n', 1)[1]
        self.assertLess(restoration.index('restore_word ||'),
                        restoration.index('> "$state/cancelled"'))
        function = self.source.split('\nrestore_word() {', 1)[1].split('\nguard() {', 1)[0]
        self.assertLess(function.index('[ "$actual" = "$saved" ]'),
                        function.index('RESTORED value='))

    def test_snapshot_has_no_hardware_writes_and_log_responses_must_be_fresh(self):
        snapshot = self.source.split('\nsnapshot)\n', 1)[1].split('\narm|arm-hold-', 1)[0]
        self.assertNotIn('exchange write', snapshot)
        self.assertNotIn('m45w ', snapshot)
        self.assertIn('[ "$after" != "$before" ]', self.source)
        self.assertNotRegex(self.source, r'\bdmesg\s+(?:-c|--clear)\b')


class ProbeTransportTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        args = SimpleNamespace(ssh='ssh', bind=None,
                               identity=Path('not-a-real-key'), host='192.0.2.1')
        self.transport = probe.SSHTransport(args, self.messages.append)

    def test_upload_uses_binary_stdin_so_windows_cannot_convert_lf(self):
        response = subprocess.CompletedProcess([], 0, b'OK\r\n', b'')
        with mock.patch.object(probe.subprocess, 'run', return_value=response) as run:
            self.assertEqual(self.transport.call('mock command', '#!/bin/sh\nprintf hi\n'), 'OK')
        self.assertEqual(run.call_args.kwargs['input'], b'#!/bin/sh\nprintf hi\n')
        self.assertNotIn('text', run.call_args.kwargs)

    def test_ssh_timeout_becomes_probe_error(self):
        timeout = subprocess.TimeoutExpired('mock ssh', 15)
        with mock.patch.object(probe.subprocess, 'run', side_effect=timeout):
            with self.assertRaises(probe.ProbeError):
                self.transport.call('mock command')

    def test_nonzero_ssh_exit_cannot_be_overridden_by_stdout_ack(self):
        response = subprocess.CompletedProcess([], 1, ACK['restore'].encode(),
                                               b'non-LED sentinel changed\n')
        with mock.patch.object(probe.subprocess, 'run', return_value=response):
            with self.assertRaises(probe.ProbeError):
                self.transport.call('mock command')

    def test_unexpected_remote_directory_never_uploads(self):
        with mock.patch.object(self.transport, 'call', return_value='/etc/config/system') as call:
            with self.assertRaises(probe.ProbeError):
                self.transport.deploy()
        call.assert_called_once()

    def test_unknown_action_never_reaches_ssh(self):
        self.transport.state = '/tmp/sk-led-diag.ABC123'
        for action in ('arbitrary-register-write', 'polarity-3-inverted',
                       'polarity--1-baseline', 'polarity-0-on'):
            with self.subTest(action=action), mock.patch.object(self.transport, 'call') as call:
                with self.assertRaises(probe.ProbeError):
                    self.transport.action(action)
                call.assert_not_called()

    def test_cli_cannot_mix_experiments_or_accept_unknown_channel(self):
        for arguments in (['--force-phy-led', '--invert-phy-led-channel', '0'],
                          ['--invert-phy-led-channel', '-1'],
                          ['--invert-phy-led-channel', '3'],
                          ['--hold-phy-led-level', 'on', '--force-phy-led'],
                          ['--hold-phy-led-level', 'invalid']):
            with self.subTest(arguments=arguments), \
                    mock.patch.object(probe, 'SSHTransport') as transport, \
                    mock.patch('sys.stderr', new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as error:
                    probe.main(arguments)
                self.assertEqual(error.exception.code, 2)
                transport.assert_not_called()

    def test_cli_term_and_hup_use_same_verified_cleanup_as_ctrl_c(self):
        for name in ('SIGTERM', 'SIGHUP'):
            if not hasattr(probe.signal, name):
                continue
            with self.subTest(signal=name), tempfile.TemporaryDirectory(
                    prefix='sk-led-signal-') as directory:
                handlers = {}
                signum = getattr(probe.signal, name)

                class SignalTransport(FakeTransport):
                    state = '/tmp/sk-led-diag.ABC123'

                    def deploy(self):
                        pass

                    def action(self, action):
                        output = super().action(action)
                        if action == 'arm':
                            handlers[signum](signum, None)
                        return output

                transport = SignalTransport()
                output = Path(directory) / 'probe.log'
                with mock.patch.object(probe, 'ROOT', Path(directory).resolve()), \
                     mock.patch.object(probe, 'SSHTransport', return_value=transport), \
                     mock.patch.object(probe.signal, 'signal',
                                       side_effect=lambda number, handler:
                                       handlers.__setitem__(number, handler)), \
                     redirect_stdout(io.StringIO()):
                    status = probe.main(['--force-phy-led', '--output', str(output)])
                self.assertEqual(status, 130)
                self.assertEqual(transport.calls, ['snapshot', 'arm', 'restore'])
                log = output.read_text(encoding='utf-8')
                self.assertIn('RESTORED value=0x00a7 verified=1', log)
                self.assertIn('INTERRUPTED:', log)


class ProbeFixtureTests(unittest.TestCase):
    def test_busybox_original_check_preserves_the_injection_register(self):
        shell = (Path(r'C:\Program Files\Git\bin\bash.exe')
                 if os.name == 'nt' else Path('/bin/sh'))
        if not shell.is_file():
            self.skipTest('POSIX shell unavailable for fixture variable-scope check')
        function = re.search(r'assert_originals\(\) [({]\n.*?\n[)}]',
                             busybox_fixture.CASES, re.S)
        self.assertIsNotNone(function)
        script = ('set -eu\ncmp() { :; }\nroot=/fixture\n' + function[0] +
                  '\nreg=0xd032\nassert_originals\nprintf "%s\\n" "$reg"\n')
        result = subprocess.run([str(shell)], input=script, text=True,
                                encoding='utf-8', capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '0xd032')

    def test_busybox_fixture_replaces_all_hardware_and_config_paths(self):
        fixture_root = '/tmp/sk-led-mock.ABC123'
        script = busybox_fixture.payload(probe.HELPER, fixture_root)
        self.assertNotIn('/sys/', script)
        self.assertNotIn('/etc/', script)
        self.assertNotIn('> "$diag"', script)
        self.assertEqual(script.count('| sk-led-fixture-io'), 2)
        self.assertIn('FIXTURE_COMPLETE no real hardware accessed;', script)
        for unsafe in ('/tmp', '/etc/config/system', '/tmp/sk-led-mock.ABC123/../x',
                       '/tmp/sk-led-mock.ABC123;reboot', '/tmp/sk-led-mock.short'):
            with self.subTest(root=unsafe), self.assertRaises(ValueError):
                busybox_fixture.payload(probe.HELPER, unsafe)

    def test_simulated_mdio_has_real_newlines_and_preserves_saved_word(self):
        # Validate the fixture's generated Python on Windows too, even though
        # real flock/background-shell tests need Linux.
        with tempfile.TemporaryDirectory(prefix='sk-led-mdio-stub-') as directory:
            root = Path(directory)
            (root / 'registers.json').write_text(
                json.dumps({'1f/d044': '0x00a7'}), encoding='utf-8')
            (root / 'settings.json').write_text('{}', encoding='utf-8')
            env = {**os.environ, 'SK_LED_FIXTURE_ROOT': str(root)}
            for command in ('m45 5 0x1f 0xd044\n',
                            'm45w 5 0x1f 0xd044 0x0000\n',
                            'm45w 5 0x1f 0xd044 0x00a7\n'):
                result = subprocess.run([sys.executable, '-c', MDIO_STUB],
                                        input=command, capture_output=True,
                                        text=True, encoding='utf-8', env=env, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
            history = [json.loads(line) for line in
                       (root / 'history.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual([entry['kind'] for entry in history], ['m45', 'm45w', 'm45w'])
            self.assertEqual(len((root / 'kernel.log').read_text(encoding='utf-8').splitlines()), 3)
            self.assertEqual(json.loads((root / 'registers.json').read_text(encoding='utf-8')),
                             {'1f/d044': '0x00a7'})


@unittest.skipUnless(os.name == 'posix',
                     'Real flock/background-guard integration runs on Linux CI')
class ProbeShellIntegrationTests(unittest.TestCase):
    """Execute the actual helper state machine against temporary fake hardware."""

    def assert_action_ok(self, fixture, action, state=None):
        result = fixture.run(action, state=state)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_snapshot_is_read_only_for_registers_gpio_leds_and_configuration(self):
        with HelperFixture(probe.HELPER) as fixture:
            before_config = (fixture.root / 'system').read_bytes()
            before_leds = {str(path.relative_to(fixture.root)): path.read_bytes()
                           for path in (fixture.root / 'leds').rglob('*') if path.is_file()}
            result = self.assert_action_ok(fixture, 'snapshot')
            self.assertIn('SNAPSHOT verified=1\n', result)
            self.assertIn('USB_MONITOR port=usb1-port1 enabled=1\n', result)
            self.assertIn('USB_MONITOR port=usb2-port1 enabled=1\n', result)
            self.assertIn('USB_ROOT name=usb1 speed=480', result)
            self.assertIn('USB_ROOT name=usb2 speed=5000', result)
            self.assertNotIn('USB_DEVICE ', result)
            self.assertEqual(fixture.writes(), [])
            self.assertEqual(fixture.word(), '0x00a7')
            self.assertEqual((fixture.root / 'system').read_bytes(), before_config)
            self.assertEqual({str(path.relative_to(fixture.root)): path.read_bytes()
                              for path in (fixture.root / 'leds').rglob('*') if path.is_file()},
                             before_leds)

    def test_complete_host_protocol_with_real_shell_guard_and_restoration(self):
        with HelperFixture(probe.HELPER) as fixture:
            class ShellTransport:
                def action(self, name):
                    result = fixture.run(name)
                    if result.returncode:
                        raise probe.ProbeError(result.stdout + result.stderr)
                    return result.stdout

            messages = []
            probe.run_probe(ShellTransport(), force=True, emit=messages.append,
                            pause=lambda _seconds: None)
            writes = fixture.writes()
            self.assertEqual([entry['value'] for entry in writes],
                             [0, 0xff] * 3 + [0x00a7])
            self.assertEqual({(entry['phy'], entry['devad'], entry['reg'])
                              for entry in writes}, {(5, 0x1f, 0xd044)})
            self.assertEqual(fixture.word(), '0x00a7')
            self.assertTrue((fixture.state / 'cancelled').exists())

    def test_each_polarity_candidate_changes_only_one_bit_and_restores(self):
        for channel in range(3):
            with self.subTest(channel=channel), HelperFixture(probe.HELPER) as fixture:
                class ShellTransport:
                    def action(self, name):
                        result = fixture.run(name)
                        if result.returncode:
                            raise probe.ProbeError(result.stdout + result.stderr)
                        return result.stdout

                probe.run_probe(ShellTransport(), polarity_channel=channel,
                                emit=lambda _message: None, pause=lambda _seconds: None)
                writes = fixture.writes()
                self.assertEqual([entry['value'] for entry in writes],
                                 [0x00a7, 0x00a7 ^ (1 << channel)] * 3 + [0x00a7])
                self.assertTrue(all((entry['value'] ^ 0x00a7) & ~(1 << channel) == 0
                                    for entry in writes))
                self.assertEqual({(entry['phy'], entry['devad'], entry['reg'])
                                  for entry in writes}, {(5, 0x1f, 0xd044)})
                self.assertEqual(fixture.word(), '0x00a7')
                self.assertTrue((fixture.state / 'cancelled').exists())

    def test_held_candidate_guard_restores_after_host_exit_and_blocks_other_fields(self):
        with HelperFixture(probe.HELPER) as fixture:
            self.assert_action_ok(fixture, 'snapshot')
            armed = self.assert_action_ok(fixture, 'arm-hold-0')
            self.assertIn('ARMED deadline=60 channel=0 verified=1', armed)
            self.assertEqual((fixture.state / 'guard.deadline').read_text().strip(), '60')
            self.assert_action_ok(fixture, 'polarity-0-inverted')
            self.assertEqual(fixture.word(), '0x00a6')
            writes = fixture.writes()
            for action in ('on', 'off', 'polarity-1-inverted'):
                self.assertNotEqual(fixture.run(action).returncode, 0)
                self.assertEqual(fixture.writes(), writes)
            self.assertIn('GUARD_RESTORED verified=1', fixture.wait_for_guard(timeout=40))
            self.assertEqual(fixture.word(), '0x00a7')
            self.assertFalse((fixture.state / 'cancelled').exists())
            self.assertEqual([entry['value'] for entry in fixture.writes()], [0x00a6, 0x00a7])

    def test_held_candidate_link_change_restores_without_waiting_for_deadline(self):
        with HelperFixture(probe.HELPER) as fixture:
            self.assert_action_ok(fixture, 'snapshot')
            self.assert_action_ok(fixture, 'arm-hold-1')
            self.assert_action_ok(fixture, 'polarity-1-inverted')
            fixture.write('carrier', '0\n')
            self.assertIn('GUARD_RESTORED verified=1', fixture.wait_for_guard())
            self.assertIn('GUARD_LINK_CHANGED', (fixture.state / 'guard.log').read_text())
            self.assertEqual(fixture.word(), '0x00a7')

    def test_polarity_write_failure_mismatch_and_interrupt_restore_original(self):
        for injection in ('fail_write_once', 'mismatch_write_once', 'interrupt'):
            with self.subTest(injection=injection), HelperFixture(probe.HELPER) as fixture:
                self.assert_action_ok(fixture, 'snapshot')
                self.assert_action_ok(fixture, 'arm')
                if injection == 'interrupt':
                    self.assert_action_ok(fixture, 'polarity-1-inverted')
                else:
                    fixture.configure(**{injection: True})
                    failed = fixture.run('polarity-1-inverted')
                    self.assertNotEqual(failed.returncode, 0)
                self.assertTrue((fixture.state / 'dirty').exists())
                self.assertFalse((fixture.state / 'cancelled').exists())
                # An interrupted client follows this same restore action;
                # Python-level KeyboardInterrupt/finally is tested separately.
                self.assert_action_ok(fixture, 'restore')
                self.assertEqual(fixture.word(), '0x00a7')

    def test_polarity_guard_deadline_restores_and_refuses_late_stage(self):
        with HelperFixture(probe.HELPER) as fixture:
            self.assert_action_ok(fixture, 'snapshot')
            self.assert_action_ok(fixture, 'arm')
            self.assert_action_ok(fixture, 'polarity-2-inverted')
            self.assertEqual(fixture.word(), '0x00a3')
            self.assertIn('GUARD_RESTORED verified=1', fixture.wait_for_guard())
            self.assertEqual(fixture.word(), '0x00a7')
            writes = fixture.writes()
            failed = fixture.run('polarity-2-inverted')
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(fixture.writes(), writes)

    def test_one_session_cannot_mix_factory_or_other_channel(self):
        with HelperFixture(probe.HELPER) as fixture:
            self.assert_action_ok(fixture, 'snapshot')
            self.assert_action_ok(fixture, 'arm')
            self.assert_action_ok(fixture, 'polarity-0-inverted')
            writes = fixture.writes()
            for action in ('on', 'off', 'polarity-1-inverted', 'polarity-2-baseline',
                           'polarity-3-inverted', 'polarity--1-inverted'):
                failed = fixture.run(action)
                self.assertNotEqual(failed.returncode, 0)
                self.assertEqual(fixture.writes(), writes)
            self.assert_action_ok(fixture, 'restore')
            self.assertEqual(fixture.word(), '0x00a7')

    def test_write_failure_or_readback_mismatch_preserves_recovery(self):
        for injection in ('fail_write_once', 'mismatch_write_once'):
            with self.subTest(injection=injection), HelperFixture(probe.HELPER) as fixture:
                self.assert_action_ok(fixture, 'snapshot')
                self.assert_action_ok(fixture, 'arm')
                fixture.configure(**{injection: True})
                failed = fixture.run('off')
                self.assertNotEqual(failed.returncode, 0)
                self.assertTrue((fixture.state / 'dirty').exists())
                self.assertFalse((fixture.state / 'cancelled').exists())
                restored = self.assert_action_ok(fixture, 'restore')
                self.assertIn('RESTORED value=0x00a7 verified=1\n', restored)
                self.assertEqual(fixture.word(), '0x00a7')

    def test_guard_restores_then_new_session_blocks_old_late_restore(self):
        with HelperFixture(probe.HELPER) as fixture:
            self.assert_action_ok(fixture, 'snapshot')
            self.assert_action_ok(fixture, 'arm')
            self.assert_action_ok(fixture, 'off')
            self.assertEqual(fixture.word(), '0x0000')
            self.assertIn('GUARD_RESTORED verified=1', fixture.wait_for_guard())
            self.assertEqual(fixture.word(), '0x00a7')
            before = fixture.writes()
            late = fixture.run('on')
            self.assertNotEqual(late.returncode, 0)
            self.assertEqual(fixture.writes(), before)
            # A late cleanup may verify history but must not perform another
            # restoration write, particularly after a newer owner changes it.
            self.assert_action_ok(fixture, 'restore')
            self.assertEqual(fixture.writes(), before)
            # A distinct second session shares only the hardware/global lock.
            # Its live guard must exclude stale restoration from the first,
            # even though the first guard already restored successfully.
            second = fixture.new_session('DEF456')
            self.assert_action_ok(fixture, 'snapshot', state=second)
            self.assert_action_ok(fixture, 'arm', state=second)
            self.assert_action_ok(fixture, 'off', state=second)
            self.assertEqual(fixture.word(), '0x0000')
            before_late_restore = fixture.writes()
            blocked = fixture.run('restore')
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn('late recovery cannot own global lock', blocked.stderr)
            self.assertNotIn('RESTORED value=', blocked.stdout)
            self.assertEqual(fixture.writes(), before_late_restore)
            self.assertEqual(fixture.word(), '0x0000')
            self.assertFalse((second / 'cancelled').exists())
            self.assertFalse((second / 'expired').exists())
            self.assert_action_ok(fixture, 'restore', state=second)
            self.assertEqual(fixture.word(), '0x00a7')
            before = fixture.writes()
            registers = json.loads((fixture.root / 'registers.json').read_text(encoding='utf-8'))
            registers['1f/d044'] = '0x1234'
            fixture.write('registers.json', json.dumps(registers))
            stale = fixture.run('restore')
            self.assertNotEqual(stale.returncode, 0)
            self.assertEqual(fixture.word(), '0x1234')
            self.assertEqual(fixture.writes(), before)

    def test_failed_host_restore_does_not_cancel_independent_guard(self):
        with HelperFixture(probe.HELPER) as fixture:
            self.assert_action_ok(fixture, 'snapshot')
            self.assert_action_ok(fixture, 'arm')
            self.assert_action_ok(fixture, 'off')
            fixture.configure(fail_restore=True)
            failed = fixture.run('restore')
            self.assertNotEqual(failed.returncode, 0)
            self.assertFalse((fixture.state / 'cancelled').exists())
            fixture.configure()
            self.assertIn('GUARD_RESTORED verified=1', fixture.wait_for_guard())
            self.assertEqual(fixture.word(), '0x00a7')

    def test_missing_fresh_readback_is_fail_closed_before_arm(self):
        with HelperFixture(probe.HELPER) as fixture:
            fixture.configure(drop_response=True)
            failed = fixture.run('snapshot')
            self.assertNotEqual(failed.returncode, 0)
            self.assertNotIn('SNAPSHOT verified=1', failed.stdout)
            self.assertEqual(fixture.writes(), [])
            self.assertFalse((fixture.state / 'guard.ready').exists())

    def test_symlink_lock_paths_never_truncate_or_write_another_file(self):
        for symlink_directory in (False, True):
            with self.subTest(directory=symlink_directory), HelperFixture(probe.HELPER) as fixture:
                self.assert_action_ok(fixture, 'snapshot')
                important = fixture.root / 'untouched-config'
                important.write_text('do not truncate\n', encoding='utf-8')
                lock_dir = fixture.root / 'global-lock'
                if symlink_directory:
                    other = fixture.root / 'other-directory'
                    other.mkdir()
                    lock_dir.symlink_to(other, target_is_directory=True)
                else:
                    lock_dir.mkdir(mode=0o700)
                    (lock_dir / 'active.lock').symlink_to(important)
                failed = fixture.run('arm')
                self.assertNotEqual(failed.returncode, 0)
                self.assertEqual(important.read_text(encoding='utf-8'), 'do not truncate\n')
                self.assertEqual(fixture.writes(), [])

    def test_level_host_protocol_and_masked_writes_restore_all_words(self):
        for level in ('off', 'on'):
            with self.subTest(level=level), HelperFixture(probe.HELPER) as fixture:
                before = fixture.fields()
                config = (fixture.root / 'system').read_bytes()

                class ShellTransport:
                    def action(inner_self, action):
                        return self.assert_action_ok(fixture, action)

                messages = []
                probe.run_level_probe(ShellTransport(), level, messages.append)
                self.assertEqual(fixture.fields(), {
                    'd032': '0x0080', 'd040': '0x0000',
                    'd044': '0x00a7' if level == 'on' else '0x00a6'})
                self.assertIn('restoration PENDING', '\n'.join(messages))
                self.assertFalse((fixture.state / 'restored').exists())
                restored = self.assert_action_ok(fixture, 'restore')
                self.assertIn('RESTORED_FIELDS d032=0x00a7 d040=0x0001 d044=0x00a7 verified=1', restored)
                self.assertEqual(fixture.fields(), before)
                self.assertEqual((fixture.root / 'system').read_bytes(), config)
                masks = {0xd032: 0x0027, 0xd040: 1, 0xd044: 1}
                for write in fixture.writes():
                    self.assertEqual((write['phy'], write['devad']), (5, 0x1f))
                    self.assertIn(write['reg'], masks)
                    original = int(before[f'{write["reg"]:04x}'], 16)
                    self.assertEqual((write['value'] ^ original) & ~masks[write['reg']], 0)

    def test_each_level_field_write_failure_or_mismatch_recovers_remaining_fields(self):
        for reg in ('0xd032', '0xd040', '0xd044'):
            for injection in ('fail_write_reg_once', 'mismatch_write_reg_once'):
                with self.subTest(reg=reg, injection=injection), HelperFixture(probe.HELPER) as fixture:
                    before = fixture.fields()
                    self.assert_action_ok(fixture, 'snapshot')
                    self.assert_action_ok(fixture, 'arm-level-0')
                    fixture.configure(**{injection: reg})
                    failed = fixture.run('level-off')
                    self.assertNotEqual(failed.returncode, 0)
                    self.assertTrue((fixture.state / 'dirty').exists())
                    self.assertFalse((fixture.state / 'cancelled').exists())
                    self.assert_action_ok(fixture, 'restore')
                    self.assertEqual(fixture.fields(), before)

    def test_one_restore_failure_does_not_skip_other_level_fields_or_cancel_guard(self):
        for reg in ('0xd032', '0xd040', '0xd044'):
            with self.subTest(reg=reg), HelperFixture(probe.HELPER) as fixture:
                before = fixture.fields()
                self.assert_action_ok(fixture, 'snapshot')
                self.assert_action_ok(fixture, 'arm-level-0')
                self.assert_action_ok(fixture, 'level-off')
                fixture.configure(fail_restore_reg=reg)
                failed = fixture.run('restore')
                self.assertNotEqual(failed.returncode, 0)
                self.assertNotIn('RESTORED value=', failed.stdout)
                self.assertFalse((fixture.state / 'cancelled').exists())
                for key, value in fixture.fields().items():
                    if key != reg[2:]:
                        self.assertEqual(value, before[key])
                fixture.configure()
                self.assert_action_ok(fixture, 'restore')
                self.assertEqual(fixture.fields(), before)

    def test_level_guard_recovers_all_fields_after_host_exit(self):
        with HelperFixture(probe.HELPER) as fixture:
            before = fixture.fields()
            self.assert_action_ok(fixture, 'snapshot')
            self.assert_action_ok(fixture, 'arm-level-0')
            self.assert_action_ok(fixture, 'level-off')
            self.assertIn('GUARD_RESTORED verified=1', fixture.wait_for_guard(timeout=40))
            self.assertEqual(fixture.fields(), before)
            writes = fixture.writes()
            for action in ('level-off', 'level-on', 'on', 'polarity-0-inverted'):
                self.assertNotEqual(fixture.run(action).returncode, 0)
                self.assertEqual(fixture.writes(), writes)
            self.assert_action_ok(fixture, 'restore')
            self.assertEqual(fixture.writes(), writes)

    def test_level_guard_link_change_recovers_all_fields_early(self):
        with HelperFixture(probe.HELPER) as fixture:
            before = fixture.fields()
            self.assert_action_ok(fixture, 'snapshot')
            self.assert_action_ok(fixture, 'arm-level-0')
            self.assert_action_ok(fixture, 'level-off')
            fixture.write('carrier', '0\n')
            self.assertIn('GUARD_RESTORED verified=1', fixture.wait_for_guard(timeout=5))
            self.assertEqual(fixture.fields(), before)
            self.assertIn('GUARD_LINK_CHANGED', (fixture.state / 'guard.log').read_text(encoding='utf-8'))

    def test_legacy_arming_cannot_write_event_fields_or_mix_level_scope(self):
        for arm in ('arm', 'arm-hold-0'):
            with self.subTest(arm=arm), HelperFixture(probe.HELPER) as fixture:
                before = fixture.fields()
                self.assert_action_ok(fixture, 'snapshot')
                self.assert_action_ok(fixture, arm)
                for action in ('level-off', 'level-on'):
                    self.assertNotEqual(fixture.run(action).returncode, 0)
                self.assertEqual(fixture.writes(), [])
                self.assert_action_ok(fixture, 'restore')
                self.assertEqual(fixture.fields(), before)
        with HelperFixture(probe.HELPER) as fixture:
            self.assert_action_ok(fixture, 'snapshot')
            self.assert_action_ok(fixture, 'arm-level-0')
            self.assert_action_ok(fixture, 'level-off')
            before = fixture.writes()
            for action in ('level-on', 'level-off', 'on', 'off', 'polarity-0-inverted'):
                self.assertNotEqual(fixture.run(action).returncode, 0)
                self.assertEqual(fixture.writes(), before)
            self.assert_action_ok(fixture, 'restore')

    def test_level_restore_preserves_external_bits_and_refuses_full_success(self):
        with HelperFixture(probe.HELPER) as fixture:
            before = fixture.fields()
            self.assert_action_ok(fixture, 'snapshot')
            self.assert_action_ok(fixture, 'arm-level-0')
            self.assert_action_ok(fixture, 'level-off')
            registers = json.loads((fixture.root / 'registers.json').read_text(encoding='utf-8'))
            registers['1f/d032'] = '0x0180'
            fixture.write('registers.json', json.dumps(registers))
            failed = fixture.run('restore')
            self.assertNotEqual(failed.returncode, 0)
            self.assertNotIn('RESTORED value=', failed.stdout)
            self.assertFalse((fixture.state / 'cancelled').exists())
            self.assertFalse((fixture.state / 'restored').exists())
            self.assertEqual(fixture.fields(), {**before, 'd032': '0x01a7'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
