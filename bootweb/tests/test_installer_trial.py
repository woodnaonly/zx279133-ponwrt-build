"""Parser fault tests are synthetic; never hardware installation evidence."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
from host.uart import TrialError
from installer_trial import preflight_evidence,installation_evidence,final_evidence


class InstallerPreflightTests(unittest.TestCase):
    def fixture(self):
        manifest = dict(original_sha256="a" * 64, candidate_sha256="b" * 64, package_sha256="c" * 64,
                        package_bytes=1443840, protected_sha256=[str(i + 1) * 64 for i in range(4)])
        lines = ["BW_BOUNDED_SPI_ATTACHED writes=0"]
        for phase in ("before", "after"):
            for index, (off, size) in enumerate(((0, 0x20000), (0x180000, 0x20000), (0x1a0000, 0x60000), (0xb00000, 0x400000))):
                lines.append(f"BW_INSTALL_PROTECTED phase={phase} off={off:x} bytes={size:x} sha256={manifest['protected_sha256'][index]} matches_backup=1")
        lines.append(f"BW_INSTALL_RAM_READY original_sha256={'a'*64} candidate_sha256={'b'*64} package_sha256={'c'*64} package_bytes=1443840 bound_ms=579150 uptime_ms=100000")
        for off in range(0x20000, 0x180000, 0x20000):
            lines.append(f"BW_NAND_DRY_END rc=0 kind=erase offset={off:x} wren=1 loads=0 executes=1 hardware_mutations=0")
        lines += [f"BW_INSTALL_PREFLIGHT_CURRENT sha256={'a'*64} unchanged=1 candidate_readback_claimed=0",
                  "BW_INSTALL_NAND_RELEASE rc=0 reads=98 corrected_reads=0 erases=11 pages=300 skipped_ff=404 max_read_ms=340 max_bad_ms=1 max_erase_ms=1 max_page_ms=5 failed=0",
                  "BW_BOUNDED_SPI_DETACHED rc=0 calls=100 status_reads=10 max_ms=2 denied=0 pointer_restored=1",
                  "BW_INSTALL_EXIT rc=0 elapsed_ms=30000 stack_ok=1 authorization_consumed=0"]
        return manifest, "\n".join(lines)

    def test_complete_emulation_does_not_claim_candidate_readback(self):
        manifest, text = self.fixture(); result = preflight_evidence(text, manifest)
        self.assertEqual(result["hardware_mutations"], 0)
        self.assertEqual(result["emulated_pages"] + result["skipped_ff_pages"], 704)
        self.assertNotIn("nand_written", result)

    def test_changed_missing_and_duplicate_protected_evidence_fails(self):
        manifest, text = self.fixture()
        protected = next(line for line in text.splitlines() if "phase=after off=180000" in line)
        for value in (text.replace(protected, ""), text + "\n" + protected,
                      text.replace(protected, protected.replace("2" * 64, "f" * 64))):
            with self.assertRaises(TrialError): preflight_evidence(value, manifest)

    def test_wrong_candidate_pages_boundaries_errors_and_real_mode_fail(self):
        manifest, text = self.fixture()
        for old, new in (("candidate_sha256=" + "b" * 64, "candidate_sha256=" + "e" * 64),
                         ("pages=300", "pages=299"), ("offset=160000", "offset=180000"),
                         ("unchanged=1", "unchanged=0"), ("writes=0", "writes=1"),
                         ("pointer_restored=1", "pointer_restored=0")):
            with self.subTest(old=old), self.assertRaises(TrialError): preflight_evidence(text.replace(old, new), manifest)
        for marker in ("BW_INSTALL_REFUSED", "BW_INSTALL_ECC_ERROR", "BW_NAND_REAL_END"):
            with self.assertRaises(TrialError): preflight_evidence(text + "\n" + marker, manifest)

    def test_actual_evidence_cannot_be_claimed_from_a_successful_dry_run(self):
        manifest,text=self.fixture()
        with self.assertRaises(TrialError):installation_evidence(text,manifest)
        for value in ({"mode":"preflight","native_write_enabled":False},
                      {"mode":"install","native_write_enabled":True,"authorization":"preflight-no-write-authorization"}):
            with self.assertRaises(TrialError):final_evidence(value)


if __name__ == "__main__": unittest.main()
