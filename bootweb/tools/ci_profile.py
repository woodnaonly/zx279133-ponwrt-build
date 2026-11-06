"""Static CI target selection, never a probe report or installation authority."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.core import BASE
from host.image import regions
from host.reset import CONTROL_DTB_SHA256

PROFILE = ROOT / "profiles/native241-ci.json"
RESET_PINCTRL = {"register": "0x10e20000", "global_pin": 14, "shift": 29,
                 "width": 2, "mask": "0x60000000", "function": 1, "value": "0x20000000"}


def public_boot(data, lock=None):
    lock = lock or json.loads((ROOT / "tools/dependencies.lock.json").read_text(encoding="utf-8"))
    expected = lock["public_boot"]
    if (len(data) != expected["bytes"] or hashlib.sha256(data).hexdigest() != expected["sha256"] or
            hashlib.sha256(regions(data)["stage2"]).hexdigest() != BASE or expected["stage2_sha256"] != BASE):
        raise ValueError("CI requires the exact locked public svn308776 boot reference")
    return expected


def load_ci_profile(path, data):
    profile = json.loads(Path(path).read_text(encoding="utf-8"))
    expected = {"schema": "sk-d840n-native241-ci-profile-v1", "board": "skyworth_sk-d840n",
                "layout": "native241", "power_mode": "continuous_required",
                "vendor_abi_sha256": BASE, "control_dtb_sha256": CONTROL_DTB_SHA256,
                "reset_pinctrl_configuration": RESET_PINCTRL, "hardware_acceptance_complete": False,
                "installation_authorized": False, "web_uboot_enabled": False}
    if profile != expected or any(profile.get(k) is not False for k in
            ("hardware_acceptance_complete", "installation_authorized", "web_uboot_enabled")):
        raise ValueError("CI profile is not the fixed offline-only native241 target")
    public_boot(data)
    return {"ci_offline_build": True, "ci_profile_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            "hardware_acceptance_complete": False, "installation_authorized": False,
            "reset_static_configuration": {"control_dtb_sha256": CONTROL_DTB_SHA256,
                "pinctrl_configuration": dict(RESET_PINCTRL), "physical_reset_pass": False}}
