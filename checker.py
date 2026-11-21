"""Deterministic, source-linked checks. A passed check is not a boot guarantee."""
import os
import plistlib
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

from defusedxml import ElementTree

import amd

VERSION = "1.0.8"
ROOT = Path(__file__).resolve().parent
GUID = "7C436110-AB2A-4BBB-A880-FE41995C9F82"
DOCS = f"https://github.com/acidanthera/OpenCorePkg/blob/{VERSION}/Docs/Configuration.pdf"
GUIDE = "https://dortania.github.io/OpenCore-Install-Guide/"
GENERATIONS = {"haswell", "broadwell", "skylake", "kaby-lake", "coffee-lake", "comet-lake", "zen", "other"}
GPUS = {"amd-supported", "amd-polaris-vega", "amd-navi", "amd-navi24", "intel-supported", "intel-xe", "nvidia-kepler", "nvidia-maxwell", "nvidia-turing", "other"}
CHIPSETS = {"other", "100-200", "z390", "300", "z370", "400", "b550-a520", "x570-b450", "trx40"}
OS_VERSIONS = {"10.15", "11.0", "11.3", "12", "13", "14", "15"}


class ConfigError(ValueError):
    pass


def parse_config(data):
    if len(data) > 2 * 1024 * 1024:
        raise ConfigError("Use a config.plist smaller than 2 MB, not a full EFI archive.")
    if not data.strip():
        raise ConfigError("This file is empty. Choose a non-empty OpenCore config.plist.")
    for signature, kind in ((b"%PDF", "a PDF"), (b"\x89PNG", "an image"), (b"\xff\xd8\xff", "an image"),
                            (b"GIF8", "an image"), (b"PK\x03\x04", "a ZIP archive")):
        if data.startswith(signature):
            raise ConfigError(f"This is {kind}, not an OpenCore config. Upload config.plist from your EFI/OC folder.")
    stripped = data.lstrip()
    if not data.startswith(b"bplist") and not stripped.startswith((b"<", b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff")):
        kind = "JSON" if stripped.startswith((b"{", b"[")) else "plain text or another file format"
        raise ConfigError(f"This looks like {kind}. Choose OpenCore's XML or binary config.plist, not a text or JSON export.")
    try:
        if not data.startswith(b"bplist"):
            tree = ElementTree.fromstring(data, forbid_entities=True, forbid_external=True)
            if tree.tag != "plist":
                raise ConfigError("This XML is not an Apple property list. Choose OpenCore's config.plist, not a web page or another XML file.")
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
            if obj is None or isinstance(obj, plistlib.UID):
                raise ConfigError("This binary plist contains null or UID values, which OpenCore's XML config format does not support.")
            if isinstance(obj, dict):
                stack.extend((v, depth + 1) for v in obj.values())
            elif isinstance(obj, list):
                stack.extend((v, depth + 1) for v in obj)
        if not {"ACPI", "Booter", "DeviceProperties", "Kernel", "Misc", "NVRAM", "PlatformInfo", "UEFI"}.intersection(result):
            raise ConfigError("This is a valid property list, but it has no OpenCore sections. Choose EFI/OC/config.plist, not an application's Info.plist or another settings file.")
        return result
    except ConfigError:
        raise
    except Exception as error:
        raise ConfigError("This property list is malformed or damaged (XML syntax, binary data or a value type is invalid). Choose an intact OpenCore config.plist, or re-save that config with ProperTree.") from error


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
    cores = str(form.get("cpu_cores", "")).strip()
    if cores and (not re.fullmatch(r"[0-9]{1,3}", cores) or not 1 <= int(cores) <= 255):
        raise ConfigError("Enter 1–255 physical CPU cores, not the thread count, or leave it unknown.")
    profile["cpu_cores"] = int(cores) if cores else None
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
    page = "haswell" if cpu == "broadwell" else "coffee-lake" if cpu == "other" else cpu
    source = GUIDE + ("AMD/zen.html" if cpu == "zen" else f"config.plist/{page}.html")
    native = native_validate(config)

    def check(key, passed, title, path, detail, fix, severity="warning", link=source, applicable=True):
        if native["status"] in ("passed", "failed") and (key == "lilu-quirk" or key.startswith("order-")):
            applicable = False
        checks.append({"id": key, "status": "skipped" if not applicable else "passed" if passed else "failed"})
        if applicable and not passed:
            findings.append({"id": key, "severity": severity, "title": title, "path": path,
                             "detail": detail, "fix": fix, "source": link})

    kernel_range = darwin_range(profile["macos"])
    kexts = [str(e.get("BundlePath", "")).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()
             for e in entries(config, "Kernel/Add", kernel_range)]
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
    check("xhci", q("XhciPortLimit") is not True, "USB port-limit patch is enabled on macOS 11.3+", "Kernel → Quirks → XhciPortLimit", "OpenCore 0.9.3+ improved this quirk on macOS 11–14, but a proper USB map is still required.", "Set XhciPortLimit to false and create a proper USB map.", applicable=os_version >= (11, 3))
    check("usbmap", has("USBMap.kext") or has("UTBMap.kext") or has("USBPorts.kext"), "A USB map is not identifiable by filename", "Kernel → Add", "Custom-named maps or firmware mappings cannot be recognized from a plist alone.", "Verify that you have a working, machine-specific USB map. Do not rename a working custom map just to silence this warning.", link="https://dortania.github.io/OpenCore-Post-Install/usb/")
    check("usbinjectall", not has("USBInjectAll.kext"), "USBInjectAll is still enabled", "Kernel → Add", "USBInjectAll does not work on AMD CPUs." if cpu == "zen" else "USBInjectAll is a setup aid, not a finished USB map.", "Replace it with a tested USB map.", link=GUIDE + "ktext.html#usb")
    check("lilu-quirk", q("DisableLinkeditJettison") is True, "Lilu needs DisableLinkeditJettison", "Kernel → Quirks → DisableLinkeditJettison", "This quirk is required for reliable Lilu operation.", "Set DisableLinkeditJettison to true.", "error", DOCS, applicable=has("Lilu.kext"))
    for plugin in ("WhateverGreen.kext", "AppleALC.kext", "VirtualSMC.kext"):
        check("order-" + plugin, has("Lilu.kext") and kexts.index("lilu.kext") < kexts.index(plugin.lower()) if has(plugin) else True,
              f"{plugin} needs Lilu earlier in the list", "Kernel → Add", "Dependencies must be enabled and loaded before their plugins.", "Enable Lilu and move it above this kext.", "error", DOCS, applicable=has(plugin))
    check("dsdt", not any(Path(p).name == "dsdt.aml" for p in acpi), "A complete DSDT is being injected", "ACPI → Add", "Dortania advises against adding your firmware's complete DSDT.", "Use targeted SSDTs instead of injecting DSDT.aml.")
    check("plug", any("plug" in p for p in acpi), "CPU power-management SSDT is not identifiable", "ACPI → Add", "Haswell and newer desktops normally need SSDT-PLUG. Custom filenames or alternate methods cannot be identified here.", "Verify plugin-type CPU power management and add a machine-appropriate SSDT-PLUG if absent.", applicable=modern_intel)
    check("ec-usbx", any("ec" in p for p in acpi) and (cpu in ("haswell", "broadwell") or any("usbx" in p for p in acpi)), "EC / USB power SSDTs are not identifiable", "ACPI → Add", "Haswell/Broadwell need an EC fix; Skylake and newer and AMD Zen also need USBX. Custom SSDTs may already provide these.", "Verify your ACPI tables against Dortania's EC and USBX guidance.")
    check("awac", any("awac" in p or "rtc0" in p for p in acpi), "Check the board's RTC / AWAC fix", "ACPI → Add", "300-series boards may need an RTC fix; B460 and Z490 require SSDT-AWAC or SSDT-RTC0.", "Inspect your ACPI tables before adding an AWAC/RTC fix.", applicable=profile["chipset"] in ("z390", "300", "z370", "400"))
    check("pmc", any("pmc" in p for p in acpi), "Check native NVRAM support on this chipset", "ACPI → Add", "True 300-series boards other than Z370 may need SSDT-PMC. A filename check cannot prove firmware behavior.", "Test NVRAM persistence and inspect whether SSDT-PMC is needed.", applicable=profile["chipset"] in ("z390", "300"))
    check("cpur", any("cpur" in p for p in acpi), "Check CPU definitions on B550 / A520", "ACPI → Add", "Dortania lists SSDT-CPUR for B550/A520 CPU definitions. This is only a filename hint; custom-named tables may already contain the fix.", "For B550/A520, inspect the firmware CPU definitions against the linked SSDT-CPUR source. Add the table only if that fix is absent; an enabled custom-named equivalent does not need renaming.", link=GUIDE + "AMD/zen.html#acpi", applicable=cpu == "zen" and profile["chipset"] == "b550-a520")
    check("cfglock", q("AppleXcpmCfgLock") is True, "CFG-Lock is enabled without the XCPM workaround", "Kernel → Quirks → AppleXcpmCfgLock", "You reported CFG-Lock enabled. This can prevent CPU power management from initializing.", "Prefer disabling CFG-Lock in BIOS; otherwise enable AppleXcpmCfgLock.", "error", applicable=modern_intel and profile["cfg_lock"] == "enabled")
    check("vtd", q("DisableIoMapper") is True or "dart=0" in boot.split(), "VT-d is enabled without an I/O mapper workaround", "Kernel → Quirks → DisableIoMapper", "The selected BIOS state may need DisableIoMapper; a deliberately configured DMAR table can be an exception.", "Review your DMAR/IOMMU setup; otherwise enable DisableIoMapper or disable VT-d.", applicable=modern_intel and profile["vt_d"] == "enabled")
    check("rebar", get(config, "Booter/Quirks/ResizeAppleGpuBars") == 0, "Resizable BAR is enabled without the macOS workaround", "Booter → Quirks → ResizeAppleGpuBars", "With Resizable BAR enabled in firmware, Dortania recommends ResizeAppleGpuBars = 0.", "Set ResizeAppleGpuBars to 0, or disable Resizable BAR in firmware.", "error", applicable=profile["rebar"] == "enabled")
    check("z390services", get(config, "Booter/Quirks/ProtectUefiServices") is True, ("400-series" if profile["chipset"] == "400" else "Z390") + " firmware services are not protected", "Booter → Quirks → ProtectUefiServices", "Dortania recommends this on Z390 and requires it on Z490.", "Enable ProtectUefiServices and verify against your firmware.", applicable=profile["chipset"] in ("z390", "400"))
    for field, path in [("DummyPowerManagement", "Kernel/Emulate/"), ("ProvideCurrentCpuInfo", "Kernel/Quirks/")]:
        check("amd-" + field, get(config, path + field) is True, f"AMD Zen needs {field}", (path + field).replace("/", " → "), "Dortania's AMD Zen configuration enables this option.", f"Set {field} to true for the selected AMD Zen profile.", "error", applicable=cpu == "zen")
    amd_gap = None
    if cpu == "zen":
        try:
            result = amd.inspect_patches(get(config, "Kernel/Patch", []), kernel_range, profile["cpu_cores"])
            check("amd-patches", result["recognized"] > 0, "No recognized AMD Vanilla patches cover this macOS", "Kernel → Patch",
                  "An unrelated patch or an edited comment cannot prove AMD support. This compares actual patch bytes and operation fields against the pinned AMD Vanilla reference.",
                  "Import the linked reference patch set, enable the required entries and edit the physical core count.", "error", amd.SOURCE)
            check("amd-required-patches", not result["missing"], "Required AMD reference patches are missing or modified", "Kernel → Patch",
                  "Missing, disabled, wrong-identifier, modified or out-of-range reference operations: " + "; ".join(result["missing"]),
                  "Compare these operations with the linked reference for the selected macOS. Custom or newer patch sets require independent review; they are not certified by this checker.", "error", amd.SOURCE)
            check("amd-core-count", not result["core_errors"], "AMD patch core count is unset or does not match", "Kernel → Patch → Replace",
                  "; ".join(result["core_errors"]), "Set the second byte of the applicable cpuid_cores_per_package Replace values to your physical core count in hexadecimal, not the thread count.", "error", amd.SOURCE + "#read-me-first")
            check("amd-patch-ranges", not result["range_errors"], "AMD patch kernel bounds extend beyond the reference", "Kernel → Patch → MinKernel / MaxKernel",
                  "; ".join(result["range_errors"]), "Restore the reference bounds. Patches for different point releases must not run together outside their intended ranges.", "error", amd.SOURCE)
            check("amd-cores-unknown", profile["cpu_cores"] is not None, "Physical CPU core count is unknown", "Hardware → CPU cores",
                  "Zero placeholders are detected, but nonzero replacements cannot be verified against your hardware without its physical core count.",
                  "Enter the physical core count from your CPU specifications and run again. Do not use the thread count.", "warning", amd.SOURCE + "#read-me-first")
            if profile["cpu_cores"] is None:
                amd_gap = "AMD physical core count is unknown; nonzero core-count replacements cannot be verified against your CPU."
        except (OSError, ValueError, KeyError, plistlib.InvalidFileException):
            amd_gap = "Verified AMD Vanilla reference unavailable: patch bytes, required groups and core edits were not checked. Run setup_opencore.py."
        for field in ("AppleCpuPmCfgLock", "AppleXcpmCfgLock", "AppleXcpmExtraMsrs"):
            check("amd-intel-" + field, q(field) is not True, f"Intel-only {field} is enabled on AMD", "Kernel → Quirks → " + field,
                  "Dortania's AMD Zen profile leaves this Intel power-management quirk disabled.", "Disable this quirk for the selected AMD Zen profile.", link=source + "#quirks")
        for field in ("Cpuid1Data", "Cpuid1Mask"):
            value = get(config, "Kernel/Emulate/" + field, b"")
            check("amd-intel-" + field, isinstance(value, bytes) and not any(value), f"Intel CPUID emulation remains in {field}", "Kernel → Emulate → " + field,
                  "AMD Zen does not use Intel CPUID spoofing. The guide leaves these fields blank/zero.", "Clear the Intel CPUID emulation value for the selected AMD profile.", link=source + "#emulate")
    gpu_source = "https://dortania.github.io/GPU-Buyers-Guide/"
    bq = lambda name: get(config, "Booter/Quirks/" + name)
    check("booter-memory", not (bq("EnableWriteUnprotector") is True and bq("RebuildAppleMemoryMap") is True), "Memory-map quirks commonly conflict", "Booter → Quirks", "EnableWriteUnprotector and RebuildAppleMemoryMap commonly conflict; enable one.", "Choose the memory-map strategy documented for your firmware.")
    check("write-unprotector", bq("EnableWriteUnprotector") is True, "Check the firmware memory-write workaround", "Booter → Quirks → EnableWriteUnprotector", "Dortania recommends EnableWriteUnprotector enabled and RebuildAppleMemoryMap disabled on Haswell through Kaby Lake.", "Enable EnableWriteUnprotector and disable RebuildAppleMemoryMap.", applicable=cpu in ("haswell", "broadwell", "skylake", "kaby-lake"))
    check("setup-virtual-map", bq("SetupVirtualMap") is not True, "SetupVirtualMap is enabled on an incompatible platform", "Booter → Quirks → SetupVirtualMap", "B550, A520 and TRx40 must disable this; Comet Lake ASUS, Gigabyte and AsRock boards will not boot with this on.", "Disable SetupVirtualMap for these boards.", "error" if cpu == "zen" else "warning", applicable=cpu == "comet-lake" or (cpu == "zen" and profile["chipset"] in ("b550-a520", "trx40")))
    check("vault", get(config, "Misc/Security/Vault") != "Secure", "Secure Vault requires a created vault", "Misc → Security → Vault", "Secure will not boot without a vault created; a plist cannot prove one exists.", "Use Optional during setup, or create and verify your vault.", link=source + "#misc")
    check("amd-plug", not any("plug" in p for p in acpi), "AMD users should not use SSDT-PLUG", "ACPI → Add", "SSDT-PLUG configures Intel CPU power management.", "Remove SSDT-PLUG on AMD.", applicable=cpu == "zen")
    check("amd-intel-acpi", not any("awac" in p or "pmc" in p for p in acpi), "Intel chipset SSDTs are enabled on AMD", "ACPI → Add", "AWAC and PMC tables target Intel 300-series firmware, not AMD Zen.", "Remove Intel-specific AWAC/PMC tables.", applicable=cpu == "zen")
    check("amd-smc-plugins", not (has("SMCProcessor.kext") or has("SMCSuperIO.kext")), "Intel VirtualSMC plugins are enabled on AMD", "Kernel → Add", "SMCProcessor and SMCSuperIO are not for AMD CPU based systems.", "Remove these plugins; consider SMCAMDProcessor for AMD monitoring.", link=GUIDE + "ktext.html#virtualsmc-plugins", applicable=cpu == "zen")
    check("amd-cfglock", q("AppleXcpmCfgLock") is not True, "AMD does not need AppleXcpmCfgLock", "Kernel → Quirks", "AMD users can ignore this Intel XCPM workaround.", "Set AppleXcpmCfgLock to false.", "info", applicable=cpu == "zen")
    check("amd-iommu", profile["vt_d"] != "enabled", "IOMMU is reported enabled on AMD", "BIOS → IOMMU", "Dortania's AMD BIOS settings disable IOMMU.", "Disable IOMMU in BIOS.", "info", applicable=cpu == "zen")
    check("vtd-dart", "dart=0" not in boot.split() or q("DisableIoMapper") is True, "Prefer DisableIoMapper over dart=0", "Kernel → Quirks → DisableIoMapper", "DisableIoMapper is a better alternative to dart=0 as SIP can stay on.", "Remove dart=0 and use DisableIoMapper if an I/O mapper workaround is needed.", "info", applicable=modern_intel)
    check("gpu-navi", "agdpmod=pikera" in boot.split(), "Navi needs agdpmod=pikera", "NVRAM → Add → boot-args", "Navi 10/21/22/23 GPUs need the WhateverGreen board-ID workaround.", "Add agdpmod=pikera.", applicable=profile["gpu"] == "amd-navi")
    check("gpu-polaris-pikera", "agdpmod=pikera" not in boot.split(), "Do not use pikera without Navi", "NVRAM → Add → boot-args", "Don't use if you don't have Navi; Polaris and Vega do not need this argument.", "Remove agdpmod=pikera.", applicable=profile["gpu"] == "amd-polaris-vega")
    check("gpu-legacy-intel", False, "This Intel iGPU lost native support after Monterey", "Hardware → Graphics", "Haswell and Broadwell graphics have highest supported OS Monterey (12).", "Use supported graphics for macOS 13 or newer.", "error", gpu_source + "modern-gpus/intel-gpu.html", applicable=profile["gpu"] == "intel-supported" and cpu in ("haswell", "broadwell") and os_version >= (13,))
    display_ids = {"haswell": {"0300220D"}, "broadwell": {"07002216", "06002616"}, "skylake": {"00001219"}, "kaby-lake": {"00001259"}, "coffee-lake": {"07009B3E", "00009B3E"}, "comet-lake": {"07009B3E", "00009B3E"}}
    headless_ids = {"haswell": "04001204", "skylake": "01001219", "kaby-lake": "03001259", "coffee-lake": "0300913E", "comet-lake": "0300C89B"}
    if cpu == "skylake" and os_version >= (13,):
        display_ids["skylake"] = {"00001259"}
    device_properties = get(config, "DeviceProperties/Add", {})
    igpu = device_properties.get("PciRoot(0x0)/Pci(0x2,0x0)", {}) if isinstance(device_properties, dict) else {}
    platform_id = igpu.get("AAPL,ig-platform-id") if isinstance(igpu, dict) else None
    platform_hex = platform_id.hex().upper() if isinstance(platform_id, bytes) else None
    if modern_intel and profile["gpu"] == "intel-supported":
        check("igpu-platform-id", platform_hex in display_ids[cpu], "Check the display iGPU platform-id", "DeviceProperties → Add → AAPL,ig-platform-id", "Missing platform-id can cause a black screen; the display id must match the CPU family.", "Use display bytes " + ", ".join(sorted(display_ids[cpu])) + ".", "error" if platform_id is None else "warning", source + "#deviceproperties")
    elif modern_intel and cpu in headless_ids and profile["gpu"] in ("amd-supported", "amd-polaris-vega", "amd-navi", "amd-navi24", "nvidia-kepler", "nvidia-maxwell", "nvidia-turing"):
        check("igpu-headless", platform_hex == headless_ids[cpu], "Use a headless iGPU id with a discrete display GPU", "DeviceProperties → Add → AAPL,ig-platform-id", "The selected discrete GPU drives the display; an injected iGPU should use a headless framebuffer.", "Use headless bytes " + headless_ids[cpu] + ".", link=source + "#deviceproperties", applicable=platform_id is not None)
    check("gpu-unsupported", profile["gpu"] not in ("intel-xe", "nvidia-turing", "amd-navi24"), "The selected GPU family has no native macOS acceleration", "Hardware → Graphics", "Intel Xe, NVIDIA Turing and later, and AMD Navi 24 are not natively supported by macOS.", "Use a supported GPU or disable this GPU and use a supported alternative. A plist edit cannot add a missing driver.", "error", gpu_source)
    check("gpu-maxwell", profile["gpu"] != "nvidia-maxwell", "NVIDIA Maxwell / Pascal is unsupported on this macOS", "Hardware → Graphics", "NVIDIA web drivers stop at macOS High Sierra, older than every target offered here.", "Use a supported graphics device for this macOS version.", "error", gpu_source)
    check("gpu-kepler", profile["gpu"] != "nvidia-kepler", "NVIDIA Kepler lost native support in Monterey", "Hardware → Graphics", "Kepler has no native acceleration on macOS 12 and newer. Root patching is outside this checker's coverage.", "Use a supported GPU or independently evaluate the limitations of legacy patching.", "error", gpu_source, applicable=os_version >= (12,))
    check("gpu-disable", "-wegnoegpu" not in boot.split(), "Boot arguments disable external GPUs", "NVRAM → Add → boot-args", "-wegnoegpu disables external graphics when WhateverGreen is active.", "Remove this argument if you intend to use the discrete GPU.", applicable=profile["gpu"] in ("amd-supported", "amd-polaris-vega", "amd-navi", "nvidia-kepler"))
    check("debug-args", not any(a in boot.split() for a in ("-v", "debug=0x100", "keepsyms=1")), "Debug boot arguments are still present", "NVRAM → Add → boot-args", "Verbose and debug arguments are useful during setup; they are not necessarily faults.", "Keep these while troubleshooting; remove them only after the system is stable.", "info")
    check("amfi", not any(a.startswith(("amfi_get_out_of_my_way=1", "amfi=0x80")) for a in boot.split()), "AMFI is disabled by a boot argument", "NVRAM → Add → boot-args", "Disabling AMFI weakens macOS code-signing protection.", "Remove the disabling argument unless a deliberate, documented setup requires it.", link=DOCS)
    sip = get(config, f"NVRAM/Add/{GUID}/csr-active-config")
    check("sip", sip in (None, b"\x00\x00\x00\x00"), "System Integrity Protection is not fully enabled", "NVRAM → Add → csr-active-config", "A nonzero CSR configuration disables some system protections. Legacy patches may require this intentionally.", "Use four zero data bytes for full SIP, unless your patching setup explicitly requires otherwise.", link=source + "#nvram")
    check("dmg", get(config, "Misc/Security/DmgLoading") != "Any", "Unsigned disk images are allowed", "Misc → Security → DmgLoading", "DmgLoading = Any permits unsigned disk images.", "Use Signed for normal Apple recovery images.", link=source + "#misc")
    generic = get(config, "PlatformInfo/Generic", {})
    generic = generic if isinstance(generic, dict) else {}
    model = generic.get("SystemProductName")
    recommended = {"haswell": {"iMac14,4", "iMac15,1"}, "broadwell": {"iMac16,2", "iMac17,1"}, "skylake": {"iMac17,1"}, "kaby-lake": {"iMac18,1", "iMac18,3"}, "coffee-lake": {"iMac19,1", "iMac19,2"}, "comet-lake": {"iMac20,1", "iMac20,2"}, "zen": {"MacPro7,1", "iMacPro1,1", "MacPro6,1"}}
    def last_supported(name):
        if re.fullmatch(r"iMac(?:14,\d+|15,1|16,\d+|17,1)|MacPro6,1", name):
            return 12
        if re.fullmatch(r"iMac18,\d+", name):
            return 13
        if re.fullmatch(r"iMac(?:19|20),\d+|iMacPro1,1|MacPro7,1", name):
            return 15
        return None
    if cpu in recommended and isinstance(model, str):
        accepted = set(recommended[cpu])
        if cpu == "haswell" and os_version[0] == 12:
            accepted.update(("iMac16,2", "iMac17,1"))
        if cpu != "zen" and all((last_supported(m) or 99) < os_version[0] for m in accepted):
            accepted = {"iMac18,1", "iMac18,3"} if os_version[0] == 13 else {"iMac19,1", "iMac19,2"}
        expired = last_supported(model) is not None and last_supported(model) < os_version[0]
        check("smbios", not expired and model in accepted, "SMBIOS is unsupported on the target macOS" if expired else "SMBIOS does not match the selected CPU family", "PlatformInfo → Generic → SystemProductName", "The model must support the target OS and match Dortania's platform recommendations.", "Review " + ", ".join(sorted(accepted)) + " for this platform and target.", "error" if expired else "warning", source + "#platforminfo")
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
    check("uuid", valid_uuid, "SystemUUID is not a valid UUID", "PlatformInfo → Generic → SystemUUID", "The UUID must use the standard 8-4-4-4-12 hexadecimal format.", "Generate a valid, unique UUID with GenSMBIOS.", "error", applicable=value != "")
    rom = generic.get("ROM")
    check("rom", isinstance(rom, bytes) and len(rom) == 6 and rom not in (bytes(6), bytes.fromhex("112233445566")), "ROM is missing, malformed, or an example value", "PlatformInfo → Generic → ROM", "ROM should be six nonzero, machine-specific bytes, commonly from a stable network adapter.", "Set ROM as six bytes of Data appropriate for this machine.")

    if native["status"] == "failed":
        findings.insert(0, {"id": "ocvalidate", "severity": "error", "title": f"OpenCore validator reported {native['issues']} issue(s)",
                            "path": "OpenCore → Schema & semantics", "detail": "The official version-matched validator rejected this config. Open the raw diagnostics below for the exact keys.",
                            "fix": "Resolve these diagnostics with the OpenCore 1.0.8 configuration manual, then run the check again.", "source": DOCS})
    gaps = ["EFI files: presence, versions, executable contents and kext dependencies on disk were not inspected.",
            "ACPI contents, firmware behavior, USB mapping quality, unlisted SMBIOS/macOS combinations and actual boot success require manual verification.",
            "CPU/GPU model and board text are report labels, not automatically detected hardware. Rules use your selected families and chipset.",
            "Kext and patch presence checks count only x86_64/Any entries whose kernel ranges cover the selected macOS family. Narrower ranges need manual review for your exact point release."]
    if cpu == "other":
        gaps.append("No CPU-generation-specific rules ran for Other / unknown. This is a desktop checker; laptop and newer Intel workarounds are not covered.")
    if profile["gpu"] == "other":
        gaps.append("GPU compatibility is unknown for the selected family.")
    if any(profile[k] == "unknown" for k in ("cfg_lock", "vt_d", "rebar")):
        gaps.append("Some BIOS settings are unknown; the corresponding conditional checks were skipped.")
    if cpu == "zen":
        gaps.append("AMD kernel patches are compared with pinned reference " + amd.COMMIT[:12] + "; optional PAT choices, board/Zen-4-specific IOPCI patches, custom patch sets and actual kernel execution still need manual review.")
    if amd_gap:
        gaps.insert(0, amd_gap)
    if native["status"] == "unavailable":
        gaps.insert(0, "Official validator unavailable: schema and semantic coverage is incomplete.")
    counts = {s: sum(f["severity"] == s for f in findings) for s in ("error", "warning", "info")}
    return {"version": VERSION, "profile": profile, "findings": findings, "counts": counts, "native": native,
            "checks": checks, "checked": sum(c["status"] != "skipped" for c in checks),
            "passed": sum(c["status"] == "passed" for c in checks), "skipped": sum(c["status"] == "skipped" for c in checks),
            "gaps": gaps, "status": "incomplete" if native["status"] == "unavailable" or amd_gap else "issues" if counts["error"] or counts["warning"] else "no-known-issues"}
