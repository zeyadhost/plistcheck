"""Produce a self-contained static site. Downloads happen only at build time."""
import argparse
import base64
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import shutil
import tarfile
import urllib.request
import zipfile

import amd
import build_ocvalidate

ROOT = Path(__file__).resolve().parent
PYODIDE_VERSION = "0.27.7"
PYODIDE_INTEGRITY = "RUSVJlhQdfWfgO9hVHCiXoG+nVZQRS5D9FzgpLJ/VcgGBLSAKoPL8kTiOikxbHQm1kRISeWUBdulEgO26qpSRA=="
CDN = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/"


def download(url, cache, digest, algorithm="sha256"):
    if not cache.exists():
        with urllib.request.urlopen(url, timeout=120) as response:
            data = response.read(100 * 1024 * 1024)
        cache.write_bytes(data)
    data = cache.read_bytes()
    if hashlib.new(algorithm, data).digest() != digest:
        raise SystemExit(f"Checksum mismatch for {url}; delete {cache} and retry.")
    return data


def build(opencore=None, udk=None):
    oc, edk = build_ocvalidate.build(opencore, udk)
    cache = ROOT / "vendor" / "downloads"
    cache.mkdir(parents=True, exist_ok=True)
    package = download(f"https://registry.npmjs.org/pyodide/-/pyodide-{PYODIDE_VERSION}.tgz",
                       cache / "pyodide.tgz", base64.b64decode(PYODIDE_INTEGRITY), "sha512")
    dist = ROOT / "dist"
    if dist.exists():
        shutil.rmtree(dist)
    assets = dist / "assets"
    shutil.copytree(ROOT / "web", assets, ignore=shutil.ignore_patterns("index.html"))
    shutil.copy2(ROOT / "web" / "index.html", dist / "index.html")
    (dist / ".nojekyll").touch()
    runtime = assets / "runtime"
    py = runtime / "pyodide"
    py.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(package), mode="r:gz") as archive:
        for name in ("pyodide.mjs", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"):
            (py / name).write_bytes(archive.extractfile(f"package/{name}").read())
    lock = json.loads((py / "pyodide-lock.json").read_text())
    packages = set()

    def dependency(name):
        if name in packages:
            return
        entry = lock["packages"][name]
        packages.add(name)
        for child in entry["depends"]:
            dependency(child)
        filename = entry["file_name"]
        data = download(CDN + filename, cache / filename, bytes.fromhex(entry["sha256"]))
        (py / filename).write_bytes(data)

    dependency("hashlib")
    for name in ("ocvalidate.mjs", "ocvalidate.wasm"):
        shutil.copy2(ROOT / "vendor" / name, runtime / name)
    patches = (ROOT / "vendor" / "AMD-patches.plist").read_bytes()
    if hashlib.sha256(patches).hexdigest() != amd.SHA256:
        raise SystemExit("AMD reference checksum mismatch. Run python setup_opencore.py.")
    # Reuse the exact parser, hardware and AMD rules instead of maintaining a JS rewrite.
    defused = importlib.metadata.distribution("defusedxml")
    if defused.version != "0.7.1":
        raise SystemExit("Install pinned requirements before building.")
    with zipfile.ZipFile(runtime / "rules.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("checker.py", "amd.py", "browser_bridge.py"):
            archive.writestr(name, (ROOT / name).read_bytes())
        archive.writestr("vendor/AMD-patches.plist", patches)
        for entry in defused.files:
            if str(entry).startswith("defusedxml/") and str(entry).endswith(".py"):
                archive.writestr(str(entry), Path(defused.locate_file(entry)).read_bytes())
    from app import demo_config
    import plistlib
    (runtime / "example.plist").write_bytes(plistlib.dumps(demo_config()))
    licenses = runtime / "licenses"
    licenses.mkdir()
    shutil.copy2(oc / "LICENSE.txt", licenses / "OpenCore.txt")
    shutil.copy2(edk / "License.txt", licenses / "EDK-II.txt")
    for name, url, checksum in [
        ("Pyodide", "https://raw.githubusercontent.com/pyodide/pyodide/0.27.7/LICENSE", "1f256ecad192880510e84ad60474eab7589218784b9a50bc7ceee34c2b91f1d5"),
        ("Python", "https://raw.githubusercontent.com/python/cpython/v3.12.7/LICENSE", "3b2f81fe21d181c499c59a256c8e1968455d6689d269aa85373bfb6af41da3bf"),
        ("OpenSSL", "https://raw.githubusercontent.com/openssl/openssl/OpenSSL_1_1_1w/LICENSE", "c32913b33252e71190af2066f08115c69bc9fddadf3bf29296e20c835389841c"),
    ]:
        (licenses / f"{name}.txt").write_bytes(download(url, cache / f"{name}-LICENSE.txt", bytes.fromhex(checksum)))
    for entry in defused.files:
        if "LICENSE" in str(entry):
            shutil.copy2(defused.locate_file(entry), licenses / "defusedxml.txt")
    (licenses / "sources.txt").write_text(
        f"OpenCore 1.0.8: https://github.com/acidanthera/OpenCorePkg/tree/{build_ocvalidate.OPENCORE}\n"
        f"EDK II: https://github.com/acidanthera/audk/tree/{build_ocvalidate.AUDK}\n"
        f"Pyodide {PYODIDE_VERSION} (MPL-2.0): https://github.com/pyodide/pyodide/tree/{PYODIDE_VERSION}\n"
        "Python (PSF): https://docs.python.org/3.12/license.html\n"
        "OpenSSL (OpenSSL/SSLeay): https://github.com/openssl/openssl/blob/OpenSSL_1_1_1w/LICENSE\n"
        f"AMD reference: {amd.SOURCE}\n"
    )
    # Record every deployed byte for deployment/debugging; no input or report is bundled.
    manifest = {str(path.relative_to(dist)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(dist.rglob("*")) if path.is_file()}
    (dist / "build-manifest.json").write_text(json.dumps({"opencore": "1.0.8", "pyodide": PYODIDE_VERSION, "files": manifest}, indent=2))
    print(f"Static site: {dist} ({sum(p.stat().st_size for p in dist.rglob('*') if p.is_file()) / 1024**2:.1f} MB). No server or runtime CDN required.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opencore-source")
    parser.add_argument("--udk-source")
    args = parser.parse_args()
    build(args.opencore_source, args.udk_source)
