"""Small same-origin API and production static frontend. No upload persistence."""
import copy
import plistlib
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge

from checker import ConfigError, GUID, ROOT, VERSION, analyze, parse_config, profile_from

app = Flask(__name__, static_folder="web", static_url_path="/assets")
app.config.update(MAX_CONTENT_LENGTH=2 * 1024 * 1024 + 16384)


@app.after_request
def headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'"
    return response


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/health")
def health():
    return jsonify(status="ok", opencore=VERSION, validator=(ROOT / "vendor" / "ocvalidate").exists())


@app.post("/api/check")
def check():
    upload = request.files.get("config")
    if upload is None:
        return jsonify(error="Choose a config.plist first."), 400
    try:
        profile = profile_from(request.form)
        config = parse_config(upload.read(2 * 1024 * 1024 + 1))
        return jsonify(analyze(config, profile))
    except ConfigError as error:
        return jsonify(error=str(error)), 400
    except (RecursionError, OverflowError, ValueError):
        return jsonify(error="This plist contains unsupported or excessively nested values."), 400


@app.errorhandler(RequestEntityTooLarge)
def too_large(_error):
    return jsonify(error="Use a config.plist smaller than 2 MB, not a full EFI archive."), 413


def demo_config():
    config = plistlib.loads((ROOT / "vendor" / "Sample.plist").read_bytes())
    def clear_arrays(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, list):
                    obj[key] = []
                else:
                    clear_arrays(value)
    clear_arrays(config)
    config["Kernel"]["Add"] = [
        {"Arch": "Any", "BundlePath": name + ".kext", "Comment": "Demo only", "Enabled": True,
         "ExecutablePath": "Contents/MacOS/" + name, "MaxKernel": "", "MinKernel": "", "PlistPath": "Contents/Info.plist"}
        for name in ("Lilu", "VirtualSMC", "WhateverGreen")
    ]
    config["UEFI"]["Drivers"] = [{"Arguments": "", "Comment": "", "Enabled": True, "HideVerbose": False, "LoadEarly": False, "Path": name} for name in ("OpenRuntime.efi", "HfsPlus.efi")]
    config["ACPI"]["Add"] = [{"Comment": "Demo only", "Enabled": True, "Path": name} for name in ("SSDT-PLUG.aml", "SSDT-EC-USBX.aml")]
    config["Kernel"]["Quirks"]["DisableLinkeditJettison"] = True
    config["Kernel"]["Quirks"]["XhciPortLimit"] = True
    config["Kernel"]["Quirks"]["AppleXcpmCfgLock"] = False
    config["Misc"]["Security"]["Vault"] = "Optional"
    config["Misc"]["Security"]["DmgLoading"] = "Signed"
    config["NVRAM"]["Add"][GUID]["boot-args"] = "-v keepsyms=1 debug=0x100"
    config["NVRAM"]["Add"][GUID]["csr-active-config"] = bytes(4)
    config["PlatformInfo"]["Generic"]["SystemProductName"] = "iMac19,1"
    config["PlatformInfo"]["Generic"]["SystemSerialNumber"] = ""
    return config


@app.get("/api/demo")
def demo():
    return Response(plistlib.dumps(demo_config()), mimetype="application/x-plist",
                    headers={"Content-Disposition": 'attachment; filename="demo-config.plist"'})
