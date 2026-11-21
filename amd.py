"""Compare AMD patch data with a pinned upstream reference; never execute patches."""
import hashlib
import plistlib
import re
from functools import lru_cache
from pathlib import Path

COMMIT = "eaf52ef292abf4ebec899df6d48626569ba50cc6"
SHA256 = "4bc820109b3d020c3c547fa23c49e0098e4f4a2c625ed6184dd54e390e84e1ab"
SOURCE = f"https://github.com/AMD-OSX/AMD_Vanilla/tree/{COMMIT}"
URL = f"https://raw.githubusercontent.com/AMD-OSX/AMD_Vanilla/{COMMIT}/patches.plist"
REFERENCE = Path(__file__).resolve().parent / "vendor" / "AMD-patches.plist"
FIELDS = ("Base", "Count", "Find", "Identifier", "Limit", "Mask", "ReplaceMask", "Skip")


@lru_cache(maxsize=1)
def reference_patches():
    data = REFERENCE.read_bytes()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError("AMD reference checksum mismatch")
    return plistlib.loads(data)["Kernel"]["Patch"]


def bounds(patch):
    result = []
    for key, default in (("MinKernel", (0, 0, 0)), ("MaxKernel", (999, 999, 999))):
        value = patch.get(key, "")
        if not isinstance(value, str) or (value and not re.fullmatch(r"\d+(?:\.\d+){0,2}", value)):
            return None
        parts = tuple(map(int, value.split("."))) if value else default
        result.append(parts + (0,) * (3 - len(parts)))
    return tuple(result) if result[0] <= result[1] else None


def overlap(a, b):
    return a is not None and b is not None and a[0] <= b[1] and b[0] <= a[1]


def same_patch(candidate, reference, core):
    # Comments are editable labels, not provenance. Compare the actual operation.
    if any(type(candidate.get(k)) is not type(reference.get(k)) or candidate.get(k) != reference.get(k) for k in FIELDS):
        return False
    actual, expected = candidate.get("Replace"), reference["Replace"]
    if not isinstance(actual, bytes) or len(actual) != len(expected):
        return False
    return actual[:1] + b"\0" + actual[2:] == expected if core else actual == expected


def covered(intervals, target):
    # Discrete Darwin version tuples, including point-release patch transitions.
    cursor = target[0]
    for low, high in sorted(intervals):
        if high < cursor:
            continue
        if low > cursor:
            return False
        if high >= target[1]:
            return True
        cursor = (high[0], high[1] + 1, 0) if high[2] == 99 else (high[0], high[1], high[2] + 1)
    return False


def inspect_patches(patches, target, cores):
    """Return missing/modified reference operations and active core/range errors.

    Optional PAT alternatives and board/Zen-4-specific IOPCI patches aren't mandatory.
    Their correctness, custom patch sets and future upstream revisions remain gaps.
    """
    reference = [p for p in reference_patches() if p["Enabled"] and "PAT" not in p["Comment"] and p["Identifier"] == "kernel"]
    active = [p for p in patches if isinstance(p, dict) and p.get("Enabled") is True
              and p.get("Arch") in ("Any", "x86_64") and overlap(bounds(p), target)] if isinstance(patches, list) else []
    missing, core_errors, range_errors = [], [], []
    recognized = 0
    # Check every active recognized operation, even a historical one that should
    # not be enabled anywhere in this target. Duplicate operations may have a
    # union of valid reference intervals.
    for patch in active:
        matches = [p for p in reference if same_patch(patch, p, "cpuid_cores_per_package" in p["Comment"])]
        low, high = bounds(patch)
        active_bounds = (max(low, target[0]), min(high, target[1]))
        if matches and not covered([bounds(p) for p in matches], active_bounds):
            range_errors.append(matches[0]["Comment"])
    for expected in reference:
        expected_bounds = bounds(expected)
        if not overlap(expected_bounds, target):
            continue
        core = "cpuid_cores_per_package" in expected["Comment"]
        matches = [p for p in active if same_patch(p, expected, core)]
        required = (max(target[0], expected_bounds[0]), min(target[1], expected_bounds[1]))
        intervals = []
        for patch in matches:
            low, high = bounds(patch)
            if not overlap((low, high), required):
                continue
            recognized += 1
            intervals.append((max(low, required[0]), min(high, required[1])))
            if core:
                count = patch["Replace"][1]
                if count == 0 or (cores is not None and count != cores):
                    core_errors.append(f"{expected['Comment']}: Replace encodes {count} physical cores"
                                       + (f"; expected {cores}" if cores is not None else "; 00 is an unedited placeholder"))
        if not covered(intervals, required):
            missing.append(expected["Comment"])
    return {"recognized": recognized, "missing": missing, "core_errors": core_errors, "range_errors": range_errors}
