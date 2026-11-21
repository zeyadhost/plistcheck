"""Build the official, unchanged ocvalidate sources for WebAssembly with Emscripten 3.1.74."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
OPENCORE = "6fe4d15cb66596d99d8cfedce69d8fc6dad71886"
AUDK = "dfd5516702b4911f1fc1919a429d637b456df913"


def source(name, url, revision, supplied):
    path = Path(supplied).resolve() if supplied else ROOT / "vendor" / "sources" / name
    if not path.exists():
        path.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", url], check=True)
        subprocess.run(["git", "-C", str(path), "fetch", "--depth", "1", "origin", revision], check=True)
        subprocess.run(["git", "-C", str(path), "checkout", "--detach", "FETCH_HEAD"], check=True)
    actual = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    if actual != revision or subprocess.check_output(["git", "-C", str(path), "diff", "HEAD"], text=True):
        raise SystemExit(f"Expected clean pinned {name} source {revision}; refusing {path}")
    return path


def build(opencore=None, udk=None):
    if not shutil.which("emcc"):
        raise SystemExit("Activate Emscripten 3.1.74 (source .emsdk/emsdk_env.sh) before building.")
    if "3.1.74" not in subprocess.check_output(["emcc", "--version"], text=True):
        raise SystemExit("This build is pinned to Emscripten 3.1.74.")
    oc = source("OpenCorePkg", "https://github.com/acidanthera/OpenCorePkg.git", OPENCORE, opencore)
    edk = source("audk", "https://github.com/acidanthera/audk.git", AUDK, udk)
    output = ROOT / "vendor" / "ocvalidate.mjs"
    flags = " ".join([
        "-O2", "-sMODULARIZE=1", "-sEXPORT_ES6=1", "-sEXPORT_NAME=createOcvalidate",
        "-sINVOKE_RUN=0", "-sEXIT_RUNTIME=0", "-sALLOW_MEMORY_GROWTH=1",
        "-sMAXIMUM_MEMORY=268435456", "-sSTACK_SIZE=1048576", "-sDYNAMIC_EXECUTION=0",
        "-sEXPORTED_RUNTIME_METHODS=FS,callMain", "-sENVIRONMENT=web,worker,node", "-sASSERTIONS=0",
    ])
    # EDK II's Ia32 binding has the same 32-bit UINTN/pointer width as wasm32.
    # The upstream userspace shims already have non-x86 fallbacks; no validation code is patched.
    with (ROOT / "vendor" / "ocvalidate-build.log").open("w") as log:
        result = subprocess.run([
            "make", f"-j{min(os.cpu_count() or 2, 8)}", "UDK_ARCH=Ia32", f"UDK_PATH={edk}",
            "CC=emcc", "STRIP=true", f"PRODUCT={output}", f"LDFLAGS={flags}",
        ], cwd=oc / "Utilities" / "ocvalidate", stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise SystemExit((ROOT / "vendor" / "ocvalidate-build.log").read_text()[-12000:])
    print("Built official ocvalidate 1.0.8 WASM:", hashlib.sha256(output.with_suffix(".wasm").read_bytes()).hexdigest())
    return oc, edk


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opencore-source")
    parser.add_argument("--udk-source")
    args = parser.parse_args()
    build(args.opencore_source, args.udk_source)
