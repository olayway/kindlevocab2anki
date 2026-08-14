"""build_existing_index: group existing cards by stem for dedup context.

Each entry keeps the `note_id` (so an `existing` verdict can later append to
that card's Lookups) and its current `lookups` value; the list position is the
`index` we hand to Claude. Pure over already-fetched notesInfo.
"""

from kindle_anki import build_existing_index


def note(note_id, stem, word, definition, lookups=""):
    return {
        "noteId": note_id,
        "fields": {
            "Stem": {"value": stem},
            "Word": {"value": word},
            "Definition": {"value": definition},
            "Lookups": {"value": lookups},
        },
    }


def test_groups_by_stem_preserving_order_and_note_ids():
    notes_info = [
        note(11, "bank", "bank", "raised ground beside a river", "L1"),
        note(12, "bank", "bank", "place that holds money", "L2,L3"),
        note(13, "make", "make off", "leave hurriedly with something"),
    ]
    idx = build_existing_index(notes_info)

    assert set(idx.keys()) == {"bank", "make"}
    assert idx["bank"][0] == {
        "note_id": 11,
        "headword": "bank",
        "definition": "raised ground beside a river",
        "lookups": "L1",
    }
    assert idx["bank"][1]["definition"] == "place that holds money"
    assert idx["bank"][1]["lookups"] == "L2,L3"
    assert idx["make"][0]["headword"] == "make off"
