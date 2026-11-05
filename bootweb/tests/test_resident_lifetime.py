"""Execute the native stop policy on the host, without MMIO or a device.

The production function body is compiled unchanged with only the clock and
resident state injected. This does not verify the native hardware adapter.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ResidentLifetimeTests(unittest.TestCase):
    def test_native_result_page_never_resumes_autoboot_after_an_accepted_task(self):
        source = (ROOT / "adapter/vendor_resident.c").read_text(encoding="utf-8")
        matches = re.findall(r"static int stop\(void \*unused\) \{.*?^\}", source, re.M | re.S)
        self.assertEqual(len(matches), 1, "the exact production stop policy must be found")
        zig = ROOT / ".build/toolchain/zig-x86_64-windows-0.14.1/zig.exe"
        compiler = [str(zig), "cc"] if zig.is_file() else [shutil.which("cc") or "cc"]
        harness = r"""
#include <stdint.h>
#include <stdio.h>
static int test_power;
static uint64_t test_now;
struct platform_state { int requested_reboot; };
static struct {
    int writing, prepared, failed, accepted;
    struct platform_state *platform;
    uint64_t end;
} resident;
static uint64_t now(void *unused) { (void)unused; return test_now; }
#define BW_NATIVE_CONTINUOUS_POWER test_power
""" + matches[0] + r"""
int main(void) {
    static const struct {
        int power, writing, prepared, failed, accepted, reboot, has_platform;
        uint64_t clock, end;
        int expected;
    } cases[] = {
        /* An idle service still expires. Continuous mode alone is no lock. */
        {1,0,0,0,0,0,1, 999,1000,0},
        {1,0,0,0,0,0,1,1000,1000,1},
        {1,0,0,0,0,0,0,1000,1000,1},
        /* Accepted confirmation survives expiry even BEFORE preparation. */
        {1,0,0,0,1,0,1,1000,1000,0},
        {1,0,0,0,1,0,1,2000000,1000,0},
        {1,0,0,1,1,0,1,2000000,1000,0},
        /* Prepared/success/failed results survive local and 17-minute ends. */
        {1,0,1,0,0,0,1,2000000,1000,0},
        {1,0,0,1,0,0,1,2000000,1000,0},
        {1,0,1,1,0,0,1,2000000,1000,0},
        {1,0,0,1,0,0,0,2000000,1000,0},
        /* A verified manual reboot may leave only after writing has ended. */
        {1,0,1,0,0,1,1,2000000,1000,1},
        {1,1,1,0,0,1,1,2000000,1000,0},
        {1,1,0,1,0,0,1,2000000,1000,0},
        /* Periodic builds keep their original idle/result deadline policy. */
        {0,0,0,0,0,0,1, 999,1000,0},
        {0,0,0,0,0,0,1,1000,1000,1},
        {0,0,1,0,0,0,1,1019999,2000000,0},
        {0,0,1,0,0,0,1,1020000,2000000,1},
        {0,0,0,1,0,0,1,1020000,2000000,1},
        {0,0,0,0,1,0,1,1020000,2000000,1},
        {0,1,1,0,0,0,1,2000000,1000,0},
        {0,1,1,0,0,1,1,2000000,1000,0}
    };
    unsigned i;
    for(i=0;i<sizeof(cases)/sizeof(cases[0]);i++) {
        struct platform_state platform = {cases[i].reboot};
        test_power=cases[i].power;test_now=cases[i].clock;
        resident.writing=cases[i].writing;resident.prepared=cases[i].prepared;
        resident.accepted=cases[i].accepted;
        resident.failed=cases[i].failed;resident.end=cases[i].end;
        resident.platform=cases[i].has_platform?&platform:0;
        if(stop(0)!=cases[i].expected) {
            fprintf(stderr,"native stop policy failed case %u\n",i);return 1;
        }
    }
    return 0;
}
"""
        with tempfile.TemporaryDirectory(prefix="bootweb-lifetime-") as folder:
            executable = Path(folder) / "resident-lifetime.exe"
            compiled = subprocess.run(
                [*compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                 "-x", "c", "-", "-o", str(executable)],
                input=harness, capture_output=True, text=True, encoding="utf-8", timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            checked = subprocess.run([str(executable)], capture_output=True, text=True,
                                     encoding="utf-8", timeout=10)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_continuous_idle_default_is_thirty_minutes_and_serial_buffer_fits(self):
        source = (ROOT / "adapter/resident-bootstrap.c").read_text(encoding="utf-8")
        self.assertIn("#ifdef BW_RESIDENT_NATIVE241_SERVICE\n#define BW_RESIDENT_SERVICE_MAX_MS 1800000u", source)
        self.assertIn("#else\n#define BW_RESIDENT_SERVICE_MAX_MS 180000u", source)
        self.assertIn("duration=BW_RESIDENT_SERVICE_MAX_MS", source)
        self.assertIn("duration>BW_RESIDENT_SERVICE_MAX_MS", source)
        self.assertIn("duration_text[8]", source)  # seven digits plus NUL
        adapter = (ROOT / "adapter/vendor_resident.c").read_text(encoding="utf-8")
        self.assertIn("(BW_NATIVE_CONTINUOUS_POWER?1800000u:180000u)", adapter)
        self.assertIn("duration>BW_RESIDENT_SERVICE_MAX_MS", adapter)

    def test_actual_prepare_latches_before_slow_hash_and_ecc_comparison(self):
        source = (ROOT / "adapter/vendor_resident.c").read_text(encoding="utf-8")
        stop = re.findall(r"static int stop\(void \*unused\) \{.*?^\}", source, re.M | re.S)
        prepare = re.findall(r"static int prepare_update\(.*?^\}", source, re.M | re.S)
        self.assertEqual((len(stop), len(prepare)), (1, 1))
        harness = r"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define BW_ERROR_SIZE 192
#define BW_NATIVE_CONTINUOUS_POWER 1
#define BW_NATIVE_TIMING_PROVEN 0
#define PRINT(...) ((void)0)
struct bw_result { int kind; char package_sha256[65]; };
struct bw_task { struct bw_result result; int continuous_power_confirmed;
    const void *buffer; size_t received; char base[65]; };
struct test_platform { int requested_reboot; uint64_t update_bound_ms; };
static struct {
    int writing, prepared, failed, accepted;
    struct test_platform *platform;
    uint64_t end;
    char installed_sha256[65], prepared_package[65];
    struct bw_result prepared_result;
} resident;
static uint8_t snapshot[0x600000];
static uint64_t clock_value;
static int heavy_calls, fault, stop_failed;
static uint64_t now(void *unused) { (void)unused; return clock_value; }
""" + stop[0] + r"""
static const char *update_lock(void *ctx,const struct bw_result *result) {
    (void)ctx;(void)result;return "";
}
static int bw_fail(char *error,const char *message) {strcpy(error,message);return -1;}
static int bw_string(const char *a,const char *b) {return strcmp(a,b)==0;}
static void bw_copy(void *a,const void *b,size_t n) {memcpy(a,b,n);}
static void bw_sha256(const void *data,size_t n,char out[65]) {
    (void)data;(void)n;clock_value=2000000;heavy_calls++;
    if(stop(0))stop_failed=1;
    memset(out,fault==1?'b':'a',64);out[64]=0;
}
static int compare(uint32_t off,uint32_t n,const uint8_t *expected) {
    (void)off;(void)n;(void)expected;heavy_calls++;
    if(stop(0))stop_failed=1;
    return fault==2?-1:0;
}
""" + prepare[0] + r"""
int main(void) {
    struct test_platform platform={0,25278200};
    struct bw_task task={0};char error[BW_ERROR_SIZE];int mode;
    for(mode=0;mode<4;mode++) {
        memset(&resident,0,sizeof(resident));resident.platform=&platform;
        resident.end=1000;clock_value=999;heavy_calls=stop_failed=0;
        fault=mode<3?mode:0;task.continuous_power_confirmed=mode!=3;
        memset(task.base,'a',64);task.base[64]=0;
        memcpy(task.result.package_sha256,task.base,65);
        memcpy(resident.installed_sha256,task.base,65);
        if(prepare_update(0,&task,error)!=(mode==0?0:-1))return 1;
        if(mode==3) {
            if(resident.accepted||heavy_calls)return 2;
        } else {
            if(!resident.accepted||stop_failed||stop(0))return 3;
            if(mode==0&&!resident.prepared)return 4;
            if(mode!=0&&resident.prepared)return 5;
        }
    }
    return 0;
}
"""
        # Task/result layout above deliberately contains only fields read by
        # the unmodified production function. Nothing can access real NAND.
        zig = ROOT / ".build/toolchain/zig-x86_64-windows-0.14.1/zig.exe"
        compiler = [str(zig), "cc"] if zig.is_file() else [shutil.which("cc") or "cc"]
        with tempfile.TemporaryDirectory(prefix="bootweb-prepare-") as folder:
            executable = Path(folder) / "resident-prepare.exe"
            compiled = subprocess.run([*compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                                       "-x", "c", "-", "-o", str(executable)],
                                      input=harness, capture_output=True, text=True, encoding="utf-8", timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            checked = subprocess.run([str(executable)], capture_output=True, text=True, encoding="utf-8", timeout=10)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)


if __name__ == "__main__":
    unittest.main()
