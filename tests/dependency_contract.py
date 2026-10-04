"""Test-only artifact selection; the runtime manifest is never changed."""

import hashlib
from importlib.metadata import distribution, distributions, packages_distributions
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

import pystove

ARTIFACTS = json.loads(
    (Path(__file__).parent / "fixtures/pystove_artifacts.json").read_text()
)


def verify_installed(scenario):
    expected = ARTIFACTS[scenario]
    name = expected.get("distribution", "pystove")
    dist = distribution(name)
    assert dist.version == expected["version"]
    assert dist.requires == expected["requires_dist"]
    providers = [
        re.sub(r"[-_.]+", "-", d.metadata["Name"]).lower()
        for d in distributions()
        if re.sub(r"[-_.]+", "-", d.metadata["Name"]).lower()
        in {"pystove", "saynwerk-pystove"}
    ]
    assert providers == [name], "Shared pystove namespace must have one owner"
    assert packages_distributions()["pystove"] == [name]
    assert (
        Path(pystove.__file__).resolve()
        == Path(dist.locate_file("pystove/__init__.py")).resolve()
    )
    installed_sources = {
        str(f)
        for f in dist.files
        if str(f).startswith("pystove/") and str(f).endswith(".py")
    }
    assert installed_sources == set(expected["runtime_sha256"])
    for name, digest in expected["runtime_sha256"].items():
        assert (
            hashlib.sha256(Path(dist.locate_file(name)).read_bytes()).hexdigest()
            == digest
        )
    direct = dist.read_text("direct_url.json")
    if scenario in {"candidate", "release"}:
        assert direct is not None
    if direct is not None:
        info = json.loads(direct)
        assert "dir_info" not in info and "vcs_info" not in info
        url = urlsplit(info["url"])
        assert url.scheme == "file" and not url.netloc
        wheel = Path(unquote(url.path))
        assert (
            hashlib.sha256(wheel.read_bytes()).hexdigest() == expected["wheel_sha256"]
        )
        # uv may omit the optional PEP 610 archive hash. Verify the wheel itself.
        recorded = info["archive_info"].get("hashes", {}).get("sha256")
        assert recorded is None or recorded == expected["wheel_sha256"]
    return expected
