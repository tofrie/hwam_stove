"""Offline artifact, dependency and runtime checks for the isolated A/B gate.

Run as python -m scripts.pystove_gate; no runtime or manifest writes.
"""

import argparse
from collections import Counter
from email.parser import BytesParser
import hashlib
from importlib.metadata import distributions, version
import json
from pathlib import Path
import platform
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = "7d7a9cd58cb434479f8180e5605b22961dfff857"
CANDIDATE = "eec0d60a0120140171a7ef2a5b6c6005da04c51b"
RUNTIME_SHA = "b9a6556d39089803e4ad531599053a166330f5a3343ac229f2790d44c3ffe8ed"
ARTIFACTS = json.loads((ROOT / "tests/fixtures/pystove_artifacts.json").read_text())


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(*args, cwd=ROOT):
    return subprocess.check_output(["git", *args], cwd=cwd)


def verify_wheel(path, scenario):
    expected = ARTIFACTS[scenario]
    assert sha(path.read_bytes()) == expected["wheel_sha256"], "Wrong wheel SHA"
    with zipfile.ZipFile(path) as archive:
        sources = {
            n: sha(archive.read(n))
            for n in archive.namelist()
            if n.startswith("pystove/") and n.endswith(".py")
        }
        assert sources == expected["runtime_sha256"]
        (metadata,) = [n for n in archive.namelist() if n.endswith("/METADATA")]
        parsed = BytesParser().parsebytes(archive.read(metadata))
        assert parsed["Name"] == expected.get("distribution", "pystove")
        assert parsed["Version"] == expected["version"]
        assert parsed.get_all("Requires-Dist") == expected["requires_dist"]
    return expected


def artifact_check(baseline, candidate, source):
    verify_wheel(baseline, "baseline")
    expected = verify_wheel(candidate, "candidate")
    assert git("rev-parse", "HEAD", cwd=source).decode().strip() == CANDIDATE
    with zipfile.ZipFile(candidate) as archive:
        for name in expected["runtime_sha256"]:
            assert archive.read(name) == git("show", f"{CANDIDATE}:{name}", cwd=source)
    return {
        "baseline_sha256": sha(baseline.read_bytes()),
        "candidate_sha256": sha(candidate.read_bytes()),
        "commit": CANDIDATE,
    }



def release_check(previous, release, source):
    """Prove the renamed wheel is the exact reviewed runtime, without importing it."""
    verify_wheel(previous, "candidate")
    expected = verify_wheel(release, "release")
    commit = expected["commit"]
    assert git("rev-parse", "HEAD", cwd=source).decode().strip() == commit
    with zipfile.ZipFile(previous) as old, zipfile.ZipFile(release) as new:
        for name in expected["runtime_sha256"]:
            before, after = old.read(name), new.read(name)
            assert after == git("show", f"{commit}:{name}", cwd=source)
            if name == "pystove/version.py":
                after = after.replace(b'"0.3.0rc1"', b'"0.3a2.dev0"')
            assert before == after, name
    return {
        "source_commit": commit, "wheel_sha256": expected["wheel_sha256"],
        "distribution": expected["distribution"], "version": expected["version"],
        "runtime_protocol_delta": [], "allowed_difference": "version literal",
        "compared_candidate": CANDIDATE,
    }


def candidate_lock(wheel, output, scenario="candidate"):
    expected = verify_wheel(wheel, scenario)
    name = expected.get("distribution", "pystove")
    original = git(
        "show", "27dfc8796df2d5aaa5aef9422d2665f4f2e65cb3:requirements-test.txt"
    ).decode()
    pattern = r"(?m)^pystove==0\.3a1 \\\n(?:[ \t]+[^\n]*\n)*"
    matches = list(re.finditer(pattern, original))
    assert len(matches) == 1
    replacement = (
        f"{name} @ {wheel.resolve().as_uri()} \\\n"
        f"    --hash=sha256:{expected['wheel_sha256']}\n"
    )
    (match,) = matches
    output.write_text(original[: match.start()] + replacement + original[match.end() :])
    return {"output": str(output), "only_replaced_requirement": "pystove"}


def environment(scenario):
    from tests.dependency_contract import verify_installed

    artifact = verify_installed(scenario)
    entries = [
        (re.sub(r"[-_.]+", "-", d.metadata["Name"]).lower(), d.version)
        for d in distributions()
    ]
    counts = Counter(n for n, _ in entries)
    assert all(count == 1 for count in counts.values()), counts
    ha = version("homeassistant")
    framework = {"2026.9.4": "0.13.367", "2026.10.0b0": "0.13.368"}[ha]
    expected = {
        "homeassistant": ha,
        "aiohttp": "3.14.3",
        "pytest": "9.0.3",
        "pytest-homeassistant-custom-component": framework,
    }
    assert platform.python_version() == "3.14.6"
    for name, value in expected.items():
        assert version(name) == value
    return {
        "python": platform.python_version(),
        "scenario": scenario,
        "artifact_sha256": artifact["wheel_sha256"],
        "pystove_requires_dist": artifact["requires_dist"],
        "distributions": dict(sorted(entries)),
        "duplicate_distributions": [],
    }


def compare_environments(a, b):
    left, right = (json.loads(path.read_text()) for path in (a, b))
    assert (left["scenario"], right["scenario"]) in {
        ("baseline", "candidate"), ("candidate", "release"), ("published", "rc2"),
    }
    assert left["python"] == right["python"] == "3.14.6"
    assert not left["duplicate_distributions"] and not right["duplicate_distributions"]
    differences = {
        name: [left["distributions"].get(name), right["distributions"].get(name)]
        for name in left["distributions"].keys() | right["distributions"].keys()
        if left["distributions"].get(name) != right["distributions"].get(name)
    }
    expected = (
        {"pystove": ["0.3a1", "0.3a2.dev0"]}
        if right["scenario"] == "candidate" else {
            "pystove": ["0.3a2.dev0", None],
            "saynwerk-pystove": [None, "0.3.0rc1"],
        }
    )
    if right["scenario"] == "rc2":
        expected = {"saynwerk-pystove": ["0.3.0rc1", "0.3.0rc2"]}
    assert differences == expected, differences
    return {
        "differences": differences,
        "distribution_count": len(left["distributions"]),
    }


def runtime_integrity():
    from scripts.check_integrity import validate_dependency_migration

    dependency = validate_dependency_migration()
    prefix = "custom_components/hwam_stove"
    names = (
        git("ls-tree", "-r", "--name-only", BASE, "--", prefix).decode().splitlines()
    )
    current = {
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / prefix).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    assert current == set(names), "Runtime inventory changed"
    before, after = bytearray(), bytearray()
    for name in sorted(names):
        old = git("show", f"{BASE}:{name}")
        new = (ROOT / name).read_bytes()
        if name == prefix + "/manifest.json":
            new = new.replace(
                b'"saynwerk-pystove==0.3.0rc1"', b'"pystove==0.3a1"'
            )
        assert old == new, name
        before.extend(name.encode() + b"\0" + old + b"\0")
        after.extend(name.encode() + b"\0" + new + b"\0")
    assert sha(before) == sha(after) == RUNTIME_SHA
    manifest = json.loads((ROOT / prefix / "manifest.json").read_text())
    assert manifest["requirements"] == ["saynwerk-pystove==0.3.0rc1"]
    return {
        "base": BASE,
        "files": len(names),
        "changed_files": dependency["changed_files"],
        "before": sha(before),
        "after_normalizing_approved_dependency_change": sha(after),
        "manifest_version": manifest["version"],
        "requirements": manifest["requirements"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    artifacts = commands.add_parser("artifacts")
    artifacts.add_argument("--baseline", type=Path, required=True)
    artifacts.add_argument("--candidate", type=Path, required=True)
    artifacts.add_argument("--source", type=Path, required=True)
    release = commands.add_parser("release-artifacts")
    release.add_argument("--previous", type=Path, required=True)
    release.add_argument("--release", type=Path, required=True)
    release.add_argument("--source", type=Path, required=True)
    lock = commands.add_parser("candidate-lock")
    lock.add_argument("--wheel", type=Path, required=True)
    lock.add_argument("--output", type=Path, required=True)
    lock.add_argument(
        "--scenario", choices=("candidate", "release"), default="candidate"
    )
    env = commands.add_parser("environment")
    env.add_argument(
        "scenario", choices=("baseline", "candidate", "release", "published", "rc2")
    )
    compare = commands.add_parser("compare")
    compare.add_argument("a", type=Path)
    compare.add_argument("b", type=Path)
    commands.add_parser("runtime")
    args = parser.parse_args()
    if args.command == "artifacts":
        result = artifact_check(args.baseline, args.candidate, args.source)
    elif args.command == "release-artifacts":
        result = release_check(args.previous, args.release, args.source)
    elif args.command == "candidate-lock":
        result = candidate_lock(args.wheel, args.output, args.scenario)
    elif args.command == "environment":
        result = environment(args.scenario)
    elif args.command == "compare":
        result = compare_environments(args.a, args.b)
    else:
        result = runtime_integrity()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
