# plistcheck

A second pair of eyes for your OpenCore config. Upload an XML or binary `config.plist`, select your desktop hardware and target macOS, and get actionable findings with links to their sources.

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

Open `http://localhost:8000`. “Try an example” loads a deliberately imperfect, synthetic Coffee Lake/Z390 config and runs the checker without needing a real EFI. Never boot the demo.

On Windows, activate `.venv\Scripts\activate` and use `flask --app app run --port 8000` for local development instead of Gunicorn. The production Docker image targets `linux/amd64`:

```sh
docker build --platform linux/amd64 -t plistcheck .
docker run --rm -p 8000:8000 plistcheck
```

No frontend build, Node runtime, database, or credentials required. The frontend is plain HTML, CSS and JavaScript. There are no existing pre-commit hooks.

## Validation layers

1. **Safe parsing:** XML and binary property lists, XML duplicate-key detection, entity/external-reference rejection, dictionary root validation, size/nesting/object limits.
2. **Official validation:** the unmodified `ocvalidate` executable from OpenCore **1.0.8**, covering its schema, value constraints, patch consistency, kext ordering, and cross-setting semantics. Binary uploads are normalized to XML before running the tool. Raw diagnostics are available in the result; one grouped finding may represent several native issues.
3. **Hardware-aware rules:** source-linked checks for Intel Haswell through Comet Lake desktops and AMD Zen, selected 300-series/B550/A520 chipset ACPI hints, reported BIOS state, GPU-family support boundaries, macOS-dependent USB settings, SMC/Lilu dependencies, SMBIOS placeholders, and security/debug settings.

Only OpenCore 1.0.8 is supported. The config format cannot reliably reveal the installed bootloader version: the user must confirm that it matches. `setup_opencore.py` downloads the official release archive, verifies its pinned SHA-256, and installs the validator and sample into ignored `vendor/`. No validator binary is committed. A missing or timed-out validator produces **incomplete coverage**, never a schema pass.

### What the profile means

Selected CPU/GPU families, chipset, macOS, and BIOS options drive rules. Exact model names are optional labels, **not automatically decoded hardware**. Unknown BIOS states skip conditional checks. “AMD supported family” and “Intel pre-Xe” do not certify a specific GPU: device IDs, spoofing, macOS-specific support, and output wiring still need review. Intel 11th-gen or newer, legacy Intel, laptops, and unknown CPUs do not have generation-specific coverage.

SSDT and USB-map hints are filename heuristics and are warnings, not proof that a table or map is missing. Custom names and firmware-native alternatives can be valid. BIOS values are self-reported, not detected. A deliberately configured DMAR table can be an exception to the VT-d recommendation.

### Not checked

- The actual EFI directory: file existence, binary versions, on-disk kext dependency metadata.
- ACPI contents, firmware implementation, USB map correctness, network/audio device IDs.
- AMD Vanilla patch contents, core-count replacements, and per-kernel applicability.
- Exhaustive CPU/GPU/macOS/SMBIOS compatibility, BIOS state, serial validity with Apple, or boot success.
- OpenCore releases other than 1.0.8, macOS later than Sequoia, and laptop-specific setup.

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
- [AMD Vanilla patches](https://github.com/AMD-OSX/AMD_Vanilla).

Independent project by [zeyadhost](https://github.com/zeyadhost), not affiliated with Apple, Acidanthera, or Dortania.
