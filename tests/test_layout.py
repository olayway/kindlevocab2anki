"""Card layout selection: template placement, resolution, and the guard."""

import pytest

import kindle_anki
from kindle_anki import Fatal, card_templates, resolve_layout


def test_definition_layout_prompts_with_definition():
    front, back = card_templates("definition")
    assert "{{Definition}}" in front and "{{Translation}}" not in front
    assert "{{Word}}" in back and "{{Translation}}" in back


def test_translation_layout_flips_definition_and_translation():
    front, back = card_templates("translation")
    # Native word prompts the front; the definition is revealed on the back.
    assert "{{Translation}}" in front and "{{Definition}}" not in front
    assert "{{Word}}" in back and "{{Definition}}" in back


def test_unknown_layout_falls_back_to_default():
    assert card_templates("bogus") == kindle_anki.LAYOUTS[kindle_anki.DEFAULT_LAYOUT]


def test_resolve_layout_cli_over_env(monkeypatch):
    monkeypatch.setenv("CARD_LAYOUT", "definition")
    assert resolve_layout("translation") == "translation"


def test_resolve_layout_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("CARD_LAYOUT", "translation")
    assert resolve_layout(None) == "translation"


def test_resolve_layout_default_when_unset(monkeypatch):
    monkeypatch.delenv("CARD_LAYOUT", raising=False)
    assert resolve_layout(None) == kindle_anki.DEFAULT_LAYOUT


def test_resolve_layout_rejects_unknown(monkeypatch):
    monkeypatch.delenv("CARD_LAYOUT", raising=False)
    with pytest.raises(Fatal):
        resolve_layout("beginner")


def test_genanki_model_carries_selected_layout():
    pytest.importorskip("genanki")
    model = kindle_anki.build_genanki_model("translation")
    assert "{{Translation}}" in model.templates[0]["qfmt"]


def _capture_anki(monkeypatch, model_exists):
    """Stub kindle_anki.anki, recording every call; report the model present."""
    calls = []

    def fake_anki(action, **params):
        calls.append((action, params))
        if action == "modelNames":
            return [kindle_anki.MODEL_NAME] if model_exists else []
        return None

    monkeypatch.setattr(kindle_anki, "anki", fake_anki)
    return calls


def test_ensure_model_relayout_repushes_templates_and_css(monkeypatch):
    calls = _capture_anki(monkeypatch, model_exists=True)
    kindle_anki.ensure_model("translation", relayout=True)
    actions = {action for action, _ in calls}
    assert {"updateModelTemplates", "updateModelStyling"} <= actions
    tmpl = next(p for a, p in calls if a == "updateModelTemplates")
    front = tmpl["model"]["templates"]["Production"]["Front"]
    assert "{{Translation}}" in front and "{{Definition}}" not in front


def test_ensure_model_leaves_existing_alone_without_relayout(monkeypatch):
    calls = _capture_anki(monkeypatch, model_exists=True)
    kindle_anki.ensure_model("translation")
    actions = {action for action, _ in calls}
    assert "updateModelTemplates" not in actions
    assert "updateModelStyling" not in actions
    assert "createModel" not in actions


def test_ensure_model_creates_when_absent_even_with_relayout(monkeypatch):
    calls = _capture_anki(monkeypatch, model_exists=False)
    kindle_anki.ensure_model("translation", relayout=True)
    actions = {action for action, _ in calls}
    assert "createModel" in actions
    assert "updateModelTemplates" not in actions
