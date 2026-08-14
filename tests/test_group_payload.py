"""build_group_payload: shape one stem-group for the Claude request.

`existing` is stripped to what Claude needs (index/headword/definition) — the
note_id and lookups stay behind for the apply step.
"""

from kindle_anki import Lookup, build_group_payload


def test_shapes_contexts_and_indexed_existing():
    lookups = [
        Lookup(
            id="L1",
            stem="bank",
            word="banks",
            sentence="The river banks flooded.",
            title="A Book",
            authors="X",
            timestamp=5,
        )
    ]
    existing_index = {
        "bank": [
            {
                "note_id": 9,
                "headword": "bank",
                "definition": "place for money",
                "lookups": "L0",
            }
        ]
    }

    payload = build_group_payload("bank", lookups, existing_index)

    assert payload == {
        "stem": "bank",
        "contexts": [
            {
                "lookup_id": "L1",
                "sentence": "The river banks flooded.",
                "book": "A Book",
                "timestamp": 5,
            }
        ],
        "existing": [
            {"index": 0, "headword": "bank", "definition": "place for money"}
        ],
    }


def test_missing_stem_yields_empty_existing():
    lookups = [
        Lookup(id="L1", stem="new", word="new", sentence="a new thing",
               title="B", authors="", timestamp=1)
    ]
    payload = build_group_payload("new", lookups, existing_index={})
    assert payload["existing"] == []
