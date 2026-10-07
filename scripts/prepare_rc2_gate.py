"""Build the pinned unpublished rc2 locally; never upload or access hardware.

CI provides an exact source checkout and pinned build tools. Runtime dependency
installation uses the resulting SHA-verified wheel via --find-links, not Git.
"""

import argparse
from importlib.metadata import version
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import zipfile

from pystove_gate import ARTIFACTS, git, sha, verify_wheel


def prepare(source, destination):
    source, destination = source.resolve(), destination.resolve()
    expected = ARTIFACTS["rc2"]
    assert git("rev-parse", "HEAD", cwd=source).decode().strip() == expected["commit"]
    assert not git("status", "--porcelain", cwd=source), "Dirty candidate checkout"
    assert not destination.is_relative_to(source)
    assert not destination.exists() or not any(destination.iterdir())
    for name, pin in {
        "build": "1.6.1",
        "setuptools": "84.0.0",
        "wheel": "0.48.0",
    }.items():
        assert version(name) == pin
    env = {**os.environ, "SOURCE_DATE_EPOCH": "1791058066"}
    subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(destination)],
        cwd=source,
        env=env,
        check=True,
    )
    (wheel,) = destination.glob("*.whl")
    verify_wheel(wheel, "rc2")
    with zipfile.ZipFile(wheel) as archive:
        for name in expected["runtime_sha256"]:
            assert archive.read(name) == git("show", f"HEAD:{name}", cwd=source)
    # Reuse only the deterministic archive normalizer, never publish/reproduce.
    spec = importlib.util.spec_from_file_location(
        "normalizer", source / "ci/pypi_release.py"
    )
    normalizer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(normalizer)
    (sdist,) = destination.glob("*.tar.gz")
    normalizer.normalize_sdist(sdist)
    assert sha(sdist.read_bytes()) == expected["sdist_sha256"]
    subprocess.run(
        [sys.executable, "ci/validate_package.py", "--dist-dir", str(destination)],
        cwd=source,
        check=True,
    )
    print(f"Verified unpublished rc2 artifacts: {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.source, args.dist)
