import plistlib
from pathlib import Path
from unittest.mock import patch

import pytest

from app import demo_config
from checker import GUID, analyze, native_validate, profile_from


FIXTURES = Path(__file__).parent / "fixtures" / "dortania"
PROFILES = [
    ("coffee-lake", "amd-polaris-vega", "z390", "14", "enabled", "enabled", "enabled"),
    ("coffee-lake", "amd-polaris-vega", "z390", "14", "enabled", "enabled", "enabled"),
    ("haswell", "intel-supported", "other", "12", "disabled", "disabled", "disabled"),
    ("haswell", "intel-supported", "other", "14", "enabled", "disabled", "disabled"),
    ("comet-lake", "amd-navi", "400", "13", "disabled", "disabled", "disabled"),
    ("comet-lake", "amd-navi", "400", "13", "disabled", "disabled", "disabled"),
    ("zen", "amd-navi", "b550-a520", "14", "unknown", "disabled", "disabled"),
    ("zen", "amd-navi", "b550-a520", "14", "unknown", "enabled", "disabled"),
    ("kaby-lake", "intel-supported", "other", "13", "disabled", "disabled", "disabled"),
    ("kaby-lake", "intel-supported", "other", "13", "disabled", "disabled", "disabled"),
    ("broadwell", "intel-supported", "other", "12", "disabled", "disabled", "disabled"),
]
MISSED = {
    2: {"smbios", "vault", "booter-memory", "gpu-polaris-pikera"},
    4: {"smbios", "gpu-legacy-intel", "igpu-platform-id", "write-unprotector"},
    6: {"setup-virtual-map", "z390services", "awac", "smbios", "igpu-headless", "gpu-navi"},
    8: {"setup-virtual-map", "smbios", "amd-plug", "amd-intel-acpi", "amd-smc-plugins", "amd-cfglock", "amd-iommu"},
    10: {"igpu-platform-id", "smbios"},
}


def fixture_case(number):
    config = plistlib.loads(next(FIXTURES.glob(f"{number:02}-*.plist")).read_bytes())
    profile = profile_from(dict(zip(("cpu", "gpu", "chipset", "macos", "cfg_lock", "vt_d", "rebar"), PROFILES[number - 1])))
    return config, profile


@pytest.mark.parametrize("number", range(1, 12))
def test_audit_fixture(number):
    result = analyze(*fixture_case(number))
    assert result["native"]["status"] != "unavailable"
    if number in MISSED:
        assert MISSED[number] <= {f["id"] for f in result["findings"]}
    else:
        assert not [f for f in result["findings"] if f["severity"] in ("error", "warning")]


def test_empty_uuid_is_only_placeholder_finding():
    config, profile = fixture_case(1)
    config["PlatformInfo"]["Generic"]["SystemUUID"] = ""
    ids = {f["id"] for f in analyze(config, profile)["findings"]}
    assert "identity-SystemUUID" in ids
    assert "uuid" not in ids
    config["PlatformInfo"]["Generic"]["SystemUUID"] = "malformed"
    assert "uuid" in {f["id"] for f in analyze(config, profile)["findings"]}


def test_demo_driver_schema():
    result = native_validate(demo_config())
    assert result["status"] == "failed"
    assert "Missing key" not in result["output"]
    assert "DmgLoading" in result["output"]


def test_native_covers_dependency_checks_with_fallback():
    config, profile = fixture_case(2)
    native = native_validate(config)
    assert "DisableLinkeditJettison is not enabled" in native["output"]
    assert "Parent (Lilu.kext)" in native["output"]
    result = analyze(config, profile)
    assert all(c["status"] == "skipped" for c in result["checks"] if c["id"] == "lilu-quirk" or c["id"].startswith("order-"))
    with patch("checker.native_validate", return_value={"status": "unavailable", "output": "", "issues": 0}):
        ids = {f["id"] for f in analyze(config, profile)["findings"]}
    assert {"lilu-quirk", "order-WhateverGreen.kext"} <= ids


@pytest.mark.parametrize("cpu,macos,model,expected", [
    ("haswell", "12", "iMac14,4", None),
    ("haswell", "13", "iMac18,3", None),
    ("skylake", "14", "iMac19,1", None),
    ("kaby-lake", "14", "iMac19,2", None),
    ("comet-lake", "13", "iMac20,2", None),
    ("zen", "14", "MacPro7,1", None),
    ("zen", "14", "MacPro6,1", "error"),
    ("coffee-lake", "14", "iMacPro1,1", "warning"),
    ("other", "14", "iMac14,4", None),
    ("haswell", "14", 1, None),
    ("haswell", "14", "", "warning"),
])
def test_smbios_family_and_support(cpu, macos, model, expected):
    config, profile = fixture_case(1)
    profile.update(cpu=cpu, macos=macos)
    config["PlatformInfo"]["Generic"]["SystemProductName"] = model
    findings = [f["severity"] for f in analyze(config, profile)["findings"] if f["id"] == "smbios"]
    assert findings == ([] if expected is None else [expected])


@pytest.mark.parametrize("value,expected", [(None, "error"), (b"wrong", "warning"), (bytes.fromhex("00001259"), None)])
def test_display_framebuffer_missing_wrong_and_correct(value, expected):
    config, profile = fixture_case(9)
    properties = config["DeviceProperties"]["Add"]["PciRoot(0x0)/Pci(0x2,0x0)"]
    if value is None:
        properties.pop("AAPL,ig-platform-id")
    else:
        properties["AAPL,ig-platform-id"] = value
    findings = [f["severity"] for f in analyze(config, profile)["findings"] if f["id"] == "igpu-platform-id"]
    assert findings == ([] if expected is None else [expected])


@pytest.mark.parametrize("value,expected", [(None, False), (bytes.fromhex("07009B3E"), True), (bytes.fromhex("0300C89B"), False)])
def test_headless_framebuffer_missing_wrong_and_correct(value, expected):
    config, profile = fixture_case(5)
    properties = config["DeviceProperties"]["Add"]["PciRoot(0x0)/Pci(0x2,0x0)"]
    if value is None:
        properties.pop("AAPL,ig-platform-id", None)
    else:
        properties["AAPL,ig-platform-id"] = value
    assert ("igpu-headless" in {f["id"] for f in analyze(config, profile)["findings"]}) == expected


@pytest.mark.parametrize("write,rebuild,expected", [(True, False, set()), (False, True, {"write-unprotector"}), (True, True, {"booter-memory"}), (False, False, {"write-unprotector"})])
def test_memory_map_strategies(write, rebuild, expected):
    config, profile = fixture_case(3)
    config["Booter"]["Quirks"].update(EnableWriteUnprotector=write, RebuildAppleMemoryMap=rebuild)
    ids = {f["id"] for f in analyze(config, profile)["findings"]}
    assert ids & {"booter-memory", "write-unprotector"} == expected


@pytest.mark.parametrize("gpu", ["amd-supported", "amd-polaris-vega", "amd-navi"])
def test_gpu_disable_includes_all_supported_amd_choices(gpu):
    config, profile = fixture_case(1)
    profile["gpu"] = gpu
    config["NVRAM"]["Add"][GUID]["boot-args"] += " -wegnoegpu"
    assert "gpu-disable" in {f["id"] for f in analyze(config, profile)["findings"]}
