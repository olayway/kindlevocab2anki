"""backfill_production: generate production pairs for the back catalogue —
notes whose ProdNative1 is empty. Idempotent (only empty notes queried),
resumable, respects --limit. The Claude call is stubbed via
generate_production_pairs; the AnkiConnect funnel is stubbed via anki().
"""

import pytest

import kindle_anki
from kindle_anki import (
    PRODUCTION_PAIRS,
    backfill_production,
    backfill_production_prompt,
    backfill_production_schema,
    production_array_schema,
)
from tests.conftest import EN

DECK = "English::Kindle"


def _note(nid, *, word="afflict", definition="to trouble", sentence="Diseases afflict us."):
    fields = {
        "Word": {"value": word},
        "Definition": {"value": definition},
        "SentenceFull": {"value": sentence},
        "Sentence": {"value": ""},
    }
    return {"noteId": nid, "fields": fields}


def _stub_anki(monkeypatch, notes):
    """findNotes returns every note id (all treated as empty); notesInfo echoes
    the given note dicts; updateNoteFields is recorded as (note_id, fields)."""
    updates: list[tuple] = []

    def fake_anki(action, **params):
        if action == "findNotes":
            return [n["noteId"] for n in notes]
        if action == "notesInfo":
            return notes
        if action == "updateNoteFields":
            note = params["note"]
            updates.append((note["id"], note["fields"]))
            return None
        raise AssertionError(f"unexpected anki action {action!r}")

    monkeypatch.setattr(kindle_anki, "anki", fake_anki)
    return updates


def _stub_generate(monkeypatch, capture=None):
    """Stub generate_production_pairs: return PRODUCTION_PAIRS canned pairs per
    card id. If `capture` is a list, record each batch of cards it was called
    with, so batching/limit can be asserted."""

    def fake_generate(client, model, cards, learning, language, level, effort=None):
        if capture is not None:
            capture.append(cards)
        return {
            c["id"]: [
                {"native": f'n{i}-{c["id"]}', "target": f't{i}-{c["id"]}'}
                for i in range(1, PRODUCTION_PAIRS + 1)
            ]
            for c in cards
        }

    monkeypatch.setattr(kindle_anki, "generate_production_pairs", fake_generate)


def test_fills_all_six_pair_fields(monkeypatch):
    updates = _stub_anki(monkeypatch, [_note(10)])
    _stub_generate(monkeypatch)

    backfill_production(None, "m", DECK, EN, "Polish", "B1")

    assert len(updates) == 1
    nid, fields = updates[0]
    assert nid == 10
    assert fields["ProdNative1"] == "n1-10" and fields["ProdTarget1"] == "t1-10"
    assert fields["ProdNative3"] == "n3-10" and fields["ProdTarget3"] == "t3-10"


def test_skips_notes_missing_word_or_definition(monkeypatch):
    notes = [_note(10), _note(11, definition=""), _note(12, word="")]
    updates = _stub_anki(monkeypatch, notes)
    _stub_generate(monkeypatch)

    backfill_production(None, "m", DECK, EN, "Polish", "B1")

    assert [nid for nid, _ in updates] == [10]  # 11 and 12 are ungroundable


def test_no_empty_notes_calls_nothing(monkeypatch):
    def fake_anki(action, **params):
        if action == "findNotes":
            return []
        raise AssertionError(f"should not call {action!r} when nothing is empty")

    monkeypatch.setattr(kindle_anki, "anki", fake_anki)
    # generate must never be reached either.
    _stub_generate(monkeypatch)
    backfill_production(None, "m", DECK, EN, "Polish", "B1")


def test_respects_limit(monkeypatch):
    notes = [_note(n) for n in range(10, 20)]  # 10 empty notes
    updates = _stub_anki(monkeypatch, notes)
    _stub_generate(monkeypatch)

    backfill_production(None, "m", DECK, EN, "Polish", "B1", limit=3)

    assert len(updates) == 3


def test_batches_by_batch_size(monkeypatch):
    notes = [_note(n) for n in range(10, 15)]  # 5 notes
    _stub_anki(monkeypatch, notes)
    batches: list = []
    _stub_generate(monkeypatch, capture=batches)

    backfill_production(None, "m", DECK, EN, "Polish", "B1", batch_size=2)

    assert [len(b) for b in batches] == [2, 2, 1]


def test_tolerates_short_pair_list_from_model(monkeypatch):
    updates = _stub_anki(monkeypatch, [_note(10)])

    def short_generate(client, model, cards, learning, language, level, effort=None):
        return {cards[0]["id"]: [{"native": "one", "target": "uno"}]}  # only 1 pair

    monkeypatch.setattr(kindle_anki, "generate_production_pairs", short_generate)
    backfill_production(None, "m", DECK, EN, "Polish", "B1")

    _, fields = updates[0]
    assert fields["ProdNative1"] == "one" and fields["ProdTarget1"] == "uno"
    assert fields["ProdNative2"] == "" and fields["ProdTarget3"] == ""


def test_dropped_card_is_left_for_a_later_run(monkeypatch):
    # The model omits a card from its response → we write nothing for it, so the
    # next run (which re-queries empty notes) retries it.
    updates = _stub_anki(monkeypatch, [_note(10), _note(11)])

    def partial_generate(client, model, cards, learning, language, level, effort=None):
        return {"10": [{"native": "n", "target": "t"}] * PRODUCTION_PAIRS}  # 11 dropped

    monkeypatch.setattr(kindle_anki, "generate_production_pairs", partial_generate)
    backfill_production(None, "m", DECK, EN, "Polish", "B1")

    assert [nid for nid, _ in updates] == [10]


def test_falls_back_to_blanked_sentence_for_grounding(monkeypatch):
    # SentenceFull empty but Sentence present → the card still grounds (we don't
    # skip it), and the fallback sentence reaches the model.
    note = _note(10, sentence="")
    note["fields"]["Sentence"] = {"value": "Diseases _____ us."}
    _stub_anki(monkeypatch, [note])
    seen: list = []
    _stub_generate(monkeypatch, capture=seen)

    backfill_production(None, "m", DECK, EN, "Polish", "B1")

    assert seen[0][0]["sentence"] == "Diseases _____ us."


# --- prompt / schema helpers ---------------------------------------------


def test_prompt_carries_focus_target_and_level():
    prompt = backfill_production_prompt(EN, "Polish", "B1")
    assert 'class="focus"' in prompt and 'class="target"' in prompt
    assert "B1" in prompt and "Polish" in prompt


def test_schema_pair_shape_and_no_unsupported_bounds():
    # The structured-output format rejects array minItems/maxItems > 1, so the
    # count lives in the prompt, not the schema. The pair shape is still pinned.
    arr = production_array_schema()
    assert "minItems" not in arr and "maxItems" not in arr
    assert arr["items"]["required"] == ["native", "target"]
    card = backfill_production_schema()["properties"]["cards"]["items"]
    assert card["required"] == ["id", "production"]
