import io
import plistlib
from unittest.mock import patch

import pytest

from app import app, demo_config
from checker import ConfigError, analyze, parse_config, profile_from


@pytest.fixture
def client():
    app.config["TESTING"] = True
    return app.test_client()


def profile(**overrides):
    return profile_from({"cpu": "coffee-lake", "gpu": "amd-supported", "chipset": "z390", "macos": "14", **overrides})


def finding_ids(config, **overrides):
    return {f["id"] for f in analyze(config, profile(**overrides))["findings"]}


def test_binary_and_xml_equivalence():
    config = demo_config()
    assert parse_config(plistlib.dumps(config, fmt=plistlib.FMT_BINARY)) == parse_config(plistlib.dumps(config))


@pytest.mark.parametrize("data", [b"not a plist", b"<plist><dict><key>a</key></dict></plist>",
    b"<plist><dict><key>a</key><true/><key>a</key><false/></dict></plist>",
    b'<!DOCTYPE plist [<!ENTITY test SYSTEM "file:///etc/passwd">]><plist><string>&test;</string></plist>',
    plistlib.dumps([1, 2]), b"x" * (2 * 1024 * 1024 + 1)])
def test_rejects_ambiguous_or_unsafe_input(data):
    with pytest.raises(ConfigError):
        parse_config(data)


def test_api_rejects_missing_file_invalid_version_and_invalid_profile(client):
    assert client.post("/api/check").status_code == 400
    for fields in ({"opencore": "0.9.9"}, {"cpu": "invented"}):
        response = client.post("/api/check", data={**fields, "config": (io.BytesIO(plistlib.dumps({})), "config.plist")})
        assert response.status_code == 400


def test_real_validator_and_hardware_findings(client):
    response = client.post("/api/check", data={"cpu": "coffee-lake", "gpu": "amd-supported", "chipset": "z390", "cfg_lock": "enabled",
        "config": (io.BytesIO(plistlib.dumps(demo_config())), "config.plist")})
    assert response.status_code == 200
    result = response.json
    assert result["native"]["status"] != "unavailable"
    assert {"xhci", "cfglock", "dmg", "awac", "pmc"} <= {f["id"] for f in result["findings"]}
    assert result["checked"] + result["skipped"] == len(result["checks"])
    assert all(f["source"].startswith("https://") for f in result["findings"])


def test_os_and_firmware_conditions():
    config = demo_config()
    assert "xhci" not in finding_ids(config, macos="11.0")
    assert "xhci" in finding_ids(config, macos="11.3")
    assert "cfglock" not in finding_ids(config, cfg_lock="unknown")
    assert "cfglock" in finding_ids(config, cfg_lock="enabled")
    assert "awac" not in finding_ids(config, chipset="other")


def test_kext_dependency_order_and_amd_patch_requirements():
    config = demo_config()
    config["Kernel"]["Add"].reverse()
    assert "order-WhateverGreen.kext" in finding_ids(config)
    assert {"amd-DummyPowerManagement", "amd-ProvideCurrentCpuInfo", "amd-patches"} <= finding_ids(config, cpu="zen")


def test_gpu_native_support_tracks_os():
    config = demo_config()
    assert "gpu-kepler" not in finding_ids(config, gpu="nvidia-kepler", macos="11.3")
    assert "gpu-kepler" in finding_ids(config, gpu="nvidia-kepler", macos="12")
    assert "gpu-unsupported" in finding_ids(config, gpu="nvidia-turing")


def test_type_errors_do_not_crash_custom_checks():
    config = {"Kernel": {"Add": ["bad", {}, {"Enabled": True, "BundlePath": []}]}, "NVRAM": "wrong", "PlatformInfo": {"Generic": []}}
    report = analyze(config, profile())
    assert report["counts"]["error"] > 0
    assert report["native"]["status"] == "failed"


def test_unavailable_native_validator_never_reports_complete():
    with patch("checker.native_validate", return_value={"status": "unavailable", "issues": 0, "output": "Unavailable"}):
        report = analyze(demo_config(), profile(cpu="other", gpu="other"))
    assert report["status"] == "incomplete"
    assert any("schema" in gap for gap in report["gaps"])
    assert any("No CPU-generation" in gap for gap in report["gaps"])


def test_native_diagnostics_include_unknown_schema_key():
    config = demo_config()
    config["Kernel"]["Quirks"]["DefinitelyNotAnOpenCoreKey"] = True
    report = analyze(config, profile())
    assert report["native"]["status"] == "failed"
    assert "DefinitelyNotAnOpenCoreKey" in report["native"]["output"]


def test_demo_and_static_app(client):
    assert client.get("/").status_code == 200
    assert client.get("/assets/app.js").status_code == 200
    demo = client.get("/api/demo")
    assert isinstance(parse_config(demo.data), dict)
    assert client.get("/").headers["Cache-Control"] == "no-store"


def test_deep_xml_rejected():
    data = b"<plist>" + b"<array>" * 66 + b"<true/>" + b"</array>" * 66 + b"</plist>"
    with pytest.raises(ConfigError, match="deeply"):
        parse_config(data)


def test_out_of_range_kexts_and_patches_do_not_satisfy_presence_checks():
    config = demo_config()
    for kext in config["Kernel"]["Add"]:
        kext["MaxKernel"] = "19.9.9"
    assert {"lilu", "smc"} <= finding_ids(config)
    config["Kernel"]["Patch"] = [{"Enabled": True, "MinKernel": "24.0.0", "MaxKernel": ""}]
    assert "amd-patches" in finding_ids(config, cpu="zen", macos="14")
    for kext in config["Kernel"]["Add"]:
        kext["MaxKernel"] = ""
    assert "smc" not in finding_ids(config)


def test_nested_kext_bundle_names_and_plugin_order():
    config = demo_config()
    for entry in config["Kernel"]["Add"]:
        entry["BundlePath"] = "Drivers/" + entry["BundlePath"]
    ids = finding_ids(config)
    assert not {"lilu", "smc", "order-WhateverGreen.kext"} & ids
