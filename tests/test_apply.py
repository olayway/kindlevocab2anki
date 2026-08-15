"""apply_new_cards: the write-side orchestration (PLAN steps 5-8).

Both external seams are stubbed here — `cluster_groups` (LLM, covered live by
the -m llm evals) and `anki` (HTTP). This test pins the wiring: new cards are
added, existing verdicts append to the matched note, junk lands in skipped.
"""

import kindle_anki
from kindle_anki import Lookup, apply_new_cards


def lk(id, stem, sentence, ts):
    return Lookup(
        id=id, stem=stem, word=stem, sentence=sentence, title="T", authors="", timestamp=ts
    )


def test_writes_new_records_existing_and_collects_junk(tmp_path, monkeypatch):
    monkeypatch.setattr(kindle_anki, "SKIPPED_JSON", tmp_path / "skipped.json")

    new_lookups = [
        lk("L1", "bank", "We sat on the river bank.", 1),
        lk("L2", "winston", "Winston left the room.", 2),
        lk("L3", "make", "Please make the bed.", 3),
    ]
    existing_index = {
        "make": [
            {"note_id": 99, "headword": "make", "definition": "create", "lookups": "L0"}
        ]
    }
    canned = {
        "bank": {
            "stem": "bank",
            "new_cards": [
                {"headword": "bank", "definition": "river edge", "translation": "brzeg", "span": "bank"}
            ],
            "assignments": [
                {"lookup_id": "L1", "verdict": "new", "card_index": 0, "reason": ""}
            ],
        },
        "winston": {
            "stem": "winston",
            "new_cards": [],
            "assignments": [
                {"lookup_id": "L2", "verdict": "junk", "card_index": -1, "reason": "proper noun"}
            ],
        },
        "make": {
            "stem": "make",
            "new_cards": [],
            "assignments": [
                {"lookup_id": "L3", "verdict": "existing", "card_index": 0, "reason": "same"}
            ],
        },
    }

    calls = []

    def fake_cluster(client, model, payloads, language=None):
        assert [p["stem"] for p in payloads] == ["bank", "winston", "make"]
        assert language is None  # no --language -> monolingual
        return {p["stem"].lower(): canned[p["stem"].lower()] for p in payloads}

    def fake_anki(action, **params):
        calls.append((action, params))
        if action == "addNotes":
            return list(range(len(params["notes"])))
        return None

    monkeypatch.setattr(kindle_anki, "cluster_groups", fake_cluster)
    monkeypatch.setattr(kindle_anki, "anki", fake_anki)

    skipped = {}
    added, updated, junked = apply_new_cards(
        client=None,
        model="m",
        new_lookups=new_lookups,
        existing_index=existing_index,
        skipped=skipped,
    )

    assert (added, updated, junked) == (1, 1, 1)

    add_calls = [p for a, p in calls if a == "addNotes"]
    assert len(add_calls) == 1
    assert add_calls[0]["notes"][0]["fields"]["Word"] == "bank"
    assert add_calls[0]["notes"][0]["fields"]["Lookups"] == "L1"

    upd = [p for a, p in calls if a == "updateNoteFields"]
    assert upd[0]["note"] == {"id": 99, "fields": {"Lookups": "L0,L3"}}

    assert skipped == {"L2": "proper noun"}
    # persisted to the (temp) skipped.json
    assert kindle_anki.load_skipped() == {"L2": "proper noun"}


def test_language_is_threaded_and_translation_written(tmp_path, monkeypatch):
    monkeypatch.setattr(kindle_anki, "SKIPPED_JSON", tmp_path / "skipped.json")

    canned = {
        "bank": {
            "stem": "bank",
            "new_cards": [
                {"headword": "bank", "definition": "river edge", "translation": "rive", "span": "bank"}
            ],
            "assignments": [
                {"lookup_id": "L1", "verdict": "new", "card_index": 0, "reason": ""}
            ],
        }
    }
    calls = []

    def fake_cluster(client, model, payloads, language=None):
        assert language == "French"  # --language French threaded through
        return {p["stem"].lower(): canned[p["stem"].lower()] for p in payloads}

    def fake_anki(action, **params):
        calls.append((action, params))
        if action == "addNotes":
            return list(range(len(params["notes"])))
        return None

    monkeypatch.setattr(kindle_anki, "cluster_groups", fake_cluster)
    monkeypatch.setattr(kindle_anki, "anki", fake_anki)

    apply_new_cards(
        client=None,
        model="m",
        new_lookups=[lk("L1", "bank", "We sat on the river bank.", 1)],
        existing_index={},
        skipped={},
        language="French",
    )

    note = [p for a, p in calls if a == "addNotes"][0]["notes"][0]
    assert note["fields"]["Translation"] == "rive"
