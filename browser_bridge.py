"""Browser-only adapter. All files below live in the worker's in-memory filesystem."""
import json
import plistlib
from pathlib import Path

import checker

_prepared = None


def prepare():
    global _prepared
    profile = checker.profile_from(json.loads(Path("/profile.json").read_text()))
    config = checker.parse_config(Path("/input.plist").read_bytes())
    Path("/normalized.plist").write_bytes(plistlib.dumps(config, sort_keys=False))
    _prepared = (config, profile)


def finish(native_json):
    global _prepared
    try:
        config, profile = _prepared
        return json.dumps(checker.analyze(config, profile, native=json.loads(native_json)))
    finally:
        _prepared = None
        for name in ("/profile.json", "/input.plist", "/normalized.plist"):
            Path(name).unlink(missing_ok=True)
