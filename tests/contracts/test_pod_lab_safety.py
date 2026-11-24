"""Destructive lab commands must stay within resources carrying the saved instance ID."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "pod_lab", Path(__file__).resolve().parents[2] / "scripts/pod_lab.py"
)
lab_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab_module)


@pytest.mark.parametrize("name", ["production", "../artemis-lab", "artemis-lab/other"])
def test_lab_name_cannot_escape_its_namespace(name):
    with pytest.raises(ValueError):
        lab_module.Lab(name)


def test_down_rejects_foreign_pod_before_removing_anything(monkeypatch, tmp_path):
    monkeypatch.setattr(lab_module, "ROOT", tmp_path)
    lab = lab_module.Lab()
    lab.config = {"id": "owned"}
    calls = []

    def fake(*args, **kw):
        calls.append(args)
        assert args[:2] == ("pod", "inspect"), "No removal should precede ownership validation"
        owner = "foreign" if args[2].endswith("node-3") else "owned"
        return SimpleNamespace(
            returncode=0, stdout=json.dumps([{"Labels": {lab_module.LABEL: owner}}])
        )

    monkeypatch.setattr(lab_module, "podman", fake)
    with pytest.raises(RuntimeError, match="unrelated pod"):
        lab.down()
    assert len(calls) == 3


def test_restart_does_not_touch_foreign_container(monkeypatch, tmp_path):
    monkeypatch.setattr(lab_module, "ROOT", tmp_path)
    lab = lab_module.Lab()
    lab.config = {"id": "owned"}
    calls = []

    def fake(*args, **kw):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='[{"Config":{"Labels":{}}}]')

    monkeypatch.setattr(lab_module, "podman", fake)
    with pytest.raises(RuntimeError, match="unrelated container"):
        lab.restart("api")
    assert calls == [("container", "inspect", "artemis-lab-api")]
