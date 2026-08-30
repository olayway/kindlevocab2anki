"""build_notes: turn Claude's per-group response into Anki note payloads.

PLAN steps 6-7. Input: the stem, its group of new lookups, and Claude's
response {new_cards, assignments}. Output buckets: `notes` (one addNotes
payload per new_card), `existing` (lookup->existing-card links to record), and
`junk` (lookup->reason for skipped.json). Pure — no Anki call, no LLM.
"""

from kindle_anki import MODEL_NAME, Lookup, build_notes
from tests.conftest import EN

DECK = "English::Kindle"


def mklookup(id, stem="x", **kw):
    base = dict(word=stem, sentence="", title="", authors="", timestamp=0)
    base.update(kw)
    return Lookup(id=id, stem=stem, **base)


def test_new_verdict_builds_one_note_with_all_fields():
    lookups = [
        Lookup(
            id="L1",
            stem="afflict",
            word="afflicted",
            sentence="Diseases that afflict us.",
            title="Zebras",
            authors="Sapolsky",
            timestamp=1000,
        )
    ]
    response = {
        "stem": "afflict",
        "new_cards": [
            {
                "headword": "afflict",
                "definition": "to cause suffering to",
                "span": "afflict",
            }
        ],
        "assignments": [
            {"lookup_id": "L1", "verdict": "new", "card_index": 0, "reason": ""}
        ],
    }

    result = build_notes("afflict", lookups, response, DECK, EN)

    assert len(result.notes) == 1
    note = result.notes[0]
    assert note["deckName"] == DECK
    assert note["modelName"] == MODEL_NAME
    # No translation by default — the Translation field is absent entirely.
    assert note["fields"] == {
        "Stem": "afflict",
        "Word": "afflict",
        "Definition": "to cause suffering to",
        "Sentence": "Diseases that _____ us.",
        "SentenceFull": 'Diseases that <b class="target">afflict</b> us.',
        "Source": "Zebras — Sapolsky",
        "LookupDate": "1970-01-01",
        "Lookups": "L1",
    }
    assert result.existing == []
    assert result.junk == []


def test_translate_true_adds_translation_field():
    lookups = [mklookup("L1", stem="afflict", sentence="Diseases that afflict us.")]
    response = {
        "stem": "afflict",
        "new_cards": [
            {
                "headword": "afflict",
                "definition": "to cause suffering to",
                "translation": "trapić",
                "span": "afflict",
            }
        ],
        "assignments": [
            {"lookup_id": "L1", "verdict": "new", "card_index": 0, "reason": ""}
        ],
    }

    result = build_notes("afflict", lookups, response, DECK, EN, translate=True)

    assert result.notes[0]["fields"]["Translation"] == "trapić"


def test_shared_card_joins_ids_and_primary_is_earliest():
    # L1 is listed first but L2 is older -> L2 is the primary (source/date/
    # sentence), yet both ids are recorded in Lookups in assignment order.
    lookups = [
        Lookup(
            id="L1",
            stem="bank",
            word="bank",
            sentence="He sat by the bank later.",
            title="Later Book",
            authors="B",
            timestamp=5000,
        ),
        Lookup(
            id="L2",
            stem="bank",
            word="banks",
            sentence="The river banks flooded.",
            title="Earlier Book",
            authors="A",
            timestamp=2000,
        ),
    ]
    response = {
        "stem": "bank",
        "new_cards": [
            {
                "headword": "bank",
                "definition": "land alongside a river",
                "translation": "brzeg",
                "span": "banks",
            }
        ],
        "assignments": [
            {"lookup_id": "L1", "verdict": "new", "card_index": 0, "reason": ""},
            {"lookup_id": "L2", "verdict": "new", "card_index": 0, "reason": ""},
        ],
    }

    result = build_notes("bank", lookups, response, DECK, EN)

    assert len(result.notes) == 1
    fields = result.notes[0]["fields"]
    assert fields["Lookups"] == "L1,L2"
    assert fields["Source"] == "Earlier Book — A"
    assert fields["Sentence"] == "The river _____ flooded."
    assert fields["SentenceFull"] == 'The river <b class="target">banks</b> flooded.'


def test_junk_verdict_records_reason_and_makes_no_note():
    lookups = [mklookup("L1", stem="winston")]
    response = {
        "stem": "winston",
        "new_cards": [],
        "assignments": [
            {
                "lookup_id": "L1",
                "verdict": "junk",
                "card_index": None,
                "reason": "proper noun",
            }
        ],
    }

    result = build_notes("winston", lookups, response, DECK, EN)

    assert result.notes == []
    assert result.junk == [{"lookup_id": "L1", "reason": "proper noun"}]
    assert result.existing == []


def test_out_of_range_card_index_is_skipped_not_crashed(capsys):
    # A "new" verdict pointing past the end of new_cards must not crash the
    # batch (IndexError). It is skipped with a warning; a valid card in the same
    # response still builds, and the skipped lookup goes unrecorded (retried).
    lookups = [
        mklookup("L1", stem="afflict", sentence="Diseases that afflict us."),
        mklookup("L2", stem="afflict", sentence="It afflicts many."),
    ]
    response = {
        "stem": "afflict",
        "new_cards": [
            {"headword": "afflict", "definition": "to cause suffering to", "span": "afflict"}
        ],
        "assignments": [
            {"lookup_id": "L1", "verdict": "new", "card_index": 0, "reason": ""},
            # card_index 5 does not exist — only index 0 was emitted.
            {"lookup_id": "L2", "verdict": "new", "card_index": 5, "reason": ""},
        ],
    }

    result = build_notes("afflict", lookups, response, DECK, EN)

    # Only the valid card built; the bad one was dropped, not crashed.
    assert len(result.notes) == 1
    assert result.notes[0]["fields"]["Lookups"] == "L1"
    assert result.existing == []
    assert result.junk == []
    assert "bad card_index 5" in capsys.readouterr().out


def test_non_int_card_index_is_skipped_not_crashed(capsys):
    # A non-int card_index on a "new" verdict must not raise TypeError on the
    # bounds comparison — it is treated as bad and skipped.
    lookups = [mklookup("L1", stem="afflict", sentence="Diseases that afflict us.")]
    response = {
        "stem": "afflict",
        "new_cards": [
            {"headword": "afflict", "definition": "to cause suffering to", "span": "afflict"}
        ],
        "assignments": [
            {"lookup_id": "L1", "verdict": "new", "card_index": None, "reason": ""}
        ],
    }

    result = build_notes("afflict", lookups, response, DECK, EN)

    assert result.notes == []
    assert "bad card_index" in capsys.readouterr().out


def test_existing_verdict_records_link_and_makes_no_note():
    lookups = [mklookup("L1", stem="make")]
    response = {
        "stem": "make",
        "new_cards": [],
        "assignments": [
            {
                "lookup_id": "L1",
                "verdict": "existing",
                "card_index": 2,
                "reason": "same sense as existing card",
            }
        ],
    }

    result = build_notes("make", lookups, response, DECK, EN)

    assert result.notes == []
    assert result.existing == [{"card_index": 2, "lookup_id": "L1"}]
    assert result.junk == []
