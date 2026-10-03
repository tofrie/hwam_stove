"""Validate the B01-only runtime scope, structure and translations without HA."""

import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "2176600eece1c644f594a9608186bf395bb2488b"
RUNTIME = ROOT / "custom_components/hwam_stove"


def json_file(path):
    return json.loads(path.read_text())


def leaf_keys(value, prefix=()):
    if not isinstance(value, dict):
        return {prefix}
    return set().union(*(leaf_keys(v, (*prefix, k)) for k, v in value.items()))


def validate():
    hashes = json_file(ROOT / "tests/fixtures/runtime_sha256.json")
    paths = {
        str(p.relative_to(ROOT)) for p in RUNTIME.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    prefix = "custom_components/hwam_stove/"
    assert paths == set(hashes) | {prefix + "migration.py"}, "Runtime inventory changed"
    unchanged = 0
    for path, expected in hashes.items():
        current = (ROOT / path).read_bytes()
        baseline = subprocess.check_output(
            ["git", "show", f"{BASELINE}:{path}"], cwd=ROOT
        )
        assert hashlib.sha256(baseline).hexdigest() == expected, path
        if path == prefix + "config_flow.py":
            assert current.count(b"VERSION = 2") == 1
            assert current.replace(b"VERSION = 2", b"VERSION = 1") == baseline
            continue
        if path == prefix + "__init__.py":
            tree = ast.parse(current)
            allowed = [node for node in tree.body if (
                isinstance(node, ast.AsyncFunctionDef)
                and node.name == "async_migrate_entry"
            ) or (
                isinstance(node, ast.ImportFrom) and node.level == 1
                and node.module == "migration"
            )]
            assert len(allowed) == 2
            tree.body = [node for node in tree.body if node not in allowed]
            assert ast.dump(tree) == ast.dump(ast.parse(baseline)), path
            continue
        assert hashlib.sha256(current).hexdigest() == expected, path
        assert current == baseline, f"Runtime differs from baseline: {path}"
        unchanged += 1
    manifest = json_file(RUNTIME / "manifest.json")
    assert manifest == {
        "domain": "hwam_stove", "name": "HWAM Smart Stove", "config_flow": True,
        "documentation": "https://github.com/mvn23/hwam_stove", "dependencies": [],
        "codeowners": [], "requirements": ["pystove==0.3a1"],
        "version": "1.0.0b2", "iot_class": "local_polling",
    }, "Manifest baseline changed (known metadata defects remain out of scope)"
    assert json_file(ROOT / "hacs.json") == {"name": "HWAM"}
    assert sorted(p.name for p in (ROOT / "custom_components").iterdir()
                  if p.is_dir() and p.name != "__pycache__") == ["hwam_stove"]
    translations = {lang: json_file(RUNTIME / f"translations/{lang}.json")
                    for lang in ("de", "en", "nl")}
    assert leaf_keys(translations["de"]) == leaf_keys(translations["en"])
    assert leaf_keys(translations["nl"]) == leaf_keys(translations["en"])
    rows = json_file(ROOT / "tests/fixtures/entities.json")
    actual_keys = []
    for platform in {r["platform"] for r in rows}:
        tree = ast.parse((RUNTIME / f"{platform}.py").read_text())
        for call in ast.walk(tree):
            if isinstance(call, ast.Call):
                for keyword in call.keywords:
                    if keyword.arg == "translation_key":
                        actual_keys.append((platform, ast.literal_eval(keyword.value)))
    assert Counter(actual_keys) == Counter(
        (r["platform"], r["translation_key"]) for r in rows
    )
    assert len(actual_keys) == 40
    for language, strings in translations.items():
        assert set(strings["config"]["step"]["init"]["data"]) == {"name", "host"}
        assert set(strings["config"]["error"]) == {
            "already_configured", "cannot_connect"
        }
        assert "deprecated_import_from_configuration_yaml" in strings["issues"]
        for row in rows:
            text = strings["entity"][row["platform"]][row["translation_key"]]
            assert text["name"], (language, row)
            if row["options"]:
                assert set(text["state"]) == set(row["options"]), (language, row)
    return {"runtime_files_byte_equal": unchanged,
            "b01_changed_files": ["__init__.py", "config_flow.py"],
            "b01_added_files": ["migration.py"], "entities": len(rows),
            "translations": list(translations), "pystove": "0.3a1"}


if __name__ == "__main__":
    print(json.dumps(validate(), indent=2))
