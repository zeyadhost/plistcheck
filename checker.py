"""Deterministic, source-linked checks. A passed check is not a boot guarantee."""
import os
import plistlib
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

from defusedxml import ElementTree

VERSION = "1.0.8"
ROOT = Path(__file__).resolve().parent
GUID = "7C436110-AB2A-4BBB-A880-FE41995C9F82"
DOCS = f"https://github.com/acidanthera/OpenCorePkg/blob/{VERSION}/Docs/Configuration.pdf"
GUIDE = "https://dortania.github.io/OpenCore-Install-Guide/"
GENERATIONS = {"haswell", "broadwell", "skylake", "kaby-lake", "coffee-lake", "comet-lake", "zen", "other"}
GPUS = {"amd-supported", "amd-navi24", "intel-supported", "intel-xe", "nvidia-kepler", "nvidia-maxwell", "nvidia-turing", "other"}
CHIPSETS = {"other", "z390", "300", "z370", "b550-a520"}
OS_VERSIONS = {"10.15", "11.0", "11.3", "12", "13", "14", "15"}


class ConfigError(ValueError):
    pass


def parse_config(data):
    if len(data) > 2 * 1024 * 1024:
        raise ConfigError("Use a config.plist smaller than 2 MB, not a full EFI archive.")
    try:
        if not data.startswith(b"bplist"):
            tree = ElementTree.fromstring(data, forbid_entities=True, forbid_external=True)
            stack = [(tree, 0)]
            while stack:
                node, depth = stack.pop()
                if depth > 64:
                    raise ConfigError("The plist is nested too deeply (maximum 64 levels).")
                if node.tag == "dict":
                    children = list(node)
                    if len(children) % 2 or any(n.tag != "key" for n in children[::2]):
                        raise ConfigError("A dictionary must contain alternating keys and values.")
                    keys = [n.text or "" for n in children[::2]]
                    if len(set(keys)) != len(keys):
                        raise ConfigError("Duplicate dictionary keys found. Each key must occur only once.")
                stack.extend((child, depth + 1) for child in node)
        result = plistlib.loads(data)
        if not isinstance(result, dict):
            raise ConfigError("The plist root must be a dictionary containing OpenCore sections.")
        # Also bound depth and object count for binary plists, including cyclic references.
        stack = [(result, 0)]
        count = 0
        while stack:
            obj, depth = stack.pop()
            count += 1
            if depth > 64 or count > 100000:
                raise ConfigError("The plist exceeds the nesting or object limit.")
            if isinstance(obj, dict):
                stack.extend((v, depth + 1) for v in obj.values())
            elif isinstance(obj, list):
                stack.extend((v, depth + 1) for v in obj)
        return result
    except ConfigError:
        raise
    except Exception as error:
        raise ConfigError("This is not a valid XML or binary property list. Export it again with ProperTree.") from error


def profile_from(form):
    profile = {}
    for field, allowed in [("cpu", GENERATIONS), ("gpu", GPUS), ("chipset", CHIPSETS), ("macos", OS_VERSIONS),
                           ("cfg_lock", {"unknown", "enabled", "disabled"}), ("vt_d", {"unknown", "enabled", "disabled"}),
                           ("rebar", {"unknown", "enabled", "disabled"})]:
        value = form.get(field, {"cpu": "other", "gpu": "other", "chipset": "other", "macos": "14"}.get(field, "unknown"))
        if value not in allowed:
            raise ConfigError(f"Choose a valid {field.replace('_', ' ')} option.")
        profile[field] = value
    if form.get("opencore", VERSION) != VERSION:
        raise ConfigError(f"This release checks OpenCore {VERSION} only. Use the validator matching your installed version.")
    for field in ("cpu_model", "gpu_model", "board"):
        profile[field] = str(form.get(field, ""))[:120]
    return profile


def get(config, path, default=None):
    value = config
    for part in path.split("/"):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value


def entries(config, path, kernel_range=None):
    value = get(config, path, [])
    enabled = [v for v in value if isinstance(v, dict) and v.get("Enabled") is True] if isinstance(value, list) else []
    if kernel_range is None:
        return enabled
    def applicable(entry):
        if entry.get("Arch", "Any") not in ("Any", "x86_64"):
            return False
        try:
            bounds = []
            for key, default in (("MinKernel", (0, 0, 0)), ("MaxKernel", (999, 999, 999))):
                raw = entry.get(key, "")
                if not isinstance(raw, str):
                    return False
                parts = tuple(int(p) for p in raw.split(".")) if raw else default
                if len(parts) > 3 or any(p < 0 for p in parts):
                    return False
                bounds.append(parts + (0,) * (3 - len(parts)))
            # Only count entries known to cover the entire selected OS family.
            return bounds[0] <= kernel_range[0] and bounds[1] >= kernel_range[1]
        except ValueError:
            return False
    return [entry for entry in enabled if applicable(entry)]


def darwin_range(macos):
    major = {"10.15": 19, "11.0": 20, "11.3": 20, "12": 21, "13": 22, "14": 23, "15": 24}[macos]
    return ((major, 4 if macos == "11.3" else 0, 0), (major, 3 if macos == "11.0" else 99, 99))


def native_validate(config):
    executable = ROOT / "vendor" / ("ocvalidate.exe" if os.name == "nt" else "ocvalidate")
    if not executable.is_file():
        return {"status": "unavailable", "output": "Official validator unavailable. Run python setup_opencore.py.", "issues": 0}
    # Normalize XML/binary input to the XML format expected by ocvalidate.
    with tempfile.TemporaryDirectory(prefix="plistcheck-") as directory:
        filename = Path(directory) / "config.plist"
        filename.write_bytes(plistlib.dumps(config, sort_keys=False))
        try:
            result = subprocess.run([str(executable), str(filename)], capture_output=True, timeout=8, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return {"status": "unavailable", "output": "Official validator could not finish. No schema pass is claimed.", "issues": 0}
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    output = output.replace(directory, "[temporary upload]")[:50000]
    match = re.search(r"Found (\d+) issues?", output)
    return {"status": "passed" if result.returncode == 0 else "failed", "output": output,
            "issues": int(match.group(1)) if match else (0 if result.returncode == 0 else 1)}


def analyze(config, profile):
    findings, checks = [], []
    cpu = profile["cpu"]
    source = GUIDE + ("AMD/zen.html" if cpu == "zen" else f"config.plist/{cpu if cpu != 'other' else 'coffee-lake'}.html")

    def check(key, passed, title, path, detail, fix, severity="warning", link=source, applicable=True):
        checks.append({"id": key, "status": "skipped" if not applicable else "passed" if passed else "failed"})
        if applicable and not passed:
            findings.append({"id": key, "severity": severity, "title": title, "path": path,
                             "detail": detail, "fix": fix, "source": link})

    kernel_range = darwin_range(profile["macos"])
    kexts = [str(e.get("BundlePath", "")).split("/")[0].lower() for e in entries(config, "Kernel/Add", kernel_range)]
    acpi = [str(e.get("Path", "")).lower() for e in entries(config, "ACPI/Add")]
    drivers = [str(e.get("Path", "")).lower() for e in entries(config, "UEFI/Drivers")]
    has = lambda name: name.lower() in kexts
    q = lambda name: get(config, "Kernel/Quirks/" + name)
    boot = get(config, f"NVRAM/Add/{GUID}/boot-args", "")
    boot = boot if isinstance(boot, str) else ""
    modern_intel = cpu not in ("zen", "other")
    os_version = tuple(int(n) for n in profile["macos"].split("."))

    for section in ("ACPI", "Booter", "DeviceProperties", "Kernel", "Misc", "NVRAM", "PlatformInfo", "UEFI"):
        check("section-" + section, isinstance(config.get(section), dict), f"{section} section is missing or malformed", section,
              "OpenCore requires all eight top-level sections with their required keys.", "Restore this section from the matching OpenCore Sample.plist, then configure it for your machine.", "error", DOCS)
    check("lilu", has("Lilu.kext"), "Lilu is not enabled", "Kernel → Add", "Many graphics, audio, and platform plugins depend on Lilu.", "Add the matching Lilu.kext and enable it before its plugins.")
    check("smc", has("VirtualSMC.kext") or has("FakeSMC.kext"), "No SMC emulator is enabled", "Kernel → Add", "Non-Apple hardware needs SMC emulation to boot macOS.", "Install and enable VirtualSMC.kext.", "error")
    check("smc-conflict", not (has("VirtualSMC.kext") and has("FakeSMC.kext")), "Two SMC emulators are enabled", "Kernel → Add", "VirtualSMC and FakeSMC must not be used together.", "Keep one SMC emulator; prefer VirtualSMC.", "error")
    check("runtime", "openruntime.efi" in drivers, "OpenRuntime.efi is not enabled", "UEFI → Drivers", "OpenRuntime provides the runtime services used by OpenCore's firmware quirks.", "Add the OpenRuntime.efi shipped with your exact OpenCore release.")
    check("filesystem", any("hfs" in d for d in drivers), "No HFS filesystem driver is listed", "UEFI → Drivers", "macOS recovery and HFS+ volumes may not be readable without HfsPlus or OpenHfsPlus.", "Check your firmware support, then add one HFS+ driver if needed.")
    check("xhci", q("XhciPortLimit") is not True, "USB port-limit patch is enabled on macOS 11.3+", "Kernel → Quirks → XhciPortLimit", "Dortania warns this patch may not work correctly on macOS 11.3 and later.", "Set XhciPortLimit to false and create a proper USB map.", applicable=os_version >= (11, 3))
    check("usbmap", has("USBMap.kext") or has("UTBMap.kext") or has("USBPorts.kext"), "A USB map is not identifiable by filename", "Kernel → Add", "Custom-named maps or firmware mappings cannot be recognized from a plist alone.", "Verify that you have a working, machine-specific USB map. Do not rename a working custom map just to silence this warning.", link="https://dortania.github.io/OpenCore-Post-Install/usb/")
    check("usbinjectall", not has("USBInjectAll.kext"), "USBInjectAll is still enabled", "Kernel → Add", "USBInjectAll is a setup aid, not a finished USB map.", "Replace it with a tested USB map when setup is complete.")
    check("lilu-quirk", q("DisableLinkeditJettison") is True, "Lilu needs DisableLinkeditJettison", "Kernel → Quirks → DisableLinkeditJettison", "This quirk is required for reliable Lilu operation.", "Set DisableLinkeditJettison to true.", "error", DOCS, applicable=has("Lilu.kext"))
    for plugin in ("WhateverGreen.kext", "AppleALC.kext", "VirtualSMC.kext"):
        check("order-" + plugin, has("Lilu.kext") and kexts.index("lilu.kext") < kexts.index(plugin.lower()) if has(plugin) else True,
              f"{plugin} needs Lilu earlier in the list", "Kernel → Add", "Dependencies must be enabled and loaded before their plugins.", "Enable Lilu and move it above this kext.", "error", DOCS, applicable=has(plugin))
    check("dsdt", not any(Path(p).name == "dsdt.aml" for p in acpi), "A complete DSDT is being injected", "ACPI → Add", "Dortania advises against adding your firmware's complete DSDT.", "Use targeted SSDTs instead of injecting DSDT.aml.")
    check("plug", any("plug" in p for p in acpi), "CPU power-management SSDT is not identifiable", "ACPI → Add", "Haswell and newer desktops normally need SSDT-PLUG. Custom filenames or alternate methods cannot be identified here.", "Verify plugin-type CPU power management and add a machine-appropriate SSDT-PLUG if absent.", applicable=modern_intel)
    check("ec-usbx", any("ec" in p for p in acpi) and any("usbx" in p for p in acpi), "EC / USB power SSDTs are not identifiable", "ACPI → Add", "Modern desktops generally need an EC fix and USB power properties. Custom SSDTs may already provide these.", "Verify your ACPI tables against Dortania's EC and USBX guidance.")
    check("awac", any("awac" in p or "rtc0" in p for p in acpi), "Check the board's RTC / AWAC fix", "ACPI → Add", "Many 300-series boards need SSDT-AWAC or SSDT-RTC0; firmware inspection determines which.", "Inspect your ACPI tables before adding an AWAC/RTC fix.", applicable=profile["chipset"] in ("z390", "300", "z370"))
    check("pmc", any("pmc" in p for p in acpi), "Check native NVRAM support on this chipset", "ACPI → Add", "True 300-series boards other than Z370 may need SSDT-PMC. A filename check cannot prove firmware behavior.", "Test NVRAM persistence and inspect whether SSDT-PMC is needed.", applicable=profile["chipset"] in ("z390", "300"))
    check("cpur", any("cpur" in p for p in acpi), "Check SSDT-CPUR for B550 / A520", "ACPI → Add", "Dortania lists SSDT-CPUR for B550 and A520 CPU definitions.", "Verify the board requires SSDT-CPUR, then add the matching table.", applicable=cpu == "zen" and profile["chipset"] == "b550-a520")
    check("cfglock", q("AppleXcpmCfgLock") is True, "CFG-Lock is enabled without the XCPM workaround", "Kernel → Quirks → AppleXcpmCfgLock", "You reported CFG-Lock enabled. This can prevent CPU power management from initializing.", "Prefer disabling CFG-Lock in BIOS; otherwise enable AppleXcpmCfgLock.", "error", applicable=modern_intel and profile["cfg_lock"] == "enabled")
    check("vtd", q("DisableIoMapper") is True or "dart=0" in boot.split(), "VT-d is enabled without an I/O mapper workaround", "Kernel → Quirks → DisableIoMapper", "The selected BIOS state may need DisableIoMapper; a deliberately configured DMAR table can be an exception.", "Review your DMAR/IOMMU setup; otherwise enable DisableIoMapper or disable VT-d.", applicable=modern_intel and profile["vt_d"] == "enabled")
    check("rebar", get(config, "Booter/Quirks/ResizeAppleGpuBars") == 0, "Resizable BAR is enabled without the macOS workaround", "Booter → Quirks → ResizeAppleGpuBars", "With Resizable BAR enabled in firmware, Dortania recommends ResizeAppleGpuBars = 0.", "Set ResizeAppleGpuBars to 0, or disable Resizable BAR in firmware.", "error", applicable=profile["rebar"] == "enabled")
    check("z390services", get(config, "Booter/Quirks/ProtectUefiServices") is True, "Z390 firmware services are not protected", "Booter → Quirks → ProtectUefiServices", "Dortania recommends this quirk on Z390 boards.", "Enable ProtectUefiServices and verify against your firmware.", applicable=profile["chipset"] == "z390")
    for field, path in [("DummyPowerManagement", "Kernel/Emulate/"), ("ProvideCurrentCpuInfo", "Kernel/Quirks/")]:
        check("amd-" + field, get(config, path + field) is True, f"AMD Zen needs {field}", (path + field).replace("/", " → "), "Dortania's AMD Zen configuration enables this option.", f"Set {field} to true for the selected AMD Zen profile.", "error", applicable=cpu == "zen")
    check("amd-patches", bool(entries(config, "Kernel/Patch", kernel_range)), "No AMD kernel patches cover the selected macOS range", "Kernel → Patch", "AMD Zen requires version-appropriate AMD Vanilla patches, including CPU core-count edits.", "Apply the current AMD Vanilla patches for your CPU and macOS version; this check does not validate their contents.", "error", "https://github.com/AMD-OSX/AMD_Vanilla", applicable=cpu == "zen")
    gpu_source = "https://dortania.github.io/GPU-Buyers-Guide/"
    check("gpu-unsupported", profile["gpu"] not in ("intel-xe", "nvidia-turing", "amd-navi24"), "The selected GPU family has no native macOS acceleration", "Hardware → Graphics", "Intel Xe, NVIDIA Turing and later, and AMD Navi 24 are not natively supported by macOS.", "Use a supported GPU or disable this GPU and use a supported alternative. A plist edit cannot add a missing driver.", "error", gpu_source)
    check("gpu-maxwell", profile["gpu"] != "nvidia-maxwell", "NVIDIA Maxwell / Pascal is unsupported on this macOS", "Hardware → Graphics", "NVIDIA web drivers stop at macOS High Sierra, older than every target offered here.", "Use a supported graphics device for this macOS version.", "error", gpu_source)
    check("gpu-kepler", profile["gpu"] != "nvidia-kepler", "NVIDIA Kepler lost native support in Monterey", "Hardware → Graphics", "Kepler has no native acceleration on macOS 12 and newer. Root patching is outside this checker's coverage.", "Use a supported GPU or independently evaluate the limitations of legacy patching.", "error", gpu_source, applicable=os_version >= (12,))
    check("gpu-disable", "-wegnoegpu" not in boot.split(), "Boot arguments disable external GPUs", "NVRAM → Add → boot-args", "-wegnoegpu disables external graphics when WhateverGreen is active.", "Remove this argument if you intend to use the discrete GPU.", applicable=profile["gpu"] in ("amd-supported", "nvidia-kepler"))
    check("debug-args", not any(a in boot.split() for a in ("-v", "debug=0x100", "keepsyms=1")), "Debug boot arguments are still present", "NVRAM → Add → boot-args", "Verbose and debug arguments are useful during setup; they are not necessarily faults.", "Keep these while troubleshooting; remove them only after the system is stable.", "info")
    check("amfi", not any(a.startswith(("amfi_get_out_of_my_way=1", "amfi=0x80")) for a in boot.split()), "AMFI is disabled by a boot argument", "NVRAM → Add → boot-args", "Disabling AMFI weakens macOS code-signing protection.", "Remove the disabling argument unless a deliberate, documented setup requires it.", link=GUIDE + "config.plist/security.html")
    sip = get(config, f"NVRAM/Add/{GUID}/csr-active-config")
    check("sip", sip in (None, b"\x00\x00\x00\x00"), "System Integrity Protection is not fully enabled", "NVRAM → Add → csr-active-config", "A nonzero CSR configuration disables some system protections. Legacy patches may require this intentionally.", "Use four zero data bytes for full SIP, unless your patching setup explicitly requires otherwise.", link=GUIDE + "config.plist/security.html")
    check("dmg", get(config, "Misc/Security/DmgLoading") != "Any", "Unsigned disk images are allowed", "Misc → Security → DmgLoading", "DmgLoading = Any permits unsigned disk images.", "Use Signed for normal Apple recovery images.", link=GUIDE + "config.plist/security.html")
    generic = get(config, "PlatformInfo/Generic", {})
    generic = generic if isinstance(generic, dict) else {}
    for field in ("SystemSerialNumber", "MLB", "SystemUUID"):
        value = generic.get(field)
        check("identity-" + field, isinstance(value, str) and value not in ("", "M00000000001", "M0000000000000001", "00000000-0000-0000-0000-000000000000") and not str(value).startswith("W000"),
              f"{field} is blank or an example placeholder", "PlatformInfo → Generic → " + field,
              "A machine-specific SMBIOS identity is required. Values are not checked against Apple's services.", "Generate your own matching SMBIOS values with GenSMBIOS; never reuse someone else's serial.", "warning")
    value = generic.get("SystemUUID")
    try:
        uuid.UUID(value) if isinstance(value, str) else (_ for _ in ()).throw(ValueError())
        valid_uuid = True
    except (ValueError, AttributeError):
        valid_uuid = False
    check("uuid", valid_uuid, "SystemUUID is not a valid UUID", "PlatformInfo → Generic → SystemUUID", "The UUID must use the standard 8-4-4-4-12 hexadecimal format.", "Generate a valid, unique UUID with GenSMBIOS.", "error")
    rom = generic.get("ROM")
    check("rom", isinstance(rom, bytes) and len(rom) == 6 and rom not in (bytes(6), bytes.fromhex("112233445566")), "ROM is missing, malformed, or an example value", "PlatformInfo → Generic → ROM", "ROM should be six nonzero, machine-specific bytes, commonly from a stable network adapter.", "Set ROM as six bytes of Data appropriate for this machine.")

    native = native_validate(config)
    if native["status"] == "failed":
        findings.insert(0, {"id": "ocvalidate", "severity": "error", "title": f"OpenCore validator reported {native['issues']} issue(s)",
                            "path": "OpenCore → Schema & semantics", "detail": "The official version-matched validator rejected this config. Open the raw diagnostics below for the exact keys.",
                            "fix": "Resolve these diagnostics with the OpenCore 1.0.8 configuration manual, then run the check again.", "source": DOCS})
    gaps = ["EFI files: presence, versions, executable contents and kext dependencies on disk were not inspected.",
            "ACPI contents, firmware behavior, USB mapping quality, SMBIOS/macOS compatibility and actual boot success require manual verification.",
            "CPU/GPU model and board text are report labels, not automatically detected hardware. Rules use your selected families and chipset.",
            "Kext and patch presence checks count only x86_64/Any entries whose kernel ranges cover the selected macOS family. Narrower ranges need manual review for your exact point release."]
    if cpu == "other":
        gaps.append("No CPU-generation-specific rules ran for Other / unknown. This is a desktop checker; laptop and newer Intel workarounds are not covered.")
    if profile["gpu"] == "other":
        gaps.append("GPU compatibility is unknown for the selected family.")
    if any(profile[k] == "unknown" for k in ("cfg_lock", "vt_d", "rebar")):
        gaps.append("Some BIOS settings are unknown; the corresponding conditional checks were skipped.")
    if cpu == "zen":
        gaps.append("AMD patch contents, per-core edits and target kernel ranges were not validated.")
    if native["status"] == "unavailable":
        gaps.insert(0, "Official validator unavailable: schema and semantic coverage is incomplete.")
    counts = {s: sum(f["severity"] == s for f in findings) for s in ("error", "warning", "info")}
    return {"version": VERSION, "profile": profile, "findings": findings, "counts": counts, "native": native,
            "checks": checks, "checked": sum(c["status"] != "skipped" for c in checks),
            "passed": sum(c["status"] == "passed" for c in checks), "skipped": sum(c["status"] == "skipped" for c in checks),
            "gaps": gaps, "status": "incomplete" if native["status"] == "unavailable" else "issues" if counts["error"] or counts["warning"] else "no-known-issues"}
