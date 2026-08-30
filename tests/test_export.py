"""Offline --export path: deck_state.json as the source of truth + .apkg output.

The state file stands in for the live deck, so the SAME read-side logic
(consumed_ids / build_existing_index / determine_new_lookups) must see cards
through state_to_notes_info, and apply_new_cards must reach the deck only via
an ApkgSink — never AnkiConnect. The genanki write is exercised separately and
skipped when genanki isn't installed (run it with `--with genanki`).
"""

import zipfile

import pytest

import kindle_anki
from tests.conftest import EN
from kindle_anki import (
    ApkgSink,
    Lookup,
    apply_new_cards,
    build_existing_index,
    card_id_of,
    consumed_ids,
    determine_new_lookups,
    load_state,
    save_state,
    state_to_notes_info,
)

DECK = "English::Kindle"


def lk(id, stem, sentence, ts):
    return Lookup(
        id=id, stem=stem, word=stem, sentence=sentence, title="T", authors="", timestamp=ts
    )


def card(card_id, stem, lookups, word=None, definition=""):
    return {
        "card_id": card_id,
        "fields": {"Stem": stem, "Word": word or stem, "Definition": definition,
                   "Lookups": lookups},
        "tags": ["kindle"],
    }


# -- state file -----------------------------------------------------------


def test_state_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setattr(kindle_anki, "STATE_JSON", tmp_path / "deck_state.json")
    assert load_state() == []
    cards = [card("L1", "bank", "L1")]
    save_state(cards)
    assert load_state() == cards


def test_load_state_rejects_non_array(tmp_path, monkeypatch):
    path = tmp_path / "deck_state.json"
    path.write_text('{"not": "an array"}')
    monkeypatch.setattr(kindle_anki, "STATE_JSON", path)
    with pytest.raises(kindle_anki.Fatal):
        load_state()


def test_card_id_is_first_lookup(tmp_path):
    note = {"fields": {"Lookups": "L7,L2,L9"}}
    assert card_id_of(note) == "L7"


# -- the state <-> read-side adapter --------------------------------------


def test_state_feeds_the_same_read_side_logic():
    state = [
        card("L1", "bank", "L1,L9", word="bank", definition="river edge"),
        card("L2", "make off", "L2", word="make off", definition="flee with something"),
    ]
    notes_info = state_to_notes_info(state)

    # consumed ids come from the Lookups field, exactly as with a live deck
    assert consumed_ids(notes_info) == {"L1", "L9", "L2"}

    # existing_index groups by stem, with card_id standing in for noteId
    idx = build_existing_index(notes_info)
    assert idx["bank"][0] == {
        "note_id": "L1",
        "headword": "bank",
        "definition": "river edge",
        "lookups": "L1,L9",
    }

    # a lookup already consumed by a card is not "new" again
    assert determine_new_lookups([lk("L9", "bank", "...", 1)], notes_info, set()) == []
    assert determine_new_lookups([lk("L5", "bank", "...", 1)], notes_info, set()) == [
        lk("L5", "bank", "...", 1)
    ]


# -- ApkgSink orchestration (genanki write stubbed) -----------------------


def _canned_cluster(canned):
    def fake(client, model, payloads, learning=None, language=None, level=None):
        return {p["stem"].lower(): canned[p["stem"].lower()] for p in payloads}

    return fake


def test_apply_offline_persists_state_and_then_dedups(tmp_path, monkeypatch):
    monkeypatch.setattr(kindle_anki, "STATE_JSON", tmp_path / "deck_state.json")
    monkeypatch.setattr(kindle_anki, "SKIPPED_JSON", tmp_path / "skipped.json")
    written = {}
    monkeypatch.setattr(kindle_anki, "write_apkg", lambda out, notes, deck_name=None, layout=None: written.update(out=out, n=len(notes)))
    monkeypatch.setattr(
        kindle_anki,
        "cluster_groups",
        _canned_cluster(
            {
                "bank": {
                    "stem": "bank",
                    "new_cards": [{"headword": "bank", "definition": "river edge", "span": "bank"}],
                    "assignments": [{"lookup_id": "L1", "verdict": "new", "card_index": 0, "reason": ""}],
                }
            }
        ),
    )

    state: list[dict] = []
    sink = ApkgSink(tmp_path / "deck.apkg", state, DECK)
    added, updated, junked = apply_new_cards(
        client=None,
        model="m",
        new_lookups=[lk("L1", "bank", "We sat on the river bank.", 1)],
        existing_index={},
        skipped={},
        learning=EN,
        deck_name=DECK,
        sink=sink,
    )
    assert (added, updated, junked) == (1, 0, 0)

    # finalize wrote the cumulative snapshot and persisted state
    assert written == {"out": tmp_path / "deck.apkg", "n": 1}
    persisted = load_state()
    assert len(persisted) == 1
    assert persisted[0]["card_id"] == "L1"
    assert persisted[0]["fields"]["Word"] == "bank"
    assert persisted[0]["fields"]["Sentence"] == "We sat on the river _____."

    # a fresh run seeing the same lookup treats it as already handled
    notes_info = state_to_notes_info(persisted)
    assert determine_new_lookups(
        [lk("L1", "bank", "We sat on the river bank.", 1)], notes_info, set()
    ) == []


def test_apply_offline_links_lookup_to_existing_card(tmp_path, monkeypatch):
    monkeypatch.setattr(kindle_anki, "STATE_JSON", tmp_path / "deck_state.json")
    monkeypatch.setattr(kindle_anki, "SKIPPED_JSON", tmp_path / "skipped.json")
    monkeypatch.setattr(kindle_anki, "write_apkg", lambda out, notes, deck_name=None, layout=None: None)
    monkeypatch.setattr(
        kindle_anki,
        "cluster_groups",
        _canned_cluster(
            {
                "make": {
                    "stem": "make",
                    "new_cards": [],
                    "assignments": [{"lookup_id": "L3", "verdict": "existing", "card_index": 0, "reason": "same"}],
                }
            }
        ),
    )

    state = [card("L0", "make", "L0", word="make", definition="create")]
    existing_index = build_existing_index(state_to_notes_info(state))
    sink = ApkgSink(tmp_path / "deck.apkg", state, DECK)

    added, updated, junked = apply_new_cards(
        client=None,
        model="m",
        new_lookups=[lk("L3", "make", "Please make the bed.", 3)],
        existing_index=existing_index,
        skipped={},
        learning=EN,
        deck_name=DECK,
        sink=sink,
    )
    assert (added, updated, junked) == (0, 1, 0)
    assert load_state()[0]["fields"]["Lookups"] == "L0,L3"


# -- real genanki write ---------------------------------------------------


def test_write_apkg_emits_a_zip_backed_package(tmp_path):
    pytest.importorskip("genanki")
    records = [
        {
            "card_id": "L1",
            "fields": {
                "Stem": "bank", "Word": "bank", "Translation": "",
                "Definition": "river edge", "Sentence": "We sat on the _____.",
                "Source": "A Book", "LookupDate": "2020-01-01", "Lookups": "L1",
            },
            "tags": ["kindle", "book::a-book"],
        }
    ]
    out = tmp_path / "deck.apkg"
    kindle_anki.write_apkg(out, records, DECK)
    assert out.exists() and out.stat().st_size > 0
    assert zipfile.is_zipfile(out)  # .apkg is a zip of the sqlite collection
