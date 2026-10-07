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
RELEASE_BASE = "801d9bc9ed137872b07e222c178734281da2e2b5"
M04_BASE = "6992abeb0af5881accf72d298400331d2025c5f5"
M04_CHANGED = {"__init__.py", "config_flow.py"} | H04_TRANSLATIONS
M05_BASE = "f72afd3819601e11fa9049139cd641799a05fb28"
M05_CHANGED = {"__init__.py", "config_flow.py"} | H04_TRANSLATIONS
M01_BASE = "69edff649cc0523bb363cb8f485114b58ebe37ab"
M01_CHANGED = {"__init__.py", "config_flow.py", "const.py"}
O01_BASE = "2a93f53b8e60d80fa422d64e6b526936686897c9"
O02_BASE = "7b5187b617bd2672b27fa9660cf941a547d66bef"
L04_BASE = "7e2ae84ffba85f452b8ce51e29173d97c7b9d820"
L04_CHANGED = {"binary_sensor.py"} | H04_TRANSLATIONS
O02_KEYS = {
    "OXYGEN_LEVEL", "ROOM_TEMPERATURE", "STOVE_TEMPERATURE",
    "VALVE1_POSITION", "VALVE2_POSITION", "VALVE3_POSITION",
}
M06_BASE = "1c255c25cd0aeac0ac2d479a61673df1ca48b440"
M06_CHANGED = {"__init__.py", "config_flow.py"} | H04_TRANSLATIONS
M02_BASE = "61820eab74a8a97140f1abe462c8d35593ea9495"
M02_CHANGED = H04_PLATFORMS | {"coordinator.py"}
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


def before_l04(name, current):
    """Only door PROBLEM metadata and the two alarm translation blocks change."""
    relative = name.removeprefix("custom_components/hwam_stove/")
    if relative not in L04_CHANGED:
        return current
    baseline = subprocess.check_output(
        ["git", "show", f"{L04_BASE}:{name}"], cwd=ROOT
    )
    if relative == "binary_sensor.py":
        old = b"device_class=BinarySensorDeviceClass.DOOR,"
        assert baseline.count(old) == 1
        expected = baseline.replace(
            old, b"device_class=BinarySensorDeviceClass.PROBLEM,"
        )
    else:
        language = Path(relative).stem
        replacements = {
            "de": [("Überhitzung", "Schornsteinüberhitzung"),
                   ("Tür zu lange auf", "Tür zu lange offen"),
                   ('"Nein"', '"Inaktiv"'), ('"Ja"', '"Aktiv"')],
            "en": [("Overheat", "Chimney overheating"),
                   ('"No"', '"Inactive"'), ('"Yes"', '"Active"')],
            "nl": [("Oververhitting", "Oververhitting van de schoorsteen"),
                   ('"Nee"', '"Inactief"'), ('"Ja"', '"Actief"')],
        }
        start = baseline.index(b'      "safety_alarms_stove_overheat": {')
        end = baseline.index(b'      "safety_alarms_manual_safety_alarm": {', start)
        block = baseline[start:end]
        for old, new in replacements[language]:
            assert block.count(old.encode()) == 1
            block = block.replace(old.encode(), new.encode())
        expected = baseline[:start] + block + baseline[end:]
    assert current == expected, f"L04 exceeded alarm metadata scope: {name}"
    return baseline


def validate_l04():
    prefix = "custom_components/hwam_stove/"
    names = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", L04_BASE, "--", prefix],
        cwd=ROOT, text=True,
    ).splitlines()
    current = {str(p.relative_to(ROOT)) for p in RUNTIME.rglob("*")
               if p.is_file() and "__pycache__" not in p.parts}
    assert current == set(names), "L04 changed runtime inventory"
    for name in names:
        expected = subprocess.check_output(
            ["git", "show", f"{L04_BASE}:{name}"], cwd=ROOT
        )
        assert before_l04(name, (ROOT / name).read_bytes()) == expected, name
    return len(names) - len(L04_CHANGED)


def before_o02(name, current):
    """Allow one import and MEASUREMENT on exactly six existing descriptions."""
    current = before_l04(name, current)
    if name != "custom_components/hwam_stove/sensor.py":
        return current
    baseline = subprocess.check_output(
        ["git", "show", f"{O02_BASE}:{name}"], cwd=ROOT
    )
    old = b"    SensorEntityDescription,\n)\nfrom homeassistant.config_entries"
    assert baseline.count(old) == 1
    expected = baseline.replace(old, old.replace(
        b"\n)", b"\n    SensorStateClass,\n)"
    ))
    for key in O02_KEYS:
        old = f"        key=pystove.DATA_{key},\n".encode()
        assert expected.count(old) == 1
        expected = expected.replace(
            old, old + b"        state_class=SensorStateClass.MEASUREMENT,\n"
        )
    assert current == expected, "O02 exceeded measurement metadata scope"
    return baseline


def validate_o02():
    prefix = "custom_components/hwam_stove/"
    names = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", O02_BASE, "--", prefix],
        cwd=ROOT, text=True,
    ).splitlines()
    current = {str(p.relative_to(ROOT)) for p in RUNTIME.rglob("*")
               if p.is_file() and "__pycache__" not in p.parts}
    assert current == set(names), "O02 changed runtime inventory"
    for name in names:
        expected = subprocess.check_output(
            ["git", "show", f"{O02_BASE}:{name}"], cwd=ROOT
        )
        assert before_o02(name, (ROOT / name).read_bytes()) == expected, name
    return len(names) - 1


def before_m01(name, current):
    """Permit only exact create-boundary mappings; preserve H03/post-create code."""
    current = before_o02(name, current)
    relative = name.removeprefix("custom_components/hwam_stove/")
    if relative not in M01_CHANGED:
        return current
    baseline = subprocess.check_output(
        ["git", "show", f"{M01_BASE}:{name}"], cwd=ROOT
    )
    imports = (
        b"from .const import DATA_STOVES, DOMAIN\n",
        b"from .const import CREATE_TRANSPORT_ERRORS, DATA_STOVES, "
        b"DOMAIN, EXCLUDED_CREATE_ERRORS\n",
    )
    if relative == "__init__.py":
        replacements = [imports, (
            b"    except TimeoutError as e:\n",
            b"    except EXCLUDED_CREATE_ERRORS:\n"
            b"        raise\n"
            b"    except CREATE_TRANSPORT_ERRORS as e:\n",
        )]
    elif relative == "config_flow.py":
        replacements = [imports, (b"from aiohttp import ClientError\n", b""), (
            b"        stove = await pystove.Stove.create(host)\n",
            b"        try:\n"
            b"            stove = await pystove.Stove.create(host)\n"
            b"        except EXCLUDED_CREATE_ERRORS:\n"
            b"            raise\n"
            b"        except CREATE_TRANSPORT_ERRORS as err:\n"
            b"            # Reuse the existing cannot_connect result, "
            b"retaining the cause.\n"
            b"            # No client was returned; initialization cleanup "
            b"belongs to pystove.\n"
            b"            raise ConnectionError() from err\n",
        ), (
            b"            except (ConnectionError, TimeoutError, ClientError):\n",
            b"            except ConnectionError:\n",
        )]
    else:
        replacements = [(
            b"from enum import StrEnum\n",
            b"from enum import StrEnum\n\n"
            b"from aiohttp import (\n"
            b"    ClientConnectionError,\n    ClientPayloadError,\n"
            b"    ClientProxyConnectionError,\n    ClientSSLError,\n"
            b"    ServerFingerprintMismatch,\n)\n",
        ), (
            b'DOMAIN = "hwam_stove"\n',
            b'DOMAIN = "hwam_stove"\n\n'
            b"# Only for the Stove.create boundary, never polling, commands "
            b"or owned cleanup.\n"
            b"CREATE_TRANSPORT_ERRORS = (TimeoutError, ClientConnectionError, "
            b"ClientPayloadError)\n"
            b"# These inherit socket errors but are not proven temporary "
            b"direct-HTTP failures.\n"
            b"EXCLUDED_CREATE_ERRORS = (\n"
            b"    ClientSSLError, ClientProxyConnectionError, "
            b"ServerFingerprintMismatch,\n)\n",
        )]
    expected = baseline
    for old, new in replacements:
        assert expected.count(old) == 1, relative
        expected = expected.replace(old, new)
    assert current == expected, f"M01 exceeded create transport scope: {relative}"
    return baseline


def validate_o01():
    """O01 adds diagnostics only; every existing runtime file stays byte-identical."""
    prefix = "custom_components/hwam_stove/"
    paths = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", O01_BASE, "--", prefix],
        cwd=ROOT, text=True,
    ).splitlines()
    current = {str(p.relative_to(ROOT)) for p in RUNTIME.rglob("*")
               if p.is_file() and "__pycache__" not in p.parts}
    assert current == set(paths) | {prefix + "diagnostics.py"}
    for name in paths:
        expected = subprocess.check_output(
            ["git", "show", f"{O01_BASE}:{name}"], cwd=ROOT
        )
        assert before_o02(name, (ROOT / name).read_bytes()) == expected, name
    return len(paths)


def validate_m01():
    prefix = "custom_components/hwam_stove/"
    paths = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", M01_BASE, "--", prefix],
        cwd=ROOT, text=True,
    ).splitlines()
    assert set(paths) | {prefix + "diagnostics.py"} == {
                          str(p.relative_to(ROOT)) for p in RUNTIME.rglob("*")
                          if p.is_file() and "__pycache__" not in p.parts}
    for name in paths:
        expected = subprocess.check_output(
            ["git", "show", f"{M01_BASE}:{name}"], cwd=ROOT
        )
        assert before_m01(name, (ROOT / name).read_bytes()) == expected, name


def before_m06(name, current):
    """Constrain reconfigure to its flow, startup YAML guard and translations."""
    current = before_m01(name, current)
    relative = name.removeprefix("custom_components/hwam_stove/")
    if relative not in M06_CHANGED:
        return current
    baseline = subprocess.check_output(
        ["git", "show", f"{M06_BASE}:{name}"], cwd=ROOT
    )
    if relative in H04_TRANSLATIONS:
        actual, original = json.loads(current), json.loads(baseline)
        step = actual["config"]["step"].pop("reconfigure")
        assert set(step) == {"title", "description", "data"}
        assert set(step["data"]) == {"host"}
        for key in {"yaml_not_checked", "yaml_configuration", "yaml_check_failed"}:
            assert actual["config"]["error"].pop(key)
        for key in {"reconfigure_successful", "reconfigure_unchanged",
                    "reconfigure_entry_changed", "reconfigure_cancelled",
                    "reconfigure_not_ready"}:
            assert actual["config"]["abort"].pop(key)
        assert actual == original, "M06 altered existing translations"
        return baseline
    if relative == "__init__.py":
        additions = [
            b'_YAML_HOSTS = f"{DOMAIN}_yaml_hosts"\n',
            b"    # Reconfigure must not release an address still owned "
            b"by a loaded YAML batch.\n"
            b"    # Keep this startup snapshot until HA restarts; "
            b"no persistent identity/alias.\n"
            b"    hass.data.setdefault(_YAML_HOSTS, set()).update(\n"
            b"        host_key(device[CONF_HOST]) for device in devices.values()\n"
            b"    )\n",
        ]
        for addition in additions:
            assert current.count(addition) == 1
            current = current.replace(addition, b"")
        assert current == baseline, "M06 altered lifecycle, migration or YAML imports"
        return baseline
    tree, old_tree = ast.parse(current), ast.parse(baseline)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    old_cls = next(n for n in old_tree.body if isinstance(n, ast.ClassDef))
    shared_test = next(n for n in cls.body if getattr(n, "name", None)
                       == "_async_test_connection")
    old_test = next(n for n in ast.walk(old_cls) if getattr(n, "name", None)
                   == "test_connection")
    assert [ast.dump(n) for n in shared_test.body[1:]] == [
        ast.dump(n) for n in old_test.body[1:]
    ], "M06 changed the H03 connection-validation contract"
    additions = {"_reserve_host", "_async_test_connection", "_async_yaml_host_guard",
                 "_reconfigure_ready", "async_step_reconfigure",
                 "_show_reconfigure_form"}
    added = [n for n in cls.body if getattr(n, "name", None) in additions]
    assert {n.name for n in added} == additions
    cls.body = [n for n in cls.body if n not in added]
    for method in {"is_matching", "_host_configured", "async_step_init"}:
        new = next(n for n in cls.body if getattr(n, "name", None) == method)
        old = next(n for n in old_cls.body if getattr(n, "name", None) == method)
        cls.body[cls.body.index(new)] = old
    imports = {
        "aiohttp": {"ClientError"},
        "homeassistant.config_entries": {
            "SOURCE_RECONFIGURE", "ConfigEntry", "ConfigEntryState", "ConfigFlow",
            "ConfigFlowResult"},
        "homeassistant.exceptions": {"HomeAssistantError"},
        "homeassistant.helpers.reload": {"async_integration_yaml_config"},
        None: {"_YAML_HOSTS"},
        "const": {"DATA_STOVES", "DOMAIN"},
    }
    for module, names in imports.items():
        item = next(n for n in tree.body if isinstance(n, ast.ImportFrom)
                    and n.module == module)
        assert {a.name for a in item.names} == names
        original = next((n for n in old_tree.body if isinstance(n, ast.ImportFrom)
                         and n.module == module), None)
        if original is None:
            tree.body.remove(item)
        else:
            tree.body[tree.body.index(item)] = original
    assert ast.dump(tree) == ast.dump(old_tree), "M06 exceeded reconfigure flow scope"
    return baseline


def validate_m06():
    prefix = "custom_components/hwam_stove/"
    paths = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", M06_BASE, "--", prefix],
        cwd=ROOT, text=True,
    ).splitlines()
    assert set(paths) | {prefix + "diagnostics.py"} == {
                          str(p.relative_to(ROOT)) for p in RUNTIME.rglob("*")
                          if p.is_file() and "__pycache__" not in p.parts}
    for name in paths:
        expected = subprocess.check_output(
            ["git", "show", f"{M06_BASE}:{name}"], cwd=ROOT
        )
        assert before_m06(name, (ROOT / name).read_bytes()) == expected, name


def before_m05(name, current):
    """Permit address validation/matching only; retain all prior lifecycle guards."""
    current = before_m06(name, current)
    relative = name.removeprefix("custom_components/hwam_stove/")
    if relative not in M05_CHANGED:
        return current
    baseline = subprocess.check_output(
        ["git", "show", f"{M05_BASE}:{name}"], cwd=ROOT
    )
    if relative in H04_TRANSLATIONS:
        actual, original = json.loads(current), json.loads(baseline)
        assert actual["config"]["error"].pop("invalid_host")
        abort = actual["config"].pop("abort")
        assert set(abort) == {"already_configured", "already_in_progress"}
        assert all(abort.values())
        key = "deprecated_import_from_configuration_yaml"
        substitutions = {
            "en": ("distinct host strings", "syntactically distinct hosts"),
            "de": ("unterschiedlichen Host-Zeichenfolgen",
                   "syntaktisch verschiedenen Hosts"),
            "nl": ("verschillende hostteksten", "syntactisch verschillende hosts"),
        }
        old, new = substitutions[Path(relative).stem]
        expected = original["issues"][key]["description"].replace(old, new)
        assert actual["issues"][key]["description"] == expected
        actual["issues"][key]["description"] = original["issues"][key]["description"]
        assert actual == original, "M05 changed unrelated translations"
        return baseline
    tree, old_tree = ast.parse(current), ast.parse(baseline)
    host_import = next(n for n in tree.body if isinstance(n, ast.ImportFrom)
                       and n.module == "_host")
    assert {a.name for a in host_import.names} == (
        {"host_key", "normalize_host"} if relative == "config_flow.py" else {"host_key"}
    )
    tree.body.remove(host_import)
    if relative == "__init__.py":
        for method in ("_async_import_yaml", "_async_yaml_issue"):
            new = next(n for n in tree.body if getattr(n, "name", None) == method)
            old = next(n for n in old_tree.body if getattr(n, "name", None) == method)
            tree.body[tree.body.index(new)] = old
    else:
        current_import = next(n for n in tree.body if isinstance(n, ast.ImportFrom)
                              and n.module == "asyncio")
        assert {a.name for a in current_import.names} == {
            "CancelledError", "Task", "create_task", "current_task", "shield"
        }
        old_import = next(n for n in old_tree.body if isinstance(n, ast.ImportFrom)
                          and n.module == "asyncio")
        tree.body[tree.body.index(current_import)] = old_import
        old_tree.body = [n for n in old_tree.body if not (
            isinstance(n, ast.ImportFrom)
            and n.module == "homeassistant.data_entry_flow"
        )]
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        old_cls = next(n for n in old_tree.body if isinstance(n, ast.ClassDef))
        # The existing connection validation and cancellation-safe client cleanup
        # must survive unchanged within the new host/reservation wrapper.
        new_test = next(n for n in ast.walk(cls) if getattr(n, "name", None)
                        == "test_connection")
        old_test = next(n for n in ast.walk(old_cls) if getattr(n, "name", None)
                        == "test_connection")
        assert ast.dump(new_test) == ast.dump(old_test)
        helpers = {"is_matching", "_release_host", "_host_owner_done",
                   "_host_configured"}
        added = [n for n in cls.body if getattr(n, "name", None) in helpers]
        assert {n.name for n in added} == helpers
        fields = [n for n in cls.body if isinstance(n, ast.AnnAssign)
                  and ast.unparse(n.target) in {"_pending_host", "_host_owner"}]
        assert len(fields) == 2
        cls.body = [n for n in cls.body if n not in added + fields]
        for method in ("async_step_init", "async_step_import"):
            new = next(n for n in cls.body if getattr(n, "name", None) == method)
            old = next(n for n in old_cls.body if getattr(n, "name", None) == method)
            cls.body[cls.body.index(new)] = old
    assert ast.dump(tree) == ast.dump(old_tree), "M05 exceeded host/duplicate scope"
    return baseline


def before_m04(name, current):
    """Allow only YAML batch setup/import completion and its repair description."""
    current = before_m05(name, current)
    relative = name.removeprefix("custom_components/hwam_stove/")
    if relative not in M04_CHANGED:
        return current
    baseline = subprocess.check_output(
        ["git", "show", f"{M04_BASE}:{name}"], cwd=ROOT
    )
    if relative in H04_TRANSLATIONS:
        actual, original = json.loads(current), json.loads(baseline)
        key = "deprecated_import_from_configuration_yaml"
        message = actual["issues"][key]["description"]
        assert "monitored_variables" in message
        for placeholder in ("configured", "total", "missing", "duplicates"):
            assert "{" + placeholder + "}" in message
        actual["issues"][key]["description"] = original["issues"][key]["description"]
        assert actual == original, "M04 changed unrelated translation content"
        return baseline
    tree, old_tree = ast.parse(current), ast.parse(baseline)
    if relative == "config_flow.py":
        imports = [n for n in tree.body if isinstance(n, ast.ImportFrom)
                   and n.module == "homeassistant.data_entry_flow"]
        assert len(imports) == 1
        assert ast.unparse(imports[0]) == (
            "from homeassistant.data_entry_flow import FlowResultType")
        tree.body.remove(imports[0])
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        old_cls = next(n for n in old_tree.body if isinstance(n, ast.ClassDef))
        new = next(n for n in cls.body if getattr(n, "name", None)
                   == "async_step_import")
        old = next(n for n in old_cls.body if getattr(n, "name", None)
                   == "async_step_import")
        cls.body[cls.body.index(new)] = old
    else:
        imports = [n for n in tree.body if isinstance(n, ast.ImportFrom)
                   and n.module == "asyncio"]
        assert len(imports) == 1
        assert {a.name for a in imports[0].names} == {"CancelledError", "Lock"}
        imports[0].names = [a for a in imports[0].names if a.name != "Lock"]
        helpers = {"_async_yaml_issue", "_async_import_yaml"}
        added = [n for n in tree.body if getattr(n, "name", None) in helpers]
        assert {n.name for n in added} == helpers
        constants = [n for n in tree.body if isinstance(n, ast.Assign)
                     and ast.unparse(n.targets[0]) in {
                         "_YAML_IMPORT_LOCK", "_YAML_ISSUE"}]
        assert len(constants) == 2
        tree.body = [n for n in tree.body if n not in added + constants]
        new = next(n for n in tree.body if getattr(n, "name", None) == "async_setup")
        old = next(n for n in old_tree.body
                   if getattr(n, "name", None) == "async_setup")
        tree.body[tree.body.index(new)] = old
    assert ast.dump(tree) == ast.dump(old_tree), "M04 exceeded YAML import scope"
    return baseline


def before_m02(name, current):
    """Constrain M02 to command readback; retain all historical scope guards."""
    current = before_m04(name, current)
    relative = name.removeprefix("custom_components/hwam_stove/")
    if relative not in M02_CHANGED:
        return current
    baseline = subprocess.check_output(
        ["git", "show", f"{M02_BASE}:{name}"], cwd=ROOT
    )
    if relative in H04_PLATFORMS:
        expected = baseline
        if relative in {"number.py", "switch.py"}:
            expected = expected.replace(b"if success:", b"if success is True:")
            lines = expected.splitlines(keepends=True)
            expected = b"".join(
                line + (b"            await self.coordinator."
                        b"async_refresh_after_command(reconcile=True)\n"
                        if line.startswith(b"            self.async_") else b"")
                for line in lines
            )
        else:
            expected = expected.replace(
                b"        require_command_confirmation(success)\n",
                b"        require_command_confirmation(success)\n"
                b"        if success is True:\n"
                b"            await self.coordinator.async_refresh_after_command()\n",
            )
        assert current == expected, f"M02 exceeded command readback scope: {name}"
        return baseline
    tree, base_tree = ast.parse(current), ast.parse(baseline)
    # Allow the coordinator refresh wrapper and equality-suppressed notification
    # hook. Verify the original data processing, H05 calls and setup are retained.
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    base_cls = next(n for n in base_tree.body if isinstance(n, ast.ClassDef))
    additions = {"async_refresh_after_command", "_async_command_refresh",
                 "_async_refresh_finished"}
    added = [n for n in cls.body if getattr(n, "name", None) in additions]
    assert {n.name for n in added} == additions
    cls.body = [n for n in cls.body if n not in added]
    init = next(n for n in cls.body if getattr(n, "name", None) == "__init__")
    allowed_assignments = {"request_debouncer", "request_debouncer.function",
                           "self._command_read", "self._command_reconcile"}
    removed = [n for n in init.body if isinstance(n, ast.Assign)
               and ast.unparse(n.targets[0]) in allowed_assignments]
    assert len(removed) == 4
    init.body = [n for n in init.body if n not in removed]
    super_call = next(n.value for n in init.body if isinstance(n, ast.Expr)
                      and isinstance(n.value, ast.Call)
                      and ast.unparse(n.value.func) == "super().__init__")
    keywords = [k for k in super_call.keywords
                if k.arg == "request_refresh_debouncer"]
    assert len(keywords) == 1
    assert ast.unparse(keywords[0].value) == "request_debouncer"
    super_call.keywords.remove(keywords[0])
    update = next(n for n in cls.body
                  if getattr(n, "name", None) == "_async_update_data")
    captures = {"self._read_previous_data = self.data",
                "self._read_previous_success = self.last_update_success"}
    removed = [n for n in update.body if ast.unparse(n) in captures]
    assert len(removed) == 2
    update.body = [n for n in update.body if n not in removed]
    guard = next(n for n in update.body if isinstance(n, ast.If)
                 and ast.unparse(n.test) == "not self._command_read")
    assert not guard.orelse and len(guard.body) == 1
    update.body[update.body.index(guard)] = guard.body[0]
    assert ast.dump(cls) == ast.dump(base_cls), "M02 altered existing coordinator logic"
    # The only other module additions are imports needed by those hooks.
    imports = [n for n in tree.body if isinstance(n, ast.ImportFrom)
               and n.module in {"homeassistant.core", "homeassistant.helpers.debounce",
                                "homeassistant.helpers.update_coordinator"}]
    assert {(n.module, a.name) for n in imports for a in n.names} == {
        ("homeassistant.core", "HomeAssistant"), ("homeassistant.core", "callback"),
        ("homeassistant.helpers.debounce", "Debouncer"),
        *( ("homeassistant.helpers.update_coordinator", name) for name in (
            "REQUEST_REFRESH_DEFAULT_COOLDOWN", "REQUEST_REFRESH_DEFAULT_IMMEDIATE",
            "DataUpdateCoordinator", "UpdateFailed")),
    }
    tree.body = [n for n in tree.body if n not in imports]
    base_tree.body = [n for n in base_tree.body if not (
        isinstance(n, ast.ImportFrom) and n.module in {
            "homeassistant.core", "homeassistant.helpers.update_coordinator"})]
    assert ast.dump(tree) == ast.dump(base_tree), "M02 exceeded coordinator scope"
    return baseline


def validate_dependency_migration():
    """Validate historical dependency/release scope after checking M02 changes."""
    prefix = "custom_components/hwam_stove/"
    names = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", DEPENDENCY_BASE, "--", prefix],
        cwd=ROOT, text=True,
    ).splitlines()
    current = {str(p.relative_to(ROOT)) for p in RUNTIME.rglob("*")
               if p.is_file() and "__pycache__" not in p.parts}
    assert current == set(names) | {
        prefix + "_host.py", prefix + "diagnostics.py"
    }, (
        "Dependency migration changed runtime inventory"
    )
    for name in names:
        expected = subprocess.check_output(
            ["git", "show", f"{DEPENDENCY_BASE}:{name}"], cwd=ROOT
        )
        if name == prefix + "manifest.json":
            assert expected.count(b'"pystove==0.3a1"') == 1
            expected = expected.replace(
                b'"pystove==0.3a1"', b'"saynwerk-pystove==0.3.0rc1"'
            )
            release_manifest = json.loads(expected)
            release_manifest.update({
                "version": "1.0.0rc1",
                "documentation": "https://github.com/tofrie/hwam_stove",
                "issue_tracker": "https://github.com/tofrie/hwam_stove/issues",
            })
            actual = json_file(ROOT / name)
            assert actual == release_manifest, "Unapproved release manifest change"
            assert list(actual) == ["domain", "name"] + sorted(
                key for key in actual if key not in {"domain", "name"}
            ), "Manifest keys must follow Hassfest ordering"
            continue
        current = before_m02(name, (ROOT / name).read_bytes())
        assert current == expected, name
        approved = subprocess.check_output(
            ["git", "show", f"{RELEASE_BASE}:{name}"], cwd=ROOT
        )
        assert current == approved, name
    return {"base": DEPENDENCY_BASE, "runtime_files": len(names),
            "changed_files": [prefix + "manifest.json"],
            "requirements": ["saynwerk-pystove==0.3.0rc1"]}


def validate():
    l04_unchanged = validate_l04()
    o02_unchanged = validate_o02()
    o01_unchanged = validate_o01()
    validate_m01()
    validate_m06()
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
    assert paths == m08_paths | {
        prefix + "_clock.py", prefix + "_host.py", prefix + "diagnostics.py"
    }, (
        "Runtime inventory changed"
    )
    m08_files = {}
    for path in m08_paths:
        current = before_m02(path, (ROOT / path).read_bytes())
        if path == prefix + "manifest.json":
            # The exact current manifest is checked above. Historical scope
            # checks must still use their original manifest, including its bytes.
            current = subprocess.check_output(
                ["git", "show", f"{DEPENDENCY_BASE}:{path}"], cwd=ROOT
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
        "documentation": "https://github.com/tofrie/hwam_stove", "dependencies": [],
        "issue_tracker": "https://github.com/tofrie/hwam_stove/issues",
        "codeowners": [], "requirements": ["saynwerk-pystove==0.3.0rc1"],
        "version": "1.0.0rc1", "iot_class": "local_polling",
    }, "Manifest differs from the exact approved release metadata"
    assert json_file(ROOT / "hacs.json") == {
        "name": "Saynwerk HWAM Smart Stove", "hide_default_branch": True,
        "homeassistant": "2026.9.4",
    }
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
            "already_configured", "cannot_connect", "invalid_host",
            "yaml_not_checked", "yaml_configuration", "yaml_check_failed",
        }
        assert "deprecated_import_from_configuration_yaml" in strings["issues"]
        for row in rows:
            text = strings["entity"][row["platform"]][row["translation_key"]]
            assert text["name"], (language, row)
            if row["options"]:
                assert set(text["state"]) == set(row["options"]), (language, row)
    return {"l04_base": L04_BASE,
            "l04_changed_files": sorted(L04_CHANGED),
            "l04_runtime_files_byte_equal": l04_unchanged,
            "o02_base": O02_BASE,
            "o02_changed_files": ["sensor.py"],
            "o02_measurement_keys": sorted(O02_KEYS),
            "o02_runtime_files_byte_equal": o02_unchanged,
            "o01_base": O01_BASE,
            "o01_added_files": ["diagnostics.py"],
            "o01_runtime_files_byte_equal": o01_unchanged,
            "m01_changed_files": sorted(M01_CHANGED),
            "m01_base": M01_BASE,
            "m06_changed_files": sorted(M06_CHANGED),
            "m06_base": M06_BASE,
            "m05_changed_files": sorted(M05_CHANGED),
            "m05_added_files": ["_host.py"],
            "m05_base": M05_BASE,
            "m04_changed_files": sorted(M04_CHANGED),
            "m04_base": M04_BASE,
            "m02_changed_files": sorted(M02_CHANGED),
            "m02_base": M02_BASE,
            "runtime_files_byte_equal": unchanged,
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
