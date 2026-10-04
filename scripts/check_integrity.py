"""Validate approved runtime scopes, structure and translations without HA."""

import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "2176600eece1c644f594a9608186bf395bb2488b"
H01A_BASELINE = "f98d5b46530550c76c0390f9bef15280d5bdd8f3"
H02_BASELINE = "6721b8b51a572067686dd55b3da55c47e2f028c0"
H03_BASELINE = "8550621c1801dc7cbf2e933834397b72ef198baf"
H04_BASELINE = "6d708b69eabe450e78a58550e0685655ef42eec6"
H04_PLATFORMS = {"number.py", "switch.py", "button.py", "time.py", "datetime.py"}
H04_TRANSLATIONS = {f"translations/{lang}.json" for lang in ("de", "en", "nl")}
H05_BASELINE = "a6182dd5ab6269ab807288cf05eccf017880e9b2"
H05_CHANGED = {"time.py", "coordinator.py"} | H04_TRANSLATIONS
M03_BASELINE = "cd78643e6962c1af58037198448f82f0c7822cd4"
M08_BASELINE = "c2e7db4f41cf5039f36e393b8226027f672caaf1"
M07_BASELINE = "7ab07db3da3c2cf7e9783a034adeb2deb0eb7f1e"
DEPENDENCY_BASE = "27dfc8796df2d5aaa5aef9422d2665f4f2e65cb3"
RUNTIME = ROOT / "custom_components/hwam_stove"


def json_file(path):
    return json.loads(path.read_text())


def leaf_keys(value, prefix=()):
    if not isinstance(value, dict):
        return {prefix}
    return set().union(*(leaf_keys(v, (*prefix, k)) for k, v in value.items()))


def validate_h03_flow(current, baseline):
    """Permit only connection-test cleanup, its helper and exact logging imports."""
    tree, base_tree = ast.parse(current), ast.parse(baseline)
    allowed = {ast.dump(n) for n in ast.parse(
        "from asyncio import CancelledError, create_task, shield\n"
        "import logging\n_LOGGER = logging.getLogger(__name__)\n"
    ).body}
    additions = [n for n in tree.body if ast.dump(n) in allowed]
    assert len(additions) == 3
    tree.body = [n for n in tree.body if n not in additions]
    flow, = [n for n in tree.body if isinstance(n, ast.ClassDef)
             and n.name == "HWAMStoveConfigFlow"]
    cleanup, = [n for n in flow.body if isinstance(n, ast.AsyncFunctionDef)
                and n.name == "_async_destroy_stove"]
    flow.body.remove(cleanup)
    old_test, = [n for n in ast.walk(base_tree) if isinstance(n, ast.AsyncFunctionDef)
                and n.name == "test_connection"]
    new_test, = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                and n.name == "test_connection"]
    new_test.body = old_test.body
    assert ast.dump(tree) == ast.dump(base_tree), "H03 exceeded flow-cleanup scope"


def validate_h04_platform(name, current, baseline):
    """Strip only confirmation checks; arguments and True paths stay byte-equal."""
    added_import = b"from ._commands import require_command_confirmation\n"
    check = b"        require_command_confirmation(success)\n"
    assert current.count(added_import) == 1
    assert current.count(check) == (2 if name == "switch.py" else 1)
    normalized = current.replace(added_import, b"").replace(check, b"")
    if name in {"button.py", "time.py", "datetime.py"}:
        assignment = b"        success = await self.entity_description."
        assert normalized.count(assignment) == 1
        normalized = normalized.replace(
            assignment, b"        await self.entity_description."
        )
    assert normalized == baseline, f"H04 exceeded confirmation scope: {name}"


def validate_h05_time(current, baseline):
    """Only the two coupled setter callbacks change; all entity behavior stays."""
    tree, base_tree = ast.parse(current), ast.parse(baseline)
    setters = [n for n in ast.walk(tree)
               if isinstance(n, ast.keyword) and n.arg == "set_func"]
    old_setters = [n for n in ast.walk(base_tree)
                   if isinstance(n, ast.keyword) and n.arg == "set_func"]
    assert len(setters) == len(old_setters) == 2
    for setter, old_setter in zip(setters, old_setters, strict=True):
        setter.value = old_setter.value
    assert ast.dump(tree) == ast.dump(base_tree), "H05 exceeded time setter scope"


def validate_h05_coordinator(current, baseline):
    """Permit only per-stove construction and passive existing-read boundaries."""
    additions = [
        b"from ._night_times import NightTimeCommands\n",
        b"        self.night_times = NightTimeCommands(stove)\n",
        b"        read_generation = self.night_times.read_started()\n",
        b"        self.night_times.read_finished(\n"
        b"            read_generation,\n"
        b"            data.get(pystove.DATA_NIGHT_BEGIN_TIME),\n"
        b"            data.get(pystove.DATA_NIGHT_END_TIME),\n"
        b"        )\n",
    ]
    for addition in additions:
        assert current.count(addition) == 1
        current = current.replace(addition, b"")
    assert current == baseline, "H05 changed existing coordinator behavior"


def validate_m03_button(current, baseline):
    """Only reuse the existing coordinator entity and pass its coordinator."""
    expected = baseline
    for old, new in (
        (b"from .entity import HWAMStoveBaseEntity, HWAMStoveEntityDescription",
         b"from .entity import HWAMStoveCoordinatorEntity, HWAMStoveEntityDescription"),
        (b"            stove_hub.stove,\n            config_entry,\n",
         b"            stove_hub,\n"),
        (b"class HwamStoveButton(HWAMStoveBaseEntity, ButtonEntity):",
         b"class HwamStoveButton(HWAMStoveCoordinatorEntity, ButtonEntity):"),
    ):
        assert expected.count(old) == 1
        expected = expected.replace(old, new)
    assert current == expected, "M03 exceeded button availability scope"


def validate_dependency_migration():
    """Only the exact manifest requirement may differ from the validated gate."""
    prefix = "custom_components/hwam_stove/"
    names = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", DEPENDENCY_BASE, "--", prefix],
        cwd=ROOT, text=True,
    ).splitlines()
    current = {str(p.relative_to(ROOT)) for p in RUNTIME.rglob("*")
               if p.is_file() and "__pycache__" not in p.parts}
    assert current == set(names), "Dependency migration changed runtime inventory"
    for name in names:
        expected = subprocess.check_output(
            ["git", "show", f"{DEPENDENCY_BASE}:{name}"], cwd=ROOT
        )
        if name == prefix + "manifest.json":
            assert expected.count(b'"pystove==0.3a1"') == 1
            expected = expected.replace(
                b'"pystove==0.3a1"', b'"saynwerk-pystove==0.3.0rc1"'
            )
        assert (ROOT / name).read_bytes() == expected, name
    return {"base": DEPENDENCY_BASE, "runtime_files": len(names),
            "changed_files": [prefix + "manifest.json"],
            "requirements": ["saynwerk-pystove==0.3.0rc1"]}


def validate():
    dependency = validate_dependency_migration()
    hashes = json_file(ROOT / "tests/fixtures/runtime_sha256.json")
    paths = {
        str(p.relative_to(ROOT)) for p in RUNTIME.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    prefix = "custom_components/hwam_stove/"
    legacy_paths = set(hashes) | {prefix + "migration.py"}
    h04_paths = legacy_paths | {prefix + "_commands.py"}
    m08_paths = h04_paths | {prefix + "_night_times.py"}
    assert paths == m08_paths | {prefix + "_clock.py"}, (
        "Runtime inventory changed"
    )
    m08_files = {}
    for path in m08_paths:
        current = (ROOT / path).read_bytes()
        if path == prefix + "manifest.json":
            # Check historical scopes against the pre-migration manifest bytes.
            current = current.replace(
                b'"saynwerk-pystove==0.3.0rc1"', b'"pystove==0.3a1"'
            )
        baseline = subprocess.check_output(
            ["git", "show", f"{M07_BASELINE}:{path}"], cwd=ROOT
        )
        expected = baseline
        if path in {prefix + "button.py", prefix + "datetime.py"}:
            old_import = b"from ._commands import require_command_confirmation\n"
            expected = expected.replace(old_import,
                b"from ._clock import stove_local_time\n" + old_import)
            old, new = (
                (b"stove.set_time()", b"stove.set_time(stove_local_time())")
                if path == prefix + "button.py" else
                (b"hub.stove.set_time(date_time)",
                 b"hub.stove.set_time(stove_local_time(date_time))")
            )
            assert expected.count(old) == 1
            expected = expected.replace(old, new)
        assert current == expected, f"M07 exceeded clock-writing scope: {path}"
        m08_files[path] = baseline
    m03_files = {}
    for path in m08_paths:
        current = m08_files[path]
        m03 = subprocess.check_output(
            ["git", "show", f"{M08_BASELINE}:{path}"], cwd=ROOT
        )
        if path == prefix + "sensor.py":
            old = b"        state_func=lambda data, key: data[key].seconds,\n"
            new = (b"        state_func=lambda data, key: "
                   b"data[key].days * 86400 + data[key].seconds,\n")
            assert m03.count(old) == 1
            assert current == m03.replace(old, new), "M08 exceeded duration scope"
        else:
            assert current == m03, f"M08 changed an unauthorized runtime file: {path}"
        m03_files[path] = m03
    h05_files = {}
    for path in m08_paths:
        current = m03_files[path]
        h05 = subprocess.check_output(
            ["git", "show", f"{M03_BASELINE}:{path}"], cwd=ROOT
        )
        if path == prefix + "button.py":
            validate_m03_button(current, h05)
        else:
            assert current == h05, f"M03 changed an unauthorized runtime file: {path}"
        h05_files[path] = h05
    h04_files = {}
    for path in h04_paths:
        current = h05_files[path]
        h04 = subprocess.check_output(
            ["git", "show", f"{H05_BASELINE}:{path}"], cwd=ROOT
        )
        relative = path.removeprefix(prefix)
        if relative == "time.py":
            validate_h05_time(current, h04)
        elif relative == "coordinator.py":
            validate_h05_coordinator(current, h04)
        elif relative in H04_TRANSLATIONS:
            strings = json.loads(current)
            added = strings["exceptions"].pop("night_times_not_synchronized")
            assert set(added) == {"message"}
            assert isinstance(added["message"], str) and added["message"]
            assert strings == json.loads(h04), f"H05 changed existing strings: {path}"
        else:
            assert current == h04, f"H05 changed an unauthorized runtime file: {path}"
        h04_files[path] = h04
    previous_scopes = {}
    for path in legacy_paths:
        current = h04_files[path]
        h03 = subprocess.check_output(
            ["git", "show", f"{H04_BASELINE}:{path}"], cwd=ROOT
        )
        relative = path.removeprefix(prefix)
        if relative in H04_PLATFORMS:
            validate_h04_platform(relative, current, h03)
        elif relative in H04_TRANSLATIONS:
            strings = json.loads(current)
            exceptions = strings.pop("exceptions")
            assert set(exceptions) == {"command_not_confirmed"}
            assert set(exceptions["command_not_confirmed"]) == {"message"}
            assert isinstance(exceptions["command_not_confirmed"]["message"], str)
            assert exceptions["command_not_confirmed"]["message"]
            assert strings == json.loads(h03), f"H04 changed existing strings: {path}"
        else:
            assert current == h03, f"H04 changed an unauthorized runtime file: {path}"
        # Validate the previous, still-frozen scopes against the pre-H04 files.
        current = h03
        h02 = subprocess.check_output(
            ["git", "show", f"{H03_BASELINE}:{path}"], cwd=ROOT
        )
        if path == prefix + "config_flow.py":
            validate_h03_flow(current, h02)
            # Verify the earlier, still-frozen scopes against the pre-H03 flow.
            current = h02
        else:
            assert current == h02, f"H03 changed an unauthorized runtime file: {path}"
        previous_scopes[path] = current
        h01a = subprocess.check_output(
            ["git", "show", f"{H02_BASELINE}:{path}"], cwd=ROOT
        )
        if path == prefix + "__init__.py":
            old_handler = b"    except (CancelledError, TimeoutError) as e:\n"
            assert h01a.count(old_handler) == 1
            expected_h02 = h01a.replace(old_handler, b"    except TimeoutError as e:\n")
            assert current == expected_h02, "H02 exceeded its one-line runtime scope"
        else:
            assert current == h01a, f"H02 changed an unauthorized runtime file: {path}"
        b01 = subprocess.check_output(
            ["git", "show", f"{H01A_BASELINE}:{path}"], cwd=ROOT
        )
        if path == prefix + "__init__.py":
            tree, b01_tree = ast.parse(current), ast.parse(b01)
            old_setup, = [n for n in b01_tree.body
                          if isinstance(n, ast.AsyncFunctionDef)
                          and n.name == "async_setup_entry"]
            new_setup, = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                          and n.name == "async_setup_entry"]
            tree.body[tree.body.index(new_setup)] = old_setup
            assert ast.dump(tree) == ast.dump(b01_tree), "H01A exceeded setup scope"
        else:
            assert current == b01, f"H01A changed an unauthorized runtime file: {path}"
    unchanged = 0
    for path, expected in hashes.items():
        current = previous_scopes[path]
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
            # Strip only the approved H01A function change before the original
            # B01-vs-foundation check. Imports, unload and migration remain checked.
            baseline_tree = ast.parse(baseline)
            old_setup, = [n for n in baseline_tree.body
                          if isinstance(n, ast.AsyncFunctionDef)
                          and n.name == "async_setup_entry"]
            new_setup, = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                          and n.name == "async_setup_entry"]
            tree.body[tree.body.index(new_setup)] = old_setup
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
        "codeowners": [], "requirements": ["saynwerk-pystove==0.3.0rc1"],
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
            "m07_runtime_files_byte_equal": len(m08_paths) - 2,
            "m07_changed_files": ["button.py", "datetime.py"],
            "m07_added_files": ["_clock.py"],
            "m08_runtime_files_byte_equal": len(m08_paths) - 1,
            "m08_changed_files": ["sensor.py"],
            "m03_runtime_files_byte_equal": len(m08_paths) - 1,
            "m03_changed_files": ["button.py"],
            "h05_runtime_files_byte_equal": len(h04_paths) - len(H05_CHANGED),
            "h05_changed_files": sorted(H05_CHANGED),
            "h05_added_files": ["_night_times.py"],
            "h04_runtime_files_byte_equal": len(legacy_paths)
                - len(H04_PLATFORMS | H04_TRANSLATIONS),
            "h04_changed_files": sorted(H04_PLATFORMS | H04_TRANSLATIONS),
            "h04_added_files": ["_commands.py"],
            "h03_runtime_files_byte_equal": len(legacy_paths) - 1,
            "h03_changed_files": ["config_flow.py"],
            "h02_runtime_files_byte_equal": len(legacy_paths) - 1,
            "h02_changed_files": ["__init__.py"],
            "h01a_runtime_files_byte_equal": len(legacy_paths) - 1,
            "h01a_changed_files": ["__init__.py"],
            "b01_changed_files": ["__init__.py", "config_flow.py"],
            "b01_added_files": ["migration.py"], "entities": len(rows),
            "translations": list(translations),
            "dependency_migration": dependency}


if __name__ == "__main__":
    print(json.dumps(validate(), indent=2))
