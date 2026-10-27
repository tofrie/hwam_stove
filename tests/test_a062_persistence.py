"""Persisted A06.1 gaps must reload without altering frozen model semantics."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from custom_components.hwam_stove import _shadow
from custom_components.hwam_stove._shadow_evidence import EVIDENCE_GAP_REASONS, Evidence
from custom_components.hwam_stove._shadow_model import ABSTENTION_REASONS, MODEL

from .test_a04_shadow import sample, warm
from .test_a042_persistence import disk
from .test_a043_benchmark import native_cycle
from .test_a061_gap_policy import adapter, observe

__all__ = ["disk"]
BASE = "a0545331d77c48a2333012b7079e22091df1c38e"


def test_only_supported_evidence_reason_added():
    assert frozenset(ABSTENTION_REASONS) | {
        "prolonged_outage"
    } == EVIDENCE_GAP_REASONS
    assert "prolonged_outage" not in ABSTENTION_REASONS
    assert "phase_change" not in EVIDENCE_GAP_REASONS
    assert set(Evidence().data["reasons"]) == set(ABSTENTION_REASONS)


@pytest.mark.parametrize("reason", sorted(EVIDENCE_GAP_REASONS))
@pytest.mark.parametrize("closed", [False, True])
def test_supported_gap_roundtrip_preserves_payload(reason, closed):
    model = warm()
    evidence = Evidence.migrate(model.dump())
    evidence.gap(reason)
    if closed:
        gap = evidence.data["open_gap"]
        gap["through"] = sample(70).at.isoformat()
        evidence.data["gaps"].append(gap)
        evidence.data["open_gap"] = None
    payload = evidence.dump(model)
    original = deepcopy(payload)
    restored = Evidence.restore(payload["evidence"], payload["engine"])
    assert restored.data == payload["evidence"]
    assert payload == original


@pytest.mark.parametrize("reason", ["phase_change", "unknown", "", None, 1, True])
@pytest.mark.parametrize("closed", [False, True])
def test_unsupported_gap_reasons_still_rejected(reason, closed):
    model = warm()
    evidence = Evidence.migrate(model.dump())
    evidence.gap(reason)
    if closed:
        gap = evidence.data["open_gap"]
        gap["through"] = sample(70).at.isoformat()
        evidence.data["gaps"].append(gap)
        evidence.data["open_gap"] = None
    payload = evidence.dump(model)
    with pytest.raises(ValueError, match="^Invalid gap reason$"):
        Evidence.restore(payload["evidence"], payload["engine"])


def test_model_reason_inventory_not_extended():
    model = warm()
    evidence = Evidence.migrate(model.dump())
    payload = evidence.dump(model)
    payload["evidence"]["reasons"]["prolonged_outage"] = 1
    with pytest.raises(ValueError, match="^Invalid reason inventory$"):
        Evidence.restore(payload["evidence"], payload["engine"])


async def test_real_generated_gap_checkpoint_and_repeated_restarts(disk, hass):
    shadow, analytics, data, tick = await adapter(hass)
    for seconds in range(0, 601, 10):
        await observe(shadow, analytics, data, tick, seconds)
    assert shadow.model.pending
    await observe(shadow, analytics, data, tick, 640)
    assert shadow.model.pending is None
    assert shadow.model.ledger[-1]["outcome"] == "sampling_gap"
    assert shadow.evidence.data["gaps"][-1]["reason"] == "prolonged_outage"
    # Keep an existing closed archive; no new policy or rescoring on recovery.
    shadow.rc8_benchmark = native_cycle(actual=40).dump()
    await shadow._save()
    path = Path(shadow.store().path)
    snapshot = json.loads(path.read_bytes())["data"]
    assert snapshot["schema"] == 5
    assert shadow.summary()["checkpoint_verified"]
    for _ in range(4):
        restored = _shadow.Shadow(hass, "gap-test")
        await restored.async_load()
        assert restored.healthy and restored.summary()["checkpoint_verified"]
        assert restored.model.ledger == snapshot["engine"]["ledger"]
        assert restored.evidence.data["models"] == snapshot["evidence"]["models"]
        assert (
            restored.evidence.data["annotations"]
            == snapshot["evidence"]["annotations"]
        )
        assert restored.rc8_benchmark == snapshot["rc8_benchmark"]
        assert restored.summary()["per_model"][MODEL]["issued"] == 1
        assert restored.summary()["invalidated_predictions"] == 1
        assert json.loads(path.read_bytes())["data"]["schema"] == 5
        await restored.async_stop()


async def test_pending_restart_invalidates_once_without_reset(disk):
    shadow = disk()
    await shadow.async_load()
    shadow.model = warm()
    await shadow._save()
    original = deepcopy(shadow.model.pending)
    for _ in range(3):
        shadow = disk()
        await shadow.async_load()
        assert shadow.healthy
        assert shadow.summary()["per_model"][MODEL]["issued"] == 1
        assert shadow.summary()["invalidated_predictions"] == 1
        assert len(shadow.model.ledger) == 1
        assert shadow.model.ledger[0]["prediction_at"] == original["prediction_at"]
        assert shadow.model.ledger[0]["gap_invalid"]


@pytest.mark.parametrize("point", ["counter", "histogram", "processed", "annotation"])
def test_valid_gap_does_not_hide_unrelated_inconsistency(point):
    model = warm()
    model.step(sample(70, alarm=True))
    evidence = Evidence.migrate(model.dump())
    evidence.gap("prolonged_outage")
    payload = evidence.dump(model)
    if point == "counter":
        payload["evidence"]["models"][MODEL]["scored"] = -1
    elif point == "histogram":
        payload["evidence"]["models"][MODEL]["errors"] = [0]
    elif point == "processed":
        payload["evidence"]["processed"] += 1
    else:
        payload["evidence"]["annotations"] = []
    with pytest.raises(ValueError):
        Evidence.restore(payload["evidence"], payload["engine"])


def test_rc9_runtime_diff_and_identity_files_exact():
    root = Path(__file__).resolve().parents[1]
    names = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", BASE, "custom_components/hwam_stove"],
        cwd=root, text=True,
    ).splitlines()
    changed = []
    for name in names:
        old = subprocess.check_output(["git", "show", f"{BASE}:{name}"], cwd=root)
        new = (root / name).read_bytes()
        if old != new:
            changed.append(Path(name).name)
        if Path(name).name == "manifest.json":
            assert json.loads(new) == {**json.loads(old), "version": "1.0.0rc10"}
        elif Path(name).name == "_shadow_evidence.py":
            addition = (
                b"# Persisted evidence gaps have a separate vocabulary "
                b"from frozen model reasons.\n"
                b'EVIDENCE_GAP_REASONS = frozenset(ABSTENTION_REASONS) | '
                b'{"prolonged_outage"}\n\n'
            )
            expected = old.replace(
                b"SCHEMA = 2\n", addition + b"SCHEMA = 2\n", 1
            ).replace(
                b'if gap["reason"] not in ABSTENTION_REASONS:',
                b'if gap["reason"] not in EVIDENCE_GAP_REASONS:',
            )
            assert hashlib.sha256(new).digest() == hashlib.sha256(expected).digest()
    assert sorted(changed) == ["_shadow_evidence.py", "manifest.json"]
