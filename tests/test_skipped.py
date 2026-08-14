"""skipped.json: junk lookup ids -> reason (PLAN decision 3). Real temp file."""

import kindle_anki


def test_load_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(kindle_anki, "SKIPPED_JSON", tmp_path / "skipped.json")
    assert kindle_anki.load_skipped() == {}


def test_save_then_load_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setattr(kindle_anki, "SKIPPED_JSON", tmp_path / "skipped.json")
    kindle_anki.save_skipped({"L1": "proper noun", "L2": "ocr artefact"})
    assert kindle_anki.load_skipped() == {"L1": "proper noun", "L2": "ocr artefact"}
