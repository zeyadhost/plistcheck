"""Install the pinned upstream validator, never an unverified executable."""
import hashlib
import io
import platform
import urllib.request
import zipfile
from pathlib import Path

import amd

VERSION = "1.0.8"
SHA256 = "2011e8b7216ecb2645d97ea710df965947edd406846e92050b9bc4190be6d27b"
URL = f"https://github.com/acidanthera/OpenCorePkg/releases/download/{VERSION}/OpenCore-{VERSION}-RELEASE.zip"
ROOT = Path(__file__).resolve().parent


def install():
    if platform.machine().lower() not in ("x86_64", "amd64"):
        raise SystemExit("The upstream validator requires an x86-64 host.")
    with urllib.request.urlopen(URL, timeout=60) as response:
        archive = response.read(50 * 1024 * 1024)
    if hashlib.sha256(archive).hexdigest() != SHA256:
        raise SystemExit("OpenCore archive checksum mismatch; installation stopped.")
    suffix = {"Linux": ".linux", "Darwin": "", "Windows": ".exe"}[platform.system()]
    destination = ROOT / "vendor"
    destination.mkdir(exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        for upstream, local in [
            (f"Utilities/ocvalidate/ocvalidate{suffix}", "ocvalidate.exe" if suffix == ".exe" else "ocvalidate"),
            ("Docs/Sample.plist", "Sample.plist"),
            ("Utilities/ocvalidate/README.md", "ocvalidate-README.md"),
        ]:
            target = destination / local
            target.write_bytes(source.read(upstream))
            if local.startswith("ocvalidate") and not local.endswith(".md"):
                target.chmod(0o755)
    print(f"Installed verified OpenCore {VERSION} validator and sample in {destination}")
    with urllib.request.urlopen(amd.URL, timeout=60) as response:
        patches = response.read(2 * 1024 * 1024)
    if hashlib.sha256(patches).hexdigest() != amd.SHA256:
        raise SystemExit("AMD reference checksum mismatch; installation stopped.")
    (destination / "AMD-patches.plist").write_bytes(patches)
    print(f"Installed verified AMD Vanilla reference {amd.COMMIT}")


if __name__ == "__main__":
    install()
