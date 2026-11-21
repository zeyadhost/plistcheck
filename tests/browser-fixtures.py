"""Generate synthetic native baselines for the actual browser Python/WASM runtimes."""
import base64
import json
from pathlib import Path
import plistlib
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import demo_config
from checker import ConfigError, analyze, parse_config, profile_from
from test_amd import amd_config
from test_laptop import clean_laptop_config, first_timer_laptop_config, LAPTOP_PROFILE

fixtures = []


def add(name, config=None, profile=None, raw=None, binary=False):
    data = raw if raw is not None else plistlib.dumps(config, fmt=plistlib.FMT_BINARY if binary else plistlib.FMT_XML)
    case = {"name": name, "data": base64.b64encode(data).decode(), "profile": profile or {}}
    try:
        parsed = parse_config(data)
        result = analyze(parsed, profile_from(case["profile"]))
        assert result["native"]["status"] != "unavailable", result["native"]
        del result["native"]["output"]
        case["expected"] = result
    except ConfigError as error:
        case["error"] = str(error)
    fixtures.append(case)


desktop = {"device": "desktop", "cpu": "coffee-lake", "gpu": "amd-polaris-vega", "chipset": "z390", "macos": "14"}
add("desktop XML", demo_config(), desktop)
add("desktop binary", demo_config(), desktop, binary=True)
add("unknown hardware stays incomplete", demo_config())
add("clean laptop XML", clean_laptop_config(), LAPTOP_PROFILE)
add("clean laptop binary", clean_laptop_config(), LAPTOP_PROFILE, binary=True)
add("broken laptop", first_timer_laptop_config(), LAPTOP_PROFILE)
config = demo_config()
config["Booter"]["Quirks"]["NotARealQuirk"] = True
add("unknown schema key fails official validation", config, desktop)
for os_version in ("10.15", "11.0", "11.3", "12", "13", "14", "15"):
    add(f"AMD reference {os_version}", amd_config(), {"device": "desktop", "cpu": "zen", "cpu_cores": "8", "macos": os_version})
for cores in (0, 16, 64):
    add(f"AMD wrong physical cores {cores}", amd_config(cores), {"device": "desktop", "cpu": "zen", "cpu_cores": "8", "macos": "14"})
config = amd_config()
config["Kernel"]["Patch"] = []
add("AMD missing patches", config, {"device": "desktop", "cpu": "zen", "cpu_cores": "8", "macos": "14"})
add("AMD unknown core count stays incomplete", amd_config(), {"device": "desktop", "cpu": "zen", "macos": "14"})
for name, raw in [
    ("empty", b""), ("PDF", b"%PDF-1.7"), ("ZIP", b"PK\x03\x04junk"), ("malformed XML", b"<plist><dict>"),
    ("duplicate key", b"<plist><dict><key>ACPI</key><dict/><key>ACPI</key><dict/></dict></plist>"),
    ("XML entities", b'<!DOCTYPE plist [<!ENTITY x "bad">]><plist><dict><key>ACPI</key><string>&x;</string></dict></plist>'),
    ("not OpenCore", plistlib.dumps({"hello": "world"})), ("bad binary", b"bplist00broken"),
    ("binary UID", plistlib.dumps({"ACPI": plistlib.UID(1)}, fmt=plistlib.FMT_BINARY)),
    ("oversized", b"x" * (2 * 1024 * 1024 + 1)),
]:
    add(name, raw=raw)
path = ROOT / "vendor" / "browser-fixtures.json"
path.write_text(json.dumps(fixtures))
print(f"Generated {len(fixtures)} native/parser baselines")
