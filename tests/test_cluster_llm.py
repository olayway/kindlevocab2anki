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
        assert card["span"] in sentences


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
