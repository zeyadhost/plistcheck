# plistcheck

plistcheck runs OpenCore's validator and hardware-specific checks on your `config.plist`. Upload an XML or binary plist, choose your hardware and target macOS, and review findings with links to the relevant documentation.

[Hosted preview (requires Devin organization access)](https://plistcheck--yfyzendo.preview.devinapps.com)

A valid plist can still fail to boot. Keep a known-good EFI backup before making changes. Coverage gaps appear in the interface and exported JSON report.

## Run locally

You need Python 3.10+ on an x86-64 Linux, macOS or Windows host. On Linux and macOS:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python setup_opencore.py
gunicorn --bind 0.0.0.0:8000 --workers 2 --threads 2 --timeout 30 app:app
```

Open `http://localhost:8000`. "Try an example" loads a synthetic Coffee Lake/Z390 config that passes official validation and has hardware-guidance issues. It selects the matching hardware profile unless you have already edited the form. In that case, it keeps your selections and explains the comparison. Do not boot the demo.

On Windows, activate `.venv\Scripts\activate` and run `flask --app app run --port 8000` for local development. Gunicorn serves production deployments on Linux and macOS.

The Docker image targets `linux/amd64`:

```sh
docker build --platform linux/amd64 -t plistcheck .
docker run --rm -p 8000:8000 plistcheck
```

Flask serves plain HTML, CSS and JavaScript. There is no frontend build, Node runtime, database or credential setup.

## Validation layers

1. The parser accepts XML and binary plists. It rejects duplicate XML keys, entities and external references, non-dictionary roots, and files that exceed size, nesting or object limits.
2. The unmodified OpenCore 1.0.8 `ocvalidate` checks the schema, values, patch consistency, kext ordering and relationships between settings. Binary uploads are converted to XML first. The report includes raw diagnostics; a grouped finding can contain several native issues.
3. Hardware rules check CPU and Booter quirks, chipset and ACPI hints, reported BIOS settings, GPU support, macOS-dependent USB settings, SMC/Lilu dependencies, SMBIOS recommendations and placeholders, display/headless iGPU IDs, and security/debug settings. Findings link to their sources.

Only OpenCore 1.0.8 is supported. Check your installed bootloader version against the displayed validator version; the config cannot reliably identify it. A missing or timed-out validator leaves coverage incomplete.

`setup_opencore.py` downloads the official release archive and pinned AMD Vanilla reference, verifies their SHA-256 checksums, and installs them in ignored `vendor/`. Validator binaries and patches are not committed to this repository.

### Hardware profiles

Select Desktop or Laptop before choosing a CPU. Desktop rules cover Intel Haswell through Comet Lake and AMD Zen. Chipset options include selected Intel 100/200/300/400-series chipsets and AMD B550/A520/X570/X470/B450/A320/TRx40.

Laptop rules cover Intel Haswell, Broadwell, Skylake, Kaby Lake, Coffee Lake, Whiskey Lake, Comet Lake and Ice Lake. They flag desktop SMBIOS models and EC tables, `_OSI` → `XOSI` renames without an identifiable companion SSDT, and I²C kext ordering problems. Backlight and battery behavior may need review. Desktop profiles do not run laptop rules.

CPU, GPU and board model names are optional report labels; the checker does not decode them. Rules use your selected families, chipset and self-reported BIOS settings. Unknown BIOS settings skip conditional checks. An unknown device type or unsupported CPU/device combination leaves hardware coverage incomplete, even when official validation passes.

GPU families cannot confirm support for an exact chip or framebuffer. The checker verifies that `AAPL,ig-platform-id` is four bytes of plist Data; choosing a laptop framebuffer still requires device-specific research.

SSDT and USB-map warnings use filenames. Custom names and firmware-native alternatives can be valid, so inspect the files before treating a warning as a missing component. A deliberately configured DMAR table can be an exception to the VT-d recommendation.

### AMD patches

For AMD Zen, enter the physical CPU core count, not the thread count. Kernel patch bytes, identifiers, masks, operation fields and kernel intervals are compared with AMD Vanilla commit `eaf52ef292abf4ebec899df6d48626569ba50cc6`. Comments do not identify patches.

Errors include missing, disabled or modified required operations, zero core-count placeholders, core-count mismatches and widened point-release ranges. The Ventura core-patch transition checks both required intervals. An unknown core count or unavailable verified reference leaves coverage incomplete.

Custom or newer patch sets need independent review. Matching the reference does not prove that a patch applies to, or executes against, a particular kernel binary.

### Not checked

- EFI files, binary versions and on-disk kext dependency metadata.
- ACPI contents, firmware behavior, USB map correctness and network/audio device IDs.
- Optional AMD PAT alternatives and board/Zen-4-specific IOPCI patches.
- Every CPU/GPU/macOS/SMBIOS combination, actual BIOS state or serial validity with Apple.
- OpenCore releases other than 1.0.8 and macOS later than Sequoia.

The app does not edit configs. Validate each change before using it to boot.

## Privacy and security

Uploads are checked on the server. Parsed data stays in memory; `ocvalidate` reads a normalized copy from a private temporary directory that is deleted when validation finishes. The app does not retain uploads or reports, log request bodies, use analytics or contact Apple. Hosting infrastructure may keep HTTP access logs.

Downloaded JSON includes your hardware labels and validator diagnostics, but not the full config. Review it before sharing.

Uploads are limited to 2 MB, with extra request space for multipart overhead. Native validation times out after 8 seconds. XML entities are rejected, and uploaded contents are never executed. Production hosting should provide TLS and infrastructure-level rate and concurrency limits for public use. The Docker container runs as a non-root user.

## Tests

```sh
python -m pytest -q
```

Tests use the pinned validator and cover parser failures, malformed types, profile validation, dependency ordering, hardware conditions, GPU/OS boundaries and unavailable-validator behavior. GitHub Actions installs the verified upstream asset before running them.

## Sources and attribution

- [OpenCorePkg 1.0.8](https://github.com/acidanthera/OpenCorePkg/releases/tag/1.0.8), [ocvalidate documentation](https://github.com/acidanthera/OpenCorePkg/tree/1.0.8/Utilities/ocvalidate) and [Configuration.pdf](https://github.com/acidanthera/OpenCorePkg/blob/1.0.8/Docs/Configuration.pdf). Downloaded utilities and the sample retain their upstream licenses.
- [Dortania OpenCore Install Guide](https://dortania.github.io/OpenCore-Install-Guide/) and [GPU Buyers Guide](https://dortania.github.io/GPU-Buyers-Guide/). Desktop and AMD guidance was reviewed against Install Guide commit `6680f2df86de1283083b0208e8a8042fa1952b2c`. Read the linked sources for firmware-dependent exceptions.
- [AMD Vanilla patches](https://github.com/AMD-OSX/AMD_Vanilla/tree/eaf52ef292abf4ebec899df6d48626569ba50cc6). Reference SHA-256: `4bc820109b3d020c3c547fa23c49e0098e4f4a2c625ed6184dd54e390e84e1ab`. These are upstream patches, downloaded for data comparison and never executed.

Independent project by [zeyadhost](https://github.com/zeyadhost), not affiliated with Apple, Acidanthera, or Dortania.
