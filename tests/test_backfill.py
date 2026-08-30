"""backfill_full_sentences: populate SentenceFull on pre-existing cards.

Old cards store only the blanked Sentence, so the original is re-read from the
vocab.db lookup (by the card's primary Lookups id) and re-highlighted. Only
notes whose SentenceFull is empty are queried and written.
"""

import kindle_anki
from kindle_anki import Lookup, backfill_full_sentences
from tests.conftest import EN

DECK = "English::Kindle"


def _stub_anki(monkeypatch, notes):
    """Stub kindle_anki.anki: `notes` maps note_id -> Lookups value; records
    every updateNoteFields as (note_id, SentenceFull). findNotes returns all
    ids (all "empty"); notesInfo echoes them back with fields."""
    updates: list[tuple] = []

    def fake_anki(action, **params):
        if action == "findNotes":
            return list(notes)
        if action == "notesInfo":
            return [
                {"noteId": nid, "fields": {"Lookups": {"value": notes[nid]}}}
                for nid in params["notes"]
            ]
        if action == "updateNoteFields":
            note = params["note"]
            updates.append((note["id"], note["fields"]["SentenceFull"]))
            return None
        raise AssertionError(f"unexpected anki action {action!r}")

    monkeypatch.setattr(kindle_anki, "anki", fake_anki)
    return updates


def _lk(id, **kw):
    base = dict(stem="x", word="x", sentence="", title="", authors="", timestamp=0)
    base.update(kw)
    return Lookup(id=id, **base)


def test_backfills_highlighted_sentence_from_lookup(monkeypatch):
    updates = _stub_anki(monkeypatch, {10: "L1"})
    lookups = [_lk("L1", stem="afflict", word="afflict", sentence="Diseases that afflict us.")]

    backfill_full_sentences(DECK, lookups, EN)

    assert updates == [(10, 'Diseases that <b class="target">afflict</b> us.')]


def test_uses_primary_lookup_and_matches_inflection(monkeypatch):
    # Lookups holds several ids; the first is the primary. The stored word is
    # the lemma, but the sentence has it inflected -> the matcher expands it.
    updates = _stub_anki(monkeypatch, {10: "L1,L2"})
    lookups = [_lk("L1", stem="afflict", word="afflict", sentence="Diseases that afflicted us.")]

    backfill_full_sentences(DECK, lookups, EN)

    assert updates == [(10, 'Diseases that <b class="target">afflicted</b> us.')]


def test_skips_notes_whose_lookup_is_gone(monkeypatch):
    # Two empty notes; only one lookup survives in vocab.db -> the other is left
    # untouched rather than blanked or crashed.
    updates = _stub_anki(monkeypatch, {10: "L1", 11: "GONE"})
    lookups = [_lk("L1", stem="vile", word="vile", sentence="the vile wind")]

    backfill_full_sentences(DECK, lookups, EN)

    assert updates == [(10, 'the <b class="target">vile</b> wind')]


def test_no_empty_notes_writes_nothing(monkeypatch):
    def fake_anki(action, **params):
        if action == "findNotes":
            return []
        raise AssertionError(f"should not call {action!r} when nothing is empty")

    monkeypatch.setattr(kindle_anki, "anki", fake_anki)
    backfill_full_sentences(DECK, [], EN)
