"""AMD operations use the checksum-verified real upstream patch reference."""
import copy
import io
import plistlib
from unittest.mock import patch

import pytest

import amd
from app import app, demo_config
from checker import ConfigError, analyze, native_validate, profile_from


def amd_config(cores=8):
    config = demo_config()
    config["ACPI"]["Add"] = []
    config["Kernel"]["Patch"] = copy.deepcopy(amd.reference_patches())
    for entry in config["Kernel"]["Patch"]:
        if "cpuid_cores_per_package" in entry["Comment"]:
            entry["Replace"] = entry["Replace"][:1] + bytes([cores]) + entry["Replace"][2:]
    config["Kernel"]["Emulate"]["DummyPowerManagement"] = True
    config["Kernel"]["Quirks"]["ProvideCurrentCpuInfo"] = True
    for field in ("AppleCpuPmCfgLock", "AppleXcpmCfgLock", "AppleXcpmExtraMsrs"):
        config["Kernel"]["Quirks"][field] = False
    for field in ("Cpuid1Data", "Cpuid1Mask"):
        config["Kernel"]["Emulate"][field] = bytes(16)
    return config


def report(config, macos="14", cores="8"):
    return analyze(config, profile_from({"device": "desktop", "cpu": "zen", "cpu_cores": cores, "macos": macos}))


def ids(result):
    return {finding["id"] for finding in result["findings"]}


@pytest.mark.parametrize("macos", ["10.15", "11.0", "11.3", "12", "13", "14", "15"])
def test_pinned_real_amd_set_is_recognized_across_supported_targets(macos):
    config = amd_config()
    assert native_validate(config)["status"] == "passed"
    assert not {key for key in ids(report(config, macos)) if key.startswith("amd-")}


@pytest.mark.parametrize("macos", ["12", "13", "14", "15"])
@pytest.mark.parametrize("cores", [0, 16, 64])
def test_placeholder_threads_and_wrong_nonzero_core_counts_are_errors(macos, cores):
    result = report(amd_config(cores), macos)
    assert "amd-core-count" in ids(result)
    assert next(f for f in result["findings"] if f["id"] == "amd-core-count")["severity"] == "error"


@pytest.mark.parametrize("mutation", ["empty", "disabled", "unrelated", "identifier", "core-removed", "groups-removed", "bytes", "arch", "range"])
def test_missing_modified_and_unrelated_patches_do_not_certify_amd(mutation):
    config = amd_config()
    patches = config["Kernel"]["Patch"]
    if mutation == "empty":
        patches.clear()
    elif mutation == "disabled":
        for entry in patches:
            entry["Enabled"] = False
    elif mutation == "unrelated":
        entry = copy.deepcopy(patches[4])
        entry.update(Find=b"\x01\x02", Replace=b"\x03\x04", Comment="algrey | cpuid_cores_per_package")
        patches[:] = [entry]
    elif mutation in ("identifier", "bytes", "arch", "range"):
        for entry in patches:
            if mutation == "identifier":
                entry["Identifier"] = "com.example.unrelated"
            elif mutation == "bytes":
                entry["Find"] = b"\x12\x34"
            elif mutation == "arch":
                entry["Arch"] = "i386"
            else:
                entry["MaxKernel"] = "22.99.99"
    elif mutation == "core-removed":
        patches[:] = [p for p in patches if "cpuid_cores_per_package" not in p["Comment"]]
    else:
        del patches[4:17]
    result = report(config)
    assert "amd-required-patches" in ids(result)
    assert result["counts"]["error"] > 0
    if mutation in ("empty", "disabled", "unrelated", "identifier", "bytes", "arch", "range"):
        assert "amd-patches" in ids(result)


def test_comments_are_not_patch_provenance_and_pat_is_optional():
    config = amd_config()
    config["Kernel"]["Patch"] = [p for p in config["Kernel"]["Patch"] if "PAT" not in p["Comment"]]
    config["Kernel"]["Patch"] = [p for p in config["Kernel"]["Patch"] if p["Identifier"] == "kernel"]
    for entry in config["Kernel"]["Patch"]:
        entry["Comment"] = "User's custom label"
    assert not {key for key in ids(report(config)) if key.startswith("amd-")}


def test_point_release_core_patch_transition_and_overbroad_ranges():
    config = amd_config()
    for entry in config["Kernel"]["Patch"]:
        if "cpuid_cores_per_package" in entry["Comment"] and entry["MinKernel"] == "22.4.0":
            entry["Enabled"] = False
    assert "amd-required-patches" in ids(report(config, "13"))
    config = amd_config()
    for entry in config["Kernel"]["Patch"]:
        if "cpuid_cores_per_package" in entry["Comment"]:
            entry["MinKernel"], entry["MaxKernel"] = "", ""
    assert "amd-patch-ranges" in ids(report(config, "13"))


def test_extra_historical_patch_enabled_only_on_wrong_kernel_is_an_error():
    config = amd_config()
    older_core = copy.deepcopy(next(p for p in config["Kernel"]["Patch"] if "cpuid_cores_per_package" in p["Comment"]))
    older_core.update(MinKernel="23.0.0", MaxKernel="23.99.99")
    config["Kernel"]["Patch"].append(older_core)
    assert "amd-patch-ranges" in ids(report(config))


def test_unknown_physical_count_is_incomplete_not_a_pass():
    result = report(amd_config(64), cores="")
    assert result["status"] == "incomplete"
    assert "amd-cores-unknown" in ids(result)
    assert any("physical core count is unknown" in gap for gap in result["gaps"])


def test_unavailable_reference_does_not_claim_amd_coverage():
    config = amd_config()
    with patch("amd.reference_patches", side_effect=OSError("Unavailable")):
        result = report(config)
    assert result["status"] == "incomplete"
    assert any("reference unavailable" in gap for gap in result["gaps"])


def test_intel_power_management_and_cpuid_leftovers_are_flagged():
    config = amd_config()
    for field in ("AppleCpuPmCfgLock", "AppleXcpmCfgLock", "AppleXcpmExtraMsrs"):
        config["Kernel"]["Quirks"][field] = True
    for field in ("Cpuid1Data", "Cpuid1Mask"):
        config["Kernel"]["Emulate"][field] = b"\1" * 16
    assert {"amd-intel-" + field for field in ("AppleCpuPmCfgLock", "AppleXcpmCfgLock", "AppleXcpmExtraMsrs", "Cpuid1Data", "Cpuid1Mask")} <= ids(report(config))


@pytest.mark.parametrize("value", ["0", "-1", "256", "8.5", "eight", "True"])
def test_core_count_input_is_validated(value):
    with pytest.raises(ConfigError, match="physical CPU cores"):
        profile_from({"cpu_cores": value})


def test_api_checks_core_count_and_demo_passes_real_validator():
    client = app.test_client()
    result = client.post("/api/check", data={"device": "desktop", "cpu": "zen", "cpu_cores": "8", "config": (io.BytesIO(plistlib.dumps(amd_config(64))), "config.plist")})
    assert result.status_code == 200
    assert "amd-core-count" in ids(result.json)
    assert native_validate(plistlib.loads(client.get("/api/demo").data))["status"] == "passed"
