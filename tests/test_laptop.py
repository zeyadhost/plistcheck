import io
import plistlib

import pytest

from app import app, demo_config
from checker import ConfigError, analyze, profile_from


IGPU_PATH = "PciRoot(0x0)/Pci(0x2,0x0)"
LAPTOP_PROFILE = {"device": "laptop", "cpu": "whiskey-lake", "gpu": "intel-supported",
                  "macos": "14", "cfg_lock": "enabled", "vt_d": "enabled", "rebar": "disabled"}


def kext(name, path=None, **settings):
    return {"Arch": "Any", "BundlePath": (path or name) + ".kext", "Comment": "", "Enabled": True,
            "ExecutablePath": "Contents/MacOS/" + name, "MaxKernel": "", "MinKernel": "",
            "PlistPath": "Contents/Info.plist", **settings}


def ssdt(name, enabled=True):
    return {"Comment": "", "Enabled": enabled, "Path": name + ".aml"}


def xosi_patch(enabled=True):
    return {"Base": "", "BaseSkip": 0, "Comment": "", "Count": 0, "Enabled": enabled, "Find": b"_OSI",
            "Limit": 0, "Mask": b"", "OemTableId": b"", "Replace": b"XOSI", "ReplaceMask": b"",
            "Skip": 0, "TableLength": 0, "TableSignature": b""}


def clean_laptop_config():
    config = demo_config()
    config["PlatformInfo"]["Generic"].update(SystemProductName="MacBookPro15,2",
                                              SystemSerialNumber="SYNTHETIC001", MLB="SYNTHETICFIXTURE1",
                                              SystemUUID="459C4FB1-9F1D-4108-9219-2BA021A57F88",
                                              ROM=b"\x02\x20\x30\x40\x50\x60")
    config["DeviceProperties"]["Add"] = {IGPU_PATH: {"AAPL,ig-platform-id": bytes.fromhex("00009B3E")}}
    config["ACPI"]["Add"] = [ssdt(name) for name in ("SSDT-PLUG", "SSDT-EC-USBX-LAPTOP", "SSDT-PNLF", "SSDT-XOSI")]
    config["ACPI"]["Patch"] = [xosi_patch()]
    config["Kernel"]["Add"].extend(kext(name) for name in ("VoodooI2C", "VoodooI2CHID", "ECEnabler", "USBMap"))
    config["Kernel"]["Add"][-1]["ExecutablePath"] = ""
    config["Kernel"]["Quirks"].update(XhciPortLimit=False, AppleXcpmCfgLock=True, DisableIoMapper=True)
    for driver in config["UEFI"]["Drivers"]:
        driver["HideVerbose"] = False
    config["Misc"]["Security"].update(DmgLoading="Signed", SecureBootModel="Default")
    return config


def first_timer_laptop_config():
    config = clean_laptop_config()
    config["PlatformInfo"]["Generic"].update(SystemProductName="iMac19,1", SystemSerialNumber="", MLB="",
                                              SystemUUID="00000000-0000-0000-0000-000000000000", ROM=bytes(6))
    config["DeviceProperties"]["Add"][IGPU_PATH]["AAPL,ig-platform-id"] = "0900A53E"
    config["ACPI"]["Add"] = [ssdt("SSDT-PLUG"), ssdt("SSDT-EC-USBX-DESKTOP")]
    config["Kernel"]["Quirks"].update(XhciPortLimit=True, AppleXcpmCfgLock=False)
    config["Kernel"]["Add"][3:] = [
        kext("VoodooI2CHID"), kext("VoodooI2C"), kext("USBInjectAll"),
        kext("VoodooPS2Keyboard", "VoodooPS2Controller.kext/Contents/PlugIns/VoodooPS2Keyboard"),
        kext("VoodooPS2Trackpad", "VoodooPS2Controller.kext/Contents/PlugIns/VoodooPS2Trackpad"),
        kext("VoodooPS2Controller"),
    ]
    config["Kernel"]["Add"].insert(1, kext("SMCBatteryManager"))
    return config


@pytest.fixture
def clean_laptop():
    return clean_laptop_config()


@pytest.fixture
def first_timer_laptop():
    return first_timer_laptop_config()


def check(config, **overrides):
    return analyze(config, profile_from({**LAPTOP_PROFILE, **overrides}))


def findings(report):
    return {finding["id"]: finding for finding in report["findings"]}


def test_first_timer_laptop_api(first_timer_laptop):
    result = app.test_client().post("/api/check", data={
        **LAPTOP_PROFILE, "config": (io.BytesIO(plistlib.dumps(first_timer_laptop)), "first-timer.plist"),
    })
    assert result.status_code == 200
    report = result.json
    issues = findings(report)
    assert report["status"] == "issues"
    assert report["hardware_coverage"].startswith("Intel laptop rules ran")
    assert {"laptop-smbios", "igpu-platform-id-type", "laptop-xosi", "laptop-ec-desktop", "laptop-pnlf",
            "laptop-order-VoodooI2CHID", "laptop-battery", "cfglock", "xhci"} <= issues.keys()
    assert issues["laptop-battery"]["severity"] == "info"
    assert issues["igpu-platform-id-type"]["severity"] == "error"
    assert issues["laptop-xosi"]["source"].endswith("/config-laptop.plist/coffee-lake.html#patch")
    assert issues["cfglock"]["source"].endswith("/config-laptop.plist/coffee-lake.html#kernel")
    assert report["native"]["status"] == "failed"
    assert all(name in report["native"]["output"] for name in ("SMCBatteryManager", "VoodooPS2Keyboard", "VoodooPS2Trackpad"))


def test_clean_laptop_still_discloses_limits(clean_laptop):
    report = check(clean_laptop)
    assert report["native"]["status"] == "passed", report["native"]["output"]
    assert report["status"] == "no-known-issues", report["findings"]
    assert report["counts"]["error"] == report["counts"]["warning"] == 0
    assert "Exact framebuffer" in report["hardware_coverage"]
    assert "debug-args" in findings(report)


@pytest.mark.parametrize("device,cpu,message", [
    ("other", "coffee-lake", "Device-specific rules were not run"),
    ("laptop", "other", "No CPU-generation-specific laptop rules ran"),
    ("laptop", "zen", "No CPU-generation-specific laptop rules ran"),
    ("desktop", "ice-lake", "Laptop rules were not run"),
])
def test_incomplete_device_coverage(clean_laptop, device, cpu, message):
    report = check(clean_laptop, device=device, cpu=cpu)
    assert report["status"] == "incomplete"
    assert message in report["hardware_coverage"]
    assert "laptop-pnlf" not in findings(report)


def test_desktop_profile_does_not_run_laptop_checks(first_timer_laptop):
    report = check(first_timer_laptop, device="desktop", cpu="coffee-lake")
    assert report["hardware_coverage"] == "Desktop rules ran. Laptop rules were not run."
    assert not any(key.startswith("laptop-") for key in findings(report))
    assert "igpu-platform-id-type" in findings(report)


def test_laptop_does_not_inherit_desktop_chipset_or_framebuffer_rules(clean_laptop):
    checks = {rule["id"]: rule["status"] for rule in check(clean_laptop, cpu="comet-lake", chipset="z390")["checks"]}
    for rule in ("pmc", "awac", "z390services", "setup-virtual-map", "igpu-platform-id", "smbios"):
        assert checks.get(rule, "skipped") == "skipped"


@pytest.mark.parametrize("cpu,page", [("coffee-lake", "coffee-lake"), ("whiskey-lake", "coffee-lake"),
                                     ("comet-lake", "coffee-lake-plus"), ("ice-lake", "icelake")])
def test_laptop_sources(first_timer_laptop, cpu, page):
    result = findings(check(first_timer_laptop, cpu=cpu))
    assert result["laptop-smbios"]["source"].endswith(f"/config-laptop.plist/{page}.html#platforminfo")


def test_xosi_requires_enabled_byte_patch_and_enabled_companion(clean_laptop):
    clean_laptop["ACPI"]["Add"][-1]["Enabled"] = False
    assert "laptop-xosi" in findings(check(clean_laptop))
    clean_laptop["ACPI"]["Add"][-1] = ssdt("custom/SSDT-XOSI")
    assert "laptop-xosi" not in findings(check(clean_laptop))
    clean_laptop["ACPI"]["Add"].pop()
    clean_laptop["ACPI"]["Patch"][0]["Enabled"] = False
    clean_laptop["ACPI"]["Patch"].append({**xosi_patch(), "Find": b"TEST", "Comment": "Rename _OSI to XOSI"})
    assert "laptop-xosi" not in findings(check(clean_laptop))


def test_i2c_check_requires_applicable_dependency(clean_laptop):
    controller, plugin = clean_laptop["Kernel"]["Add"][3:5]
    controller["MaxKernel"] = "22.99.99"
    assert "laptop-order-VoodooI2CHID" in findings(check(clean_laptop))
    plugin["Arch"] = "i386"
    assert "laptop-order-VoodooI2CHID" not in findings(check(clean_laptop))


@pytest.mark.parametrize("value", ["00009B3E", b"\x00", 0x3E9B0000])
def test_platform_id_requires_four_bytes_even_without_hardware_profile(clean_laptop, value):
    clean_laptop["DeviceProperties"]["Add"][IGPU_PATH]["AAPL,ig-platform-id"] = value
    assert "igpu-platform-id-type" in findings(check(clean_laptop, device="other"))


def test_device_type_is_validated():
    assert profile_from({})["device"] == "other"
    with pytest.raises(ConfigError, match="device"):
        profile_from({"device": "tablet"})
