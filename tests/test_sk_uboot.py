#!/usr/bin/env python3
"""Portable offline U-Boot compatibility checks; no serial/SSH dependencies."""
import copy
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import sk_uboot as u

IMAGE = dict(bytes=12095028, crc32='06525abd', hashes=[
    dict(node='/images/kernel-1', algorithm='crc32', digest='3522e3f0'),
    dict(node='/images/kernel-1', algorithm='sha1', digest='920995fd5f9aa767918d392257a3f563e79507a1'),
    dict(node='/images/fdt-1', algorithm='crc32', digest='d8a225b1'),
    dict(node='/images/fdt-1', algorithm='sha1', digest='a036e009480a7c3563f1e7e6402fe074b7607a8b')])


def fixture(booting=False):
    text = (u.BOOT if booting else u.START) + '\r\n'
    text += 'Incorrect name OpenWrt skyworth_sk-d840n in descriptor\r\nOpenWrt skyworth_sk-d840n is not support.\r\n'
    for kind in ('kernel', 'fdt'):
        text += "   Using 'config-1' configuration\r\n   Trying '%s-1' %s subimage\r\n" % (kind, kind)
        for item in IMAGE['hashes']:
            if item['node'] == '/images/' + kind + '-1':
                text += '     Hash algo:    %s\r\n     Hash value:   %s\r\n' % (item['algorithm'], item['digest'])
        text += '   Verifying Hash Integrity ... crc32+ sha1+ OK\r\n'
    return text + ('   Uncompressing Kernel Image\r\nStarting kernel ...\r\n' if booting else '=> ')


CRC = 'crc32 0x89000000 b88e34\r\ncrc32 for 89000000 ... 89b88e33 ==> 06525abd\r\n=> '


class FitTests(unittest.TestCase):
    def test_staged_and_full_boot_each_verify_both_images(self):
        self.assertEqual(len(u.fit_verified(u.START, fixture(), IMAGE)), 2)
        self.assertEqual(len(u.fit_verified(u.BOOT, fixture(True), IMAGE, booting=True)), 2)

    def test_missing_bad_duplicate_or_wrong_hash_configuration_rejected(self):
        original = fixture()
        for bad in (original.replace('crc32+ sha1+ OK', 'crc32+ sha1+ BAD', 1),
                    original.replace('3522e3f0', '00000000'), original.replace('d8a225b1', '00000000'),
                    original.replace('sha1', 'sha256', 1), original.replace("Trying 'fdt-1'", "Trying 'wrong'"),
                    original.replace("'config-1'", "'config-2'", 1),
                    original.replace('Hash value:   a036e009480a7c3563f1e7e6402fe074b7607a8b', 'Hash value: '),
                    original.replace('=> ', 'Verifying Hash Integrity ... crc32+ sha1+ OK\n=> ')):
            with self.subTest(bad=bad[-80:]), self.assertRaises(AssertionError):
                u.fit_verified(u.START, bad, IMAGE)

    def test_early_execution_and_missing_prompt_rejected(self):
        for end in ('Starting kernel ...', 'Uncompressing Kernel Image\n=> ', 'Loading Device Tree to 90000000 ... OK\n=> ', ''):
            with self.assertRaises(AssertionError): u.fit_verified(u.START, fixture().replace('=> ', end), IMAGE)

    def test_missing_or_duplicate_manifest_hash_rejected(self):
        for hashes in (IMAGE['hashes'][:-1], IMAGE['hashes']+[IMAGE['hashes'][0]], [IMAGE['hashes'][0]]*4):
            with self.assertRaises(AssertionError): u.fit_verified(u.START, fixture(), dict(IMAGE, hashes=hashes))

    def test_wrong_command_echo_or_full_boot_without_kernel_start_rejected(self):
        with self.assertRaises(AssertionError): u.fit_verified(u.START, fixture().replace(u.START, 'iminfo'), IMAGE)
        with self.assertRaises(AssertionError): u.fit_verified(u.BOOT, fixture(True).replace('Starting kernel', 'Not starting'), IMAGE, booting=True)

    def test_crc_validates_exact_address_length_and_value(self):
        u.crc_verified(CRC, IMAGE)
        for bad in (CRC.replace('89b88e33', '89b88e34'), CRC.replace('89000000 ...', '88000000 ...'),
                    CRC.replace('06525abd', '00000000'), CRC + CRC):
            with self.assertRaises(AssertionError): u.crc_verified(bad, IMAGE)

    def test_capability_probe_is_required(self):
        good = 'help bootm\nSub-commands to do part of the bootm sequence.\n start [addr [arg ...]]\n=> '
        u.capabilities(good)
        with self.assertRaises(AssertionError): u.capabilities(good.replace('start [addr [arg ...]]', 'go'))

    def test_errors_stop_even_without_prompt(self):
        for error in ("Unknown command 'iminfo'", 'ERROR: Bad FIT', 'Bad Data CRC', 'Usage:\nbootm ...'):
            with self.assertRaises(AssertionError): u.check_response(u.START, u.START+'\n'+error)


class BootOrderTests(unittest.TestCase):
    def run_flow(self, start=None, crc=CRC, power_error=None):
        send = Mock(side_effect=[fixture() if start is None else start, crc])
        begin = Mock(return_value={'id': 1})
        wait = Mock(return_value=fixture(True))
        power = Mock(side_effect=power_error)
        return send, begin, wait, power

    def test_success_order_and_returned_evidence(self):
        callbacks = self.run_flow()
        result = u.verified_boot(*callbacks, IMAGE)
        self.assertEqual(len(result['start_images']), 2)
        callbacks[1].assert_called_once_with(u.BOOT)
        callbacks[3].assert_called_once()

    def test_bad_start_never_sends_boot(self):
        callbacks = self.run_flow(start=fixture().replace('3522e3f0', '00000000'))
        with self.assertRaises(AssertionError): u.verified_boot(*callbacks, IMAGE)
        callbacks[1].assert_not_called(); callbacks[2].assert_not_called(); callbacks[3].assert_not_called()

    def test_bad_crc_never_sends_boot(self):
        callbacks = self.run_flow(crc=CRC.replace('06525abd', '00000000'))
        with self.assertRaises(AssertionError): u.verified_boot(*callbacks, IMAGE)
        callbacks[1].assert_not_called(); callbacks[2].assert_not_called()

    def test_unsafe_power_never_sends_boot(self):
        callbacks = self.run_flow(power_error=AssertionError('late physical power window'))
        with self.assertRaises(AssertionError): u.verified_boot(*callbacks, IMAGE)
        callbacks[1].assert_not_called(); callbacks[2].assert_not_called()


class TransactionTests(unittest.TestCase):
    def state(self, output=None, done=True):
        output = fixture() if output is None else output
        ticket = dict(id=3, cmd=u.START, start=100, boot_offset=10)
        state = dict(bytes_received=100+len(output), boots=[dict(offset=10)], tail=fixture(),
                     transaction=dict(ticket, end=100+len(output), output=output, done=done))
        return ticket, state

    def test_partial_response_does_not_complete_from_old_tail(self):
        ticket, state = self.state(output=u.START+'\n', done=False)
        self.assertIsNone(u.transaction_output(ticket, state, 'Verifying Hash Integrity'))

    def test_complete_missing_output_does_not_use_old_tail(self):
        ticket, state = self.state(output=u.START+'\n=> ')
        with self.assertRaises(AssertionError): u.transaction_output(ticket, state, 'Verifying Hash Integrity')

    def test_current_transaction_only(self):
        ticket, state = self.state()
        self.assertEqual(u.transaction_output(ticket, state, 'Verifying Hash Integrity'), fixture().replace('\r', ''))

    def test_replay_cursor_restart_and_truncation_rejected(self):
        ticket, good = self.state()
        cases = []
        for key,value in [('id',2),('cmd','iminfo'),('start',90),('boot_offset',9),('end',99),
                          ('truncated',True),('cancelled','restart')]:
            state = copy.deepcopy(good); state['transaction'][key] = value; cases.append(state)
        state=copy.deepcopy(good); state['boots'][-1]['offset']=11; cases.append(state)
        for state in cases:
            with self.assertRaises(AssertionError): u.transaction_output(ticket,state,'Verifying Hash Integrity')


class BootAndLogsTests(unittest.TestCase):
    def test_version_echo_is_not_physical_boot(self):
        banner=u.BANNER.encode()+b'\r\n'
        self.assertEqual(u.boot_banners(b'version\r\n'+banner,0,-1),[])
        real=b'ddr init ok\r\n'+banner
        found=u.boot_banners(real,100,-1)
        self.assertEqual(found,[(100+real.index(b'U-Boot'),u.BANNER)])
        self.assertEqual(u.boot_banners(real,100,found[0][0]),[])

    def test_ramoops_is_not_an_oops(self):
        u.logs_clean('[1.0] ramoops: ready','')
        for error in ('Oops: fatal','Kernel panic','BUG: bad','Call Trace:','jffs2: error'):
            with self.assertRaises(AssertionError): u.logs_clean(error,'')

    def test_crc_warning_offsets_and_values_not_suppressed(self):
        old='jffs2: notice: (5) check_node_data: wrong data CRC at 0x1234: read 0x1234, calculated 0xabcd'
        u.logs_clean(old.replace('(5)','(7)'),old)
        with self.assertRaises(AssertionError): u.logs_clean(old.replace('0xabcd','0xabce'),old)

    def test_eof_warning_requires_exact_published_and_live_word(self):
        image=b'\xff'*131072+b'\xde\xad\xc0\xde'
        warning=u.verified_eof_warning(image,image[-4:])
        u.logs_clean(warning,'',verified_eof=warning)
        for payload,live in ((image,b'bad!'),(image[:-1],image[-4:]),(image[:-4]+b'bad!',image[-4:])):
            with self.assertRaises(AssertionError): u.verified_eof_warning(payload,live)
        with self.assertRaises(AssertionError): u.logs_clean(warning.replace('00020000','00040000'),'',verified_eof=warning)

    def test_safety_checks_survive_python_optimization(self):
        script="import sk_uboot as u\ntry: u.capabilities('bad')\nexcept AssertionError: raise SystemExit(0)\nraise SystemExit(1)"
        result=subprocess.run([sys.executable,'-O','-c',script],cwd=Path(u.__file__).parent,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr.decode())


if __name__ == '__main__':
    unittest.main(verbosity=2)
