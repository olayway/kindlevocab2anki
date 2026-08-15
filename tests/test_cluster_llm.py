"""Real-LLM behavioral evals for the clustering step (PLAN prompt rewrite).

Marked `llm` -> deselected by default. Run with:
    uv run --with pytest --with anthropic pytest -m llm

Assertions are deliberately loose (verdict, substring, count) so they survive
LLM nondeterminism while still catching a prompt/schema regression.
"""

import pytest

from kindle_anki import cluster_groups
from tests.conftest import CHEAP_MODEL

pytestmark = pytest.mark.llm


def one_context(lookup_id, sentence, book="Some Book", timestamp=1):
    return {
        "lookup_id": lookup_id,
        "sentence": sentence,
        "book": book,
        "timestamp": timestamp,
    }


def test_proper_noun_is_junk(claude):
    groups = [
        {
            "stem": "winston",
            "contexts": [
                one_context("L1", "Winston walked to the Ministry of Truth.")
            ],
            "existing": [],
        }
    ]
    out = cluster_groups(claude, CHEAP_MODEL, groups)

    assignments = out["winston"]["assignments"]
    assert len(assignments) == 1
    assert assignments[0]["lookup_id"] == "L1"
    assert assignments[0]["verdict"] == "junk"
    assert out["winston"]["new_cards"] == []


def test_language_populates_translation_field(claude):
    groups = [
        {
            "stem": "afflict",
            "contexts": [
                one_context("L1", "The diseases that afflict us have changed.")
            ],
            "existing": [],
        }
    ]
    out = cluster_groups(claude, CHEAP_MODEL, groups, language="French")

    card = out["afflict"]["new_cards"][0]
    assert card["translation"].strip(), "translation must be present and non-empty"


def test_no_language_omits_translation_field(claude):
    groups = [
        {
            "stem": "afflict",
            "contexts": [
                one_context("L1", "The diseases that afflict us have changed.")
            ],
            "existing": [],
        }
    ]
    out = cluster_groups(claude, CHEAP_MODEL, groups)  # no language

    card = out["afflict"]["new_cards"][0]
    assert "translation" not in card


def test_polysemy_splits_into_two_cards_with_verbatim_spans(claude):
    contexts = [
        one_context("L1", "We sat on the grassy bank of the river."),
        one_context("L2", "She deposited the cheque at the bank downtown."),
    ]
    groups = [{"stem": "bank", "contexts": contexts, "existing": []}]
    out = cluster_groups(claude, CHEAP_MODEL, groups)

    g = out["bank"]
    assert len(g["new_cards"]) == 2
    by_id = {a["lookup_id"]: a for a in g["assignments"]}
    assert by_id["L1"]["verdict"] == "new"
    assert by_id["L2"]["verdict"] == "new"
    # Distinct senses -> distinct cards.
    assert by_id["L1"]["card_index"] != by_id["L2"]["card_index"]
    # Every span must be copied verbatim from one of the sentences.
    sentences = " ".join(c["sentence"] for c in contexts)
    for card in g["new_cards"]:
        assert card["span"], "span must not be empty"
        for piece in card["span"]:
            assert piece in sentences


def test_distinct_sense_from_existing_card_becomes_new(claude):
    # Existing card covers the "morally wrong" sense; the new lookup is the
    # unrelated "dirty/squalid" sense, so it must not collapse into `existing`.
    groups = [
        {
            "stem": "sordid",
            "contexts": [
                one_context(
                    "L1",
                    "There are lots of really sordid apartments in the "
                    "city's poorer areas.",
                )
            ],
            "existing": [
                {
                    "index": 0,
                    "headword": "sordid",
                    "definition": "morally wrong and shocking",
                }
            ],
        }
    ]
    out = cluster_groups(claude, CHEAP_MODEL, groups)

    g = out["sordid"]
    a = g["assignments"][0]
    assert a["lookup_id"] == "L1"
    assert a["verdict"] == "new"
    assert len(g["new_cards"]) == 1


def test_distinct_sense_from_existing_phrasal_becomes_new(claude):
    # Existing card is the phrasal expression "follow about" (stem "follow").
    # A bare "follow" meaning "understand" is a different sense, so it must be
    # a new card, not collapsed into the phrasal card that shares the stem.
    groups = [
        {
            "stem": "follow",
            "contexts": [
                one_context("L1", "Sorry, I don't follow your argument at all.")
            ],
            "existing": [
                {
                    "index": 0,
                    "headword": "follow about",
                    "definition": "to keep moving around a place close behind "
                    "someone, trailing them persistently",
                }
            ],
        }
    ]
    out = cluster_groups(claude, CHEAP_MODEL, groups)

    g = out["follow"]
    a = g["assignments"][0]
    assert a["lookup_id"] == "L1"
    assert a["verdict"] == "new"
    assert len(g["new_cards"]) == 1


def test_synonym_with_different_headword_becomes_new(claude):
    # Two different single words that mean almost the same thing ("couch" vs the
    # existing "sofa") are still different headwords, so the new lookup must be
    # its own card — an "existing" match needs the SAME word, not just the same
    # meaning. (The pipeline also never offers this cross-stem, but the model
    # must not merge on sense alone even when it is offered.)
    groups = [
        {
            "stem": "couch",
            "contexts": [
                one_context(
                    "L1", "She stretched out on the couch and fell asleep."
                )
            ],
            "existing": [
                {
                    "index": 0,
                    "headword": "sofa",
                    "definition": "a long upholstered seat for two or more people",
                }
            ],
        }
    ]
    out = cluster_groups(claude, CHEAP_MODEL, groups)

    g = out["couch"]
    a = g["assignments"][0]
    assert a["lookup_id"] == "L1"
    assert a["verdict"] == "new"
    assert len(g["new_cards"]) == 1
    assert g["new_cards"][a["card_index"]]["headword"].lower() == "couch"


def test_bare_stem_not_merged_into_existing_phrasal(claude):
    # A plain "follow" (go after) shares the stem with the existing "follow
    # about" card and the senses are adjacent, but the headword differs — a bare
    # stem is not the same card as a multi-word expression. It must be "new".
    groups = [
        {
            "stem": "follow",
            "contexts": [
                one_context(
                    "L1", "The dog began to follow the postman down the lane."
                )
            ],
            "existing": [
                {
                    "index": 0,
                    "headword": "follow about",
                    "definition": "to keep moving around a place close behind "
                    "someone, trailing them persistently",
                }
            ],
        }
    ]
    out = cluster_groups(claude, CHEAP_MODEL, groups)

    g = out["follow"]
    a = g["assignments"][0]
    assert a["lookup_id"] == "L1"
    assert a["verdict"] == "new"
    assert len(g["new_cards"]) == 1
    # Bare stem, not promoted to the phrasal expression.
    assert g["new_cards"][a["card_index"]]["headword"].lower() == "follow"


def test_same_sense_as_existing_phrasal_is_existing(claude):
    # The lookup really is the "follow about" sense (trailing someone around),
    # so it must map onto the existing phrasal card rather than mint a new one.
    groups = [
        {
            "stem": "follow",
            "contexts": [
                one_context(
                    "L1",
                    "The toddler would follow her mother about the house all day.",
                )
            ],
            "existing": [
                {
                    "index": 0,
                    "headword": "follow about",
                    "definition": "to keep moving around a place close behind "
                    "someone, trailing them persistently",
                }
            ],
        }
    ]
    out = cluster_groups(claude, CHEAP_MODEL, groups)

    g = out["follow"]
    a = g["assignments"][0]
    assert a["lookup_id"] == "L1"
    assert a["verdict"] == "existing"
    assert a["card_index"] == 0
    assert g["new_cards"] == []


def test_inflected_verb_headword_is_infinitive(claude):
    # Sentence uses the past tense; the card's headword must be the base form.
    groups = [
        {
            "stem": "outdo",
            "contexts": [
                one_context("L1", "She really outdid herself this time.")
            ],
            "existing": [],
        }
    ]
    out = cluster_groups(claude, CHEAP_MODEL, groups)

    g = out["outdo"]
    a = g["assignments"][0]
    assert a["verdict"] == "new"
    headword = g["new_cards"][a["card_index"]]["headword"].lower()
    assert headword == "outdo"


def test_phrasal_verb_promoted_to_expression_headword(claude):
    groups = [
        {
            "stem": "make",
            "contexts": [
                one_context("L1", "The thieves made off with the paintings.")
            ],
            "existing": [],
        }
    ]
    out = cluster_groups(claude, CHEAP_MODEL, groups)

    g = out["make"]
    a = g["assignments"][0]
    assert a["verdict"] == "new"
    headword = g["new_cards"][a["card_index"]]["headword"].lower()
    # Promoted beyond the bare stem to the phrasal expression.
    assert headword != "make"
    assert "off" in headword
