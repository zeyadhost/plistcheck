# plistcheck

A second pair of eyes for your OpenCore config. Upload an XML or binary `config.plist`, select your hardware and target macOS, and get actionable findings with links to their sources.

**Not “every issue”, and not a boot guarantee.** A valid plist can still describe an unbootable EFI. The interface and exported report explicitly list coverage gaps.

## Run locally

Requires Python 3.10+ and an x86-64 Linux/macOS/Windows host. Production serving uses Gunicorn on Linux/macOS; Windows can use Flask for local development.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python setup_opencore.py
gunicorn --bind 0.0.0.0:8000 --workers 2 --threads 2 --timeout 30 app:app
```

Open `http://localhost:8000`. “Try an example” loads a synthetic Coffee Lake/Z390 config that passes official validation but contains hardware-guidance issues. A pristine form gets the matching example profile; if you already edited hardware fields, those selections are preserved and the example notice explains the comparison. Never boot the demo.

On Windows, activate `.venv\Scripts\activate` and use `flask --app app run --port 8000` for local development instead of Gunicorn. The production Docker image targets `linux/amd64`:

```sh
docker build --platform linux/amd64 -t plistcheck .
docker run --rm -p 8000:8000 plistcheck
```

No frontend build, Node runtime, database, or credentials required. The frontend is plain HTML, CSS and JavaScript. There are no existing pre-commit hooks.

## Validation layers

1. **Safe parsing:** XML and binary property lists, XML duplicate-key detection, entity/external-reference rejection, dictionary root validation, size/nesting/object limits.
2. **Official validation:** the unmodified `ocvalidate` executable from OpenCore **1.0.8**, covering its schema, value constraints, patch consistency, kext ordering, and cross-setting semantics. Binary uploads are normalized to XML before running the tool. Raw diagnostics are available in the result; one grouped finding may represent several native issues.
3. **Hardware-aware rules:** source-linked checks for Intel Haswell through Comet Lake desktops and AMD Zen, selected Intel 100–400-series and AMD B550/A520/X570/X470/B450/A320/TRx40 chipset options and ACPI hints, reported BIOS state, GPU-family support boundaries, macOS-dependent USB settings, SMC/Lilu dependencies, SMBIOS family and macOS support recommendations, DeviceProperties display/headless iGPU ids, platform Booter quirks, SMBIOS placeholders, and security/debug settings.

Only OpenCore 1.0.8 is supported. The config format cannot reliably reveal the installed bootloader version: verify that it matches the displayed validator version. `setup_opencore.py` downloads the official release archive and pinned AMD Vanilla reference, verifies their SHA-256 checksums, and installs them into ignored `vendor/`. No validator binary or patch set is committed. A missing or timed-out validator produces **incomplete coverage**, never a schema pass.

### What the profile means

Select Desktop or Laptop before choosing a CPU. Desktop profiles never run laptop rules. Intel laptop checks cover Haswell, Broadwell, Skylake, Kaby Lake, Coffee Lake, Whiskey Lake, Comet Lake, and Ice Lake. They flag desktop SMBIOS models and desktop EC tables, `_OSI` → `XOSI` renames without an identifiable companion SSDT, and I²C kext ordering. They also mark backlight and battery behavior for review where appropriate.

Exact model names are optional labels, **not automatically decoded hardware**. Unknown BIOS states skip conditional checks. Broad GPU families do not certify a particular GPU or framebuffer. The checker can confirm that an injected `AAPL,ig-platform-id` is four bytes of plist Data; it does not select a laptop framebuffer for your device. An unknown device type or a CPU family not covered for that device results in incomplete coverage even if official validation succeeds.

For AMD Zen, enter the physical CPU core count, not its thread count. Kernel patch bytes, identifiers, masks, operation fields and applicable kernel intervals are compared with AMD Vanilla commit `eaf52ef292abf4ebec899df6d48626569ba50cc6`. Missing/disabled/modified required reference operations, zero core-count placeholders, mismatches against the supplied core count, and widened point-release ranges are errors. Comments are not used as patch identity. Ventura's core-patch transition is checked across both required intervals. An unknown physical core count or unavailable verified reference leaves coverage incomplete. This is reference comparison, not a proof that patches match or execute against a particular kernel binary; custom/newer sets need independent review.

SSDT and USB-map hints are filename heuristics and are warnings, not proof that a table or map is missing. Custom names and firmware-native alternatives can be valid. BIOS values are self-reported, not detected. A deliberately configured DMAR table can be an exception to the VT-d recommendation.

### Not checked

- The actual EFI directory: file existence, binary versions, on-disk kext dependency metadata.
- ACPI contents, firmware implementation, USB map correctness, network/audio device IDs.
- Optional AMD PAT alternatives, board/Zen-4-specific IOPCI patches, custom/newer AMD sets, and actual kernel-binary applicability/execution.
- Exhaustive CPU/GPU/macOS/SMBIOS compatibility, BIOS state, serial validity with Apple, or boot success.
- OpenCore releases other than 1.0.8 and macOS later than Sequoia.

The app never edits a config or offers a fake “fix everything” button. Keep a known-good EFI backup and validate each change.

## Privacy and security

Uploads are sent to the server for checking, not processed solely in the browser. The parsed file stays in memory; a normalized config is written to a private temporary directory for `ocvalidate`, then deleted when the subprocess finishes. The app does not persist uploads or reports, log request bodies, use analytics, or contact Apple. Hosting infrastructure may keep normal HTTP access logs. Downloaded JSON contains the supplied hardware labels and validator diagnostics: review it before sharing. The app does not embed the full config in a report.

Requests are limited to 2 MB plus multipart overhead; native validation has an 8-second timeout. XML entities are prohibited. Uploaded contents are never executed. Production deployment should provide TLS and, for public multi-user hosting, infrastructure-level rate/concurrency limits. The included Docker container runs as a non-root user.

## Tests

```sh
python -m pytest -q
```

Tests exercise the real pinned validator alongside parser failures, malformed input types, profile validation, dependency ordering, hardware conditions, GPU/OS boundaries, and unavailable-validator behavior. GitHub Actions installs the verified upstream asset before running the suite.

## Sources and attribution

- [OpenCorePkg 1.0.8](https://github.com/acidanthera/OpenCorePkg/releases/tag/1.0.8), [ocvalidate documentation](https://github.com/acidanthera/OpenCorePkg/tree/1.0.8/Utilities/ocvalidate), and [Configuration.pdf](https://github.com/acidanthera/OpenCorePkg/blob/1.0.8/Docs/Configuration.pdf). Upstream utilities and the sample remain under their upstream licenses; they are downloaded, not relicensed by this project.
- [Dortania OpenCore Install Guide](https://dortania.github.io/OpenCore-Install-Guide/) and [GPU Buyers Guide](https://dortania.github.io/GPU-Buyers-Guide/). Desktop/AMD guidance reviewed against Install Guide commit `6680f2df86de1283083b0208e8a8042fa1952b2c`. The guide includes firmware-dependent exceptions; read each linked source before acting.
- [AMD Vanilla patches](https://github.com/AMD-OSX/AMD_Vanilla/tree/eaf52ef292abf4ebec899df6d48626569ba50cc6). Reference SHA-256: `4bc820109b3d020c3c547fa23c49e0098e4f4a2c625ed6184dd54e390e84e1ab`. Downloaded for data comparison only; never executed or represented as this project's own patches.

Independent project by [zeyadhost](https://github.com/zeyadhost), not affiliated with Apple, Acidanthera, or Dortania.
