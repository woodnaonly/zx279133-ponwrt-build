"""Resident prerequisites against synthetic NAND only; no device is opened."""
import ctypes as C
import hashlib
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.core import Core, Identity, Result, Task, UbootBudget, VERIFY_IDENTITY, PREPARE_UBOOT, NAND_NOW, AUTHORIZE_NO_RESCUE
from host.simulator import SparseNand
from fixtures import boot, nand_seed, system


class ResidentCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = Core(testing=True)

    def identity(self, seed=None, abi=None):
        seed = nand_seed() if seed is None else seed
        return Identity((abi or self.core.base).encode(), hashlib.sha256(seed[0x20000:0x180000]).hexdigest().encode())

    def patched(self):
        seed = bytearray(nand_seed())
        seed[0x22000] ^= 1
        return bytes(seed)

    def validate(self, data, identity):
        result, error = Result(), C.create_string_buffer(192)
        rc = self.core.lib.bw_validate_identity(data, len(data), C.byref(identity), 0, C.byref(result), error)
        return rc, error.value.decode(), result

    def execute(self, data, nand, identity):
        error, scratch = C.create_string_buffer(192), C.create_string_buffer(0x20000)
        rc = self.core.lib.bw_execute_identity(data, len(data), C.byref(identity), 0,
                                              C.byref(nand.port), scratch, len(scratch), error)
        self.assertEqual(nand.errors, [])
        return rc, error.value.decode()

    def test_patched_image_uses_unchanged_vendor_abi_and_new_package_base(self):
        identity = self.identity(self.patched())
        self.assertNotEqual(identity.vendor_abi_sha256, identity.installed_sha256)
        data = boot(identity.installed_sha256.decode())
        rc, error, result = self.validate(data, identity)
        self.assertEqual(rc, 0, error)
        self.assertEqual(result.kind, 2)
        rc, error, _ = self.validate(boot(self.core.base), identity)
        self.assertNotEqual(rc, 0)
        self.assertIn("CONTROL", error)
        # Legacy production APIs do not suddenly trust arbitrary patched hashes.
        with self.assertRaises(ValueError):
            self.core.validate(data, base=identity.installed_sha256.decode())

    def test_unknown_abi_cannot_be_replaced_by_the_installed_image_hash(self):
        identity = self.identity(self.patched(), abi="0" * 64)
        rc, error, _ = self.validate(system(), identity)
        self.assertNotEqual(rc, 0)
        self.assertIn("vendor ABI", error)
        nand = SparseNand(self.patched())
        rc, _ = self.execute(system(), nand, identity)
        self.assertNotEqual(rc, 0)
        self.assertEqual(nand.events, [])

    def test_capability_bits_cannot_replace_patched_image_abi_verification(self):
        seed = self.patched()
        identity = self.identity(seed)
        nand = SparseNand(seed, capabilities=0x7f)
        rc, error = self.execute(system(), nand, identity)
        self.assertNotEqual(rc, 0)
        self.assertIn("native vendor ABI", error)
        self.assertEqual(nand.events, [])

    def test_native_identity_callback_can_accept_or_reject_a_patched_image(self):
        seed = self.patched()
        identity = self.identity(seed)
        for accepted in (False, True):
            with self.subTest(accepted=accepted):
                nand, calls = SparseNand(seed), []
                def verify(ctx, observed):
                    calls.append((observed.contents.vendor_abi_sha256, observed.contents.installed_sha256))
                    return 0 if accepted else -1
                callback = VERIFY_IDENTITY(verify)
                nand.port.verify_identity = callback
                rc, error = self.execute(boot(identity.installed_sha256.decode()), nand, identity)
                self.assertEqual(len(calls), 1)
                if accepted:
                    self.assertEqual(rc, 0, error)
                    self.assertEqual(nand.read(0x20000, 0x160000), b"C" * 0x160000)
                    self.assertTrue(all(0x20000 <= off < 0x180000 for _, off in nand.events))
                else:
                    self.assertNotEqual(rc, 0)
                    self.assertEqual(nand.events, [])

    def test_installed_image_change_after_confirmation_is_rejected(self):
        nand = SparseNand(nand_seed())
        identity = self.identity()
        nand.blocks[0x20000][0] ^= 1
        callback = VERIFY_IDENTITY(lambda ctx, observed: 0)
        nand.port.verify_identity = callback
        rc, error = self.execute(system(), nand, identity)
        self.assertNotEqual(rc, 0)
        self.assertIn("confirmed image", error)
        self.assertEqual(nand.events, [])

    def test_task_confirmation_requires_current_image_task_package_and_token(self):
        data, task = system(), Task()
        buffer = C.create_string_buffer(len(data))
        identity, token, api = self.identity(self.patched()), b"a" * 64, self.core.lib
        self.assertEqual(api.bw_task_init_identity(C.byref(task), buffer, len(buffer), C.byref(identity), 0, token), 0)
        self.assertEqual(api.bw_task_begin(C.byref(task), len(data), 42), 0)
        self.assertEqual(api.bw_task_feed(C.byref(task), data, len(data)), 0)
        self.assertEqual(api.bw_task_finish(C.byref(task)), 0)
        digest = task.result.package_sha256
        self.assertNotEqual(api.bw_task_confirm(C.byref(task), 42, digest, token), 0)
        for task_id, package, installed, auth in (
                (41, digest, task.base, token), (42, b"0" * 64, task.base, token),
                (42, digest, self.core.base.encode(), token), (42, digest, task.base, b"b" * 64)):
            self.assertNotEqual(api.bw_task_confirm_identity(C.byref(task), task_id, package, installed, auth), 0)
            self.assertEqual(task.state, 3)
        for _ in range(2):
            self.assertEqual(api.bw_task_confirm_identity(C.byref(task), 42, digest, task.base, token), 0)
            self.assertEqual(task.error, b"")
        self.assertEqual(task.state, 4)
        # A rejected duplicate must not leave a stale error on the next valid
        # idempotent confirmation, or permit a second queued execution.
        self.assertNotEqual(api.bw_task_confirm_identity(C.byref(task), 42, digest, task.base, b"b" * 64), 0)
        self.assertTrue(task.error)
        self.assertEqual(api.bw_task_confirm_identity(C.byref(task), 42, digest, task.base, token), 0)
        self.assertEqual(task.error, b"")
        self.assertEqual(task.state, 4)

    def test_uboot_preflight_cannot_be_replaced_by_all_capability_bits(self):
        for missing in ("prepare", "time", "failed-prepare", "unbounded"):
            with self.subTest(missing=missing):
                nand = SparseNand(nand_seed(), capabilities=0x7f)
                if missing == "prepare":
                    nand.port.prepare_uboot_write = PREPARE_UBOOT()
                elif missing == "time":
                    nand.port.milliseconds = NAND_NOW()
                elif missing == "failed-prepare":
                    nand.prepare_error = True
                else:
                    nand.budget = {"page_max_ms": 0}
                rc, _ = self.execute(boot(self.core.base), nand, self.identity())
                self.assertNotEqual(rc, 0)
                self.assertEqual(nand.events, [])

    def test_explicit_one_operation_waiver_does_not_fabricate_rescue_evidence(self):
        data, identity = boot(self.core.base), self.identity()
        calls = []
        def authorize(ctx, installed, digest):
            observed = (installed.contents.installed_sha256, digest)
            expected = (identity.installed_sha256, hashlib.sha256(data).hexdigest().encode())
            calls.append(observed)
            return 0 if len(calls) == 1 and observed == expected else -1
        callback = AUTHORIZE_NO_RESCUE(authorize)
        for attempt in range(2):
            nand = SparseNand(nand_seed(), capabilities=0x7f & ~8)
            nand.port.authorize_no_rescue = callback
            rc, error = self.execute(data, nand, identity)
            self.assertEqual(nand.port.capabilities & 8, 0)
            if attempt == 0:
                self.assertEqual(rc, 0, error)
                self.assertEqual(len([event for event in nand.events if event[0] == "erase"]), 11)
                self.assertTrue(all(0x20000 <= off < 0x180000 for _, off in nand.events))
            else:
                self.assertNotEqual(rc, 0)
                self.assertIn("denied/consumed", error)
                self.assertEqual(nand.events, [])
        self.assertEqual(len(calls), 2)

    def test_missing_or_denied_waiver_erases_nothing(self):
        for denied in (False, True):
            nand = SparseNand(nand_seed(), capabilities=0x7f & ~8)
            callback = AUTHORIZE_NO_RESCUE(lambda ctx, identity, digest: -1)
            if denied:
                nand.port.authorize_no_rescue = callback
            rc, error = self.execute(boot(self.core.base), nand, self.identity())
            self.assertNotEqual(rc, 0)
            self.assertIn("rescue", error)
            self.assertEqual(nand.events, [])

    def test_rescue_waiver_does_not_bypass_container_or_real_preflight(self):
        for fault in ("container", "prepare", "time", "unknown-identity"):
            nand = SparseNand(nand_seed(), capabilities=0x7f & ~8)
            calls = []
            callback = AUTHORIZE_NO_RESCUE(lambda ctx, identity, digest: calls.append(digest) or 0)
            nand.port.authorize_no_rescue = callback
            identity = self.identity()
            if fault == "container":
                nand.port.capabilities &= ~16
            elif fault == "prepare":
                nand.port.prepare_uboot_write = PREPARE_UBOOT()
            elif fault == "time":
                nand.clock = 300000
            else:
                identity = self.identity(abi="0"*64)
            rc, _ = self.execute(boot(self.core.base), nand, identity)
            self.assertNotEqual(rc, 0)
            self.assertEqual(nand.events, [])
            self.assertEqual(calls, [])

    def test_slow_or_mutating_waiver_cannot_bypass_last_erase_checks(self):
        for fault in ("late", "mutated"):
            nand = SparseNand(nand_seed(), capabilities=0x7f & ~8)
            data = C.create_string_buffer(boot(self.core.base))
            def authorize(ctx, identity, digest):
                if fault == "late":
                    nand.clock = 300000
                else:
                    data[1000] = b"X"
                return 0
            callback = AUTHORIZE_NO_RESCUE(authorize)
            nand.port.authorize_no_rescue = callback
            rc, error = self.execute(data, nand, self.identity())
            self.assertNotEqual(rc, 0)
            self.assertIn("minute 5" if fault == "late" else "candidate changed", error)
            self.assertEqual(nand.events, [])

    def test_rescue_waiver_is_not_a_system_update_authorization(self):
        nand = SparseNand(nand_seed(), capabilities=0x7f & ~8)
        calls = []
        callback = AUTHORIZE_NO_RESCUE(lambda ctx, identity, digest: calls.append(digest) or -1)
        nand.port.authorize_no_rescue = callback
        rc, error = self.execute(system(), nand, self.identity())
        self.assertEqual(rc, 0, error)
        self.assertEqual(calls, [])
        self.assertTrue(nand.events)

    def test_late_preflight_callback_cannot_erase_after_minute_five(self):
        nand = SparseNand(nand_seed())
        def progress(phase, done, total):
            if phase == "erase":
                nand.clock = 300000
        nand.progress = progress
        rc, error = self.execute(boot(self.core.base), nand, self.identity())
        self.assertNotEqual(rc, 0)
        self.assertIn("minute 5", error)
        self.assertEqual(nand.events, [])

    def test_candidate_mutation_in_last_preflight_callback_erases_nothing(self):
        data = C.create_string_buffer(boot(self.core.base))
        nand = SparseNand(nand_seed())
        def progress(phase, done, total):
            if phase == "erase":
                data[1000] = b"X"
        nand.progress = progress
        rc, error = self.execute(data, nand, self.identity())
        self.assertNotEqual(rc, 0)
        self.assertIn("candidate changed", error)
        self.assertEqual(nand.events, [])

    def test_boot_badblock_and_failed_readback_do_not_report_success(self):
        for defect in ("bad-block", "readback"):
            nand = SparseNand(nand_seed())
            if defect == "bad-block":
                nand.bad.add(0x60000)
            else:
                nand.corrupt_readback = 0x60000
            rc, error = self.execute(boot(self.core.base), nand, self.identity())
            self.assertNotEqual(rc, 0)
            if defect == "bad-block":
                self.assertIn("BTL remapping", error)
                self.assertEqual(nand.events, [])
            else:
                self.assertIn("readback SHA256", error)
                self.assertEqual(len([event for event in nand.events if event[0] == "erase"]), 11)


class PowerBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = Core(testing=True)

    def budget(self, **changes):
        values = dict(power_started_ms=10000, power_uncertainty_ms=10, erase_max_ms=10,
                      page_max_ms=1, read_max_ms=2, hash_max_ms=2, bad_query_max_ms=1,
                      service_max_ms=1, fixed_max_ms=100)
        values.update(changes)
        return UbootBudget(**values)

    def check(self, budget, now=11000):
        total, error = C.c_uint64(123), C.create_string_buffer(192)
        rc = self.core.lib.bw_uboot_budget_check(C.byref(budget), now, C.byref(total), error)
        if rc:
            self.assertEqual(total.value, 0)
        return rc, total.value, error.value.decode()

    def test_budget_counts_full_window_and_all_protected_region_reads(self):
        rc, bound, error = self.check(self.budget())
        self.assertEqual(rc, 0, error)
        self.assertEqual(bound, 11 * 10 + 704 + 96 * 2 + 96 * 2 + 11 + 838 + 100)

    def test_missing_limits_overflow_and_nonmonotonic_clocks_fail(self):
        for field in ("erase_max_ms", "page_max_ms", "read_max_ms", "hash_max_ms",
                      "bad_query_max_ms", "service_max_ms", "fixed_max_ms"):
            for value in (0, (1 << 64) - 1):
                with self.subTest(field=field, value=value):
                    self.assertNotEqual(self.check(self.budget(**{field: value}))[0], 0)
        self.assertNotEqual(self.check(self.budget(), now=9999)[0], 0)
        self.assertNotEqual(self.check(self.budget(power_uncertainty_ms=(1 << 64) - 1))[0], 0)

    def test_continuous_policy_keeps_full_bounds_without_periodic_cutoff(self):
        periodic=self.budget()
        continuous=self.budget(power_policy=1)
        self.assertNotEqual(self.check(periodic,now=2000000)[0],0)
        rc,bound,error=self.check(continuous,now=2000000)
        self.assertEqual(rc,0,error);self.assertEqual(bound,self.check(periodic)[1])
        for values in ({"power_policy":2},{"power_policy":1,"read_max_ms":0},
                       {"power_policy":1,"erase_max_ms":0},{"power_policy":1,"fixed_max_ms":(1<<64)-1}):
            self.assertNotEqual(self.check(self.budget(**values),now=2000000)[0],0)

    def test_native241_system_budget_includes_erased_tail_identity_and_cleanup(self):
        result=self.core.validate(system(layout="native241"),layout="native241")
        b=self.budget();total=C.c_uint64();error=C.create_string_buffer(192)
        check=self.core.lib.bw_system_budget_bound
        self.assertEqual(check(C.byref(result),C.byref(b),C.byref(total),error),0,error.value)
        blocks=sum(s.capacity//0x20000 for s in result.writes)
        pages=sum((s.length+2047)//2048 for s in result.writes)
        reads=213+sum((s.length+0x1ffff)//0x20000+(s.capacity-s.length+0x1ffff)//0x20000 for s in result.writes)
        self.assertEqual(blocks,2000)
        self.assertEqual(total.value,blocks*10+pages+reads*4+(blocks+2*reads)+(blocks+pages+reads)+100)
        for changes in ({"read_max_ms":0},{"hash_max_ms":0},{"page_max_ms":(1<<64)-1}):
            bad=self.budget(**changes)
            self.assertNotEqual(check(C.byref(result),C.byref(bad),C.byref(total),error),0)
            self.assertEqual(total.value,0)
        result.writes[0].capacity-=0x100000
        self.assertNotEqual(check(C.byref(result),C.byref(b),C.byref(total),error),0)
        self.assertEqual(total.value,0)

    def test_first_erase_deadline_includes_power_timestamp_uncertainty(self):
        self.assertEqual(self.check(self.budget(power_uncertainty_ms=0), now=309999)[0], 0)
        self.assertNotEqual(self.check(self.budget(power_uncertainty_ms=0), now=310000)[0], 0)
        self.assertNotEqual(self.check(self.budget(power_uncertainty_ms=1), now=309999)[0], 0)

    def test_exact_minute_fifteen_finish_is_rejected(self):
        elapsed = 1010
        overhead = 2147 - 100
        budget = self.budget(fixed_max_ms=900000 - elapsed - overhead)
        self.assertNotEqual(self.check(budget)[0], 0)
        budget.fixed_max_ms -= 1
        self.assertEqual(self.check(budget)[0], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
