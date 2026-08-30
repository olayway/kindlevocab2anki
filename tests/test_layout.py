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


def test_resolve_layout_uses_cli_value():
    assert resolve_layout("translation") == "translation"


def test_resolve_layout_default_when_unset():
    assert resolve_layout(None) == kindle_anki.DEFAULT_LAYOUT


def test_resolve_layout_rejects_unknown():
    with pytest.raises(Fatal):
        resolve_layout("beginner")


def test_genanki_model_carries_selected_layout():
    pytest.importorskip("genanki")
    model = kindle_anki.build_genanki_model("translation")
    assert "{{Translation}}" in model.templates[0]["qfmt"]


def _capture_anki(monkeypatch, model_exists, templates=None):
    """Stub kindle_anki.anki, recording every call; report the model present and
    (optionally) its existing card templates so migration branches can be driven.
    """
    calls = []

    def fake_anki(action, **params):
        calls.append((action, params))
        if action == "modelNames":
            return [kindle_anki.MODEL_NAME] if model_exists else []
        if action == "modelTemplates":
            return templates
        return None

    monkeypatch.setattr(kindle_anki, "anki", fake_anki)
    return calls


def test_ensure_model_relayout_repushes_templates_and_css(monkeypatch):
    calls = _capture_anki(monkeypatch, model_exists=True)
    kindle_anki.ensure_model("translation", relayout=True)
    actions = {action for action, _ in calls}
    assert {"updateModelTemplates", "updateModelStyling"} <= actions
    tmpl = next(p for a, p in calls if a == "updateModelTemplates")
    templates = tmpl["model"]["templates"]
    # relayout re-pushes the recognition template under its (renamed) key...
    front = templates[kindle_anki.RECOGNITION_TEMPLATE]["Front"]
    assert "{{Translation}}" in front and "{{Definition}}" not in front
    # ...and the production template too, so its edits reach an existing deck.
    assert "{{ProdNative1}}" in templates[kindle_anki.PRODUCTION_TEMPLATE]["Front"]


def test_ensure_model_leaves_layout_alone_without_relayout(monkeypatch):
    # Without relayout the recognition templates and CSS are not re-pushed —
    # the user's card edits survive. (The managed Production add still runs.)
    calls = _capture_anki(monkeypatch, model_exists=True)
    kindle_anki.ensure_model("translation")
    actions = {action for action, _ in calls}
    assert "updateModelTemplates" not in actions
    assert "updateModelStyling" not in actions
    assert "createModel" not in actions


def test_ensure_model_creates_both_templates_when_absent(monkeypatch):
    calls = _capture_anki(monkeypatch, model_exists=False)
    kindle_anki.ensure_model("translation", relayout=True)
    actions = {action for action, _ in calls}
    assert "createModel" in actions
    assert "updateModelTemplates" not in actions
    create = next(p for a, p in calls if a == "createModel")
    names = [t["Name"] for t in create["cardTemplates"]]
    # Recognition first (ordinal 0, matching legacy decks), production second.
    assert names == [kindle_anki.RECOGNITION_TEMPLATE, kindle_anki.PRODUCTION_TEMPLATE]
    prod = next(t for t in create["cardTemplates"] if t["Name"] == kindle_anki.PRODUCTION_TEMPLATE)
    assert "{{ProdNative1}}" in prod["Front"] and "{{ProdTarget1}}" in prod["Back"]


def test_ensure_model_migrates_legacy_production_template(monkeypatch):
    # A note type predating this change has one template named "Production" that
    # is really the recognition card. It must be renamed in place, and the real
    # production card added under the freed "Production" name.
    calls = _capture_anki(
        monkeypatch, model_exists=True, templates={"Production": {"Front": "", "Back": ""}}
    )
    kindle_anki.ensure_model()
    rename = next(p for a, p in calls if a == "modelTemplateRename")
    assert rename["oldTemplateName"] == "Production"
    assert rename["newTemplateName"] == kindle_anki.RECOGNITION_TEMPLATE
    add = next(p for a, p in calls if a == "modelTemplateAdd")
    assert add["template"]["Name"] == kindle_anki.PRODUCTION_TEMPLATE
    assert "{{ProdNative1}}" in add["template"]["Front"]


def test_ensure_model_migration_is_idempotent(monkeypatch):
    # Already migrated: both templates present. No rename, no re-add.
    calls = _capture_anki(
        monkeypatch,
        model_exists=True,
        templates={
            kindle_anki.RECOGNITION_TEMPLATE: {"Front": "", "Back": ""},
            kindle_anki.PRODUCTION_TEMPLATE: {"Front": "", "Back": ""},
        },
    )
    kindle_anki.ensure_model()
    actions = {action for action, _ in calls}
    assert "modelTemplateRename" not in actions
    assert "modelTemplateAdd" not in actions


def test_ensure_model_adds_production_to_recognition_only_note_type(monkeypatch):
    # A note type already renamed to "Recognition" but missing the production
    # card (e.g. partial migration) gains it without a rename.
    calls = _capture_anki(
        monkeypatch,
        model_exists=True,
        templates={kindle_anki.RECOGNITION_TEMPLATE: {"Front": "", "Back": ""}},
    )
    kindle_anki.ensure_model()
    actions = {action for action, _ in calls}
    assert "modelTemplateRename" not in actions
    add = next(p for a, p in calls if a == "modelTemplateAdd")
    assert add["template"]["Name"] == kindle_anki.PRODUCTION_TEMPLATE
