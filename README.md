# plistcheck

plistcheck runs OpenCore's validator and hardware-specific checks on your `config.plist`, entirely in your browser. Choose an XML or binary plist, select your hardware and target macOS, and review source-linked findings. Your config never leaves your device.

A valid plist can still fail to boot. Keep a known-good EFI backup before making changes. Coverage gaps appear in the interface and exported JSON report.

## Run locally

The public site is a static GitHub Pages build. To build it locally, install Python 3.10+, Git, Make and [Emscripten 3.1.74](https://emscripten.org/docs/getting_started/downloads.html), then:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python setup_opencore.py
# Activate the pinned Emscripten SDK in this shell first.
source /path/to/emsdk/emsdk_env.sh
python build_static.py
python -m http.server 8000 --directory dist
```

Open `http://localhost:8000`. A static HTTP server is required; opening `index.html` as a local file does not support workers and module loading. "Try an example" loads a synthetic Coffee Lake/Z390 config that passes official validation and has hardware-guidance issues. It uses the matching profile unless you edited the form. Do not boot the demo.

The build downloads checksum-verified Pyodide 0.27.7 and its required packages, bundles the existing Python parser/rules and pinned AMD reference, and compiles the unchanged OpenCore 1.0.8 validator to WebAssembly. OpenCore and EDK II source commits and the compiler version are pinned in `build_ocvalidate.py`. Generated files stay in ignored `vendor/` and `dist/`; binaries are not committed.

Everything needed at runtime is served from the same site, including the roughly 16 MB local runtime download. There are no runtime CDN, Flask, Node, database or credential requirements. The first check downloads and starts the runtime; later checks can use the browser's normal HTTP cache. No service worker or offline-install guarantee is provided.

The Python API and Dockerfile remain as a reference backend for native regression tests, not the public hosting path. `setup_opencore.py` installs a native validator on x86-64 Linux, macOS or Windows, as well as the sample/reference files used by the static build.

### GitHub Pages

Select **Settings → Pages → Source → GitHub Actions** once. The **Browser and Pages** workflow builds and compares the browser implementation with native baselines on pull requests, then publishes `dist/` after a push to `main`. The site uses relative asset, worker and manifest paths so it works at [`zeyad.is-a.dev/plistcheck/`](https://zeyad.is-a.dev/plistcheck/) or another project subpath. There is no `CNAME` or separate subdomain requirement.

## Validation layers

1. The parser accepts XML and binary plists. It rejects duplicate XML keys, entities and external references, non-dictionary roots, and files that exceed size, nesting or object limits.
2. Official OpenCore 1.0.8 `ocvalidate`, compiled from unchanged sources to WebAssembly, checks the schema, values, patch consistency, kext ordering and relationships between settings. Binary files are converted to XML locally. The report includes raw diagnostics; a grouped finding can contain several validator issues.
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

The public static site processes files in a disposable browser worker. Python runs locally through Pyodide; `ocvalidate` runs locally through WebAssembly. Input bytes, normalized XML and hardware labels are not sent to a server. Temporary worker files are removed and the worker terminates after each check. The site does not persist configs or reports, use analytics, contact Apple, or fall back to a server-side checker. The static host may log ordinary requests for site assets, but does not receive the config.

Downloaded JSON includes your hardware labels and validator diagnostics, but not the full config. Review it before sharing.

Files are limited to 2 MB. Local validation is limited to 8 seconds after runtime initialization, which has a separate two-minute download/startup limit. XML entities are rejected and config contents are never executed. A runtime load failure or timeout cannot produce a successful report. Current browsers with WebAssembly and module workers are required.

## Tests

```sh
python -m pytest -q
# After the static build, compare the actual Pyodide and WASM output with native baselines:
python tests/browser-fixtures.py
node --test tests/browser.test.mjs
```

Tests use the pinned validator and cover parser failures, malformed types, profile validation, dependency ordering, hardware conditions, GPU/OS boundaries and unavailable-validator behavior. Browser-runtime comparisons cover XML/binary parsing, AMD core counts and references, clean/broken laptops, unknown hardware, official schema errors and rejected files. Client tests verify local worker transfer and relative-path packaging. Node is only a development test dependency.

## Sources and attribution

- [OpenCorePkg 1.0.8](https://github.com/acidanthera/OpenCorePkg/releases/tag/1.0.8), [ocvalidate documentation](https://github.com/acidanthera/OpenCorePkg/tree/1.0.8/Utilities/ocvalidate) and [Configuration.pdf](https://github.com/acidanthera/OpenCorePkg/blob/1.0.8/Docs/Configuration.pdf). Downloaded utilities and the sample retain their upstream licenses.
- [Dortania OpenCore Install Guide](https://dortania.github.io/OpenCore-Install-Guide/) and [GPU Buyers Guide](https://dortania.github.io/GPU-Buyers-Guide/). Desktop and AMD guidance was reviewed against Install Guide commit `6680f2df86de1283083b0208e8a8042fa1952b2c`. Read the linked sources for firmware-dependent exceptions.
- [AMD Vanilla patches](https://github.com/AMD-OSX/AMD_Vanilla/tree/eaf52ef292abf4ebec899df6d48626569ba50cc6). Reference SHA-256: `4bc820109b3d020c3c547fa23c49e0098e4f4a2c625ed6184dd54e390e84e1ab`. These are upstream patches, downloaded for data comparison and never executed.

Independent project by [zeyadhost](https://github.com/zeyadhost), not affiliated with Apple, Acidanthera, or Dortania.
