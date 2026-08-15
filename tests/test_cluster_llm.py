"""Real-LLM behavioral evals for the clustering step (PLAN prompt rewrite).

Marked `llm` -> deselected by default. Run with:
    uv run --with pytest --with anthropic pytest -m llm

Assertions are deliberately loose (verdict, substring, count) so they survive
LLM nondeterminism while still catching a prompt/schema regression.
"""

import pytest

from kindle_anki import LANGUAGES, cluster_groups
from tests.conftest import CHEAP_MODEL

FR = LANGUAGES["fr"]
JA = LANGUAGES["ja"]

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


def test_existing_match_picks_correct_index_among_several(claude):
    # Two existing cards for the same stem, different senses. The lookup uses the
    # SECOND sense, so it must map to `index` 1 — not just any "existing" verdict.
    # A single-existing-card test can't catch a model that always returns 0.
    groups = [
        {
            "stem": "bank",
            "contexts": [
                one_context("L1", "She deposited her paycheck at the bank.")
            ],
            "existing": [
                {
                    "index": 0,
                    "headword": "bank",
                    "definition": "the land along the side of a river",
                },
                {
                    "index": 1,
                    "headword": "bank",
                    "definition": "a financial institution that holds money",
                },
            ],
        }
    ]
    out = cluster_groups(claude, CHEAP_MODEL, groups)

    g = out["bank"]
    a = g["assignments"][0]
    assert a["lookup_id"] == "L1"
    assert a["verdict"] == "existing"
    assert a["card_index"] == 1
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


def test_same_sense_lookups_collapse_into_one_card(claude):
    # Two lookups of the SAME sense must share one card, not mint two. This is
    # the mirror of the polysemy split, and `build_notes` relies on it: same
    # `card_index` -> the lookups collapse into a single note with a joined
    # `Lookups` field.
    contexts = [
        one_context("L1", "The disease afflicts millions worldwide."),
        one_context("L2", "A rare condition afflicted her for years."),
    ]
    groups = [{"stem": "afflict", "contexts": contexts, "existing": []}]
    out = cluster_groups(claude, CHEAP_MODEL, groups)

    g = out["afflict"]
    assert len(g["new_cards"]) == 1
    by_id = {a["lookup_id"]: a for a in g["assignments"]}
    assert by_id["L1"]["verdict"] == "new"
    assert by_id["L2"]["verdict"] == "new"
    # Same sense -> same card.
    assert by_id["L1"]["card_index"] == by_id["L2"]["card_index"]


def test_span_copied_from_primary_sentence(claude):
    # When same-sense lookups collapse, `build_notes` blanks the span out of the
    # PRIMARY sentence only (the earliest lookup, which the pipeline sends first).
    # So every span piece must be verbatim in that sentence, not merely in some
    # other context. The two sentences use different inflections, so a span drawn
    # from the wrong context would not be found in the primary one. Contexts are
    # listed earliest-first, matching how `build_group_payload` sends them.
    contexts = [
        one_context("L1", "The plague afflicted the whole village.", timestamp=1),
        one_context("L2", "Such ailments afflict the elderly most.", timestamp=5),
    ]
    groups = [{"stem": "afflict", "contexts": contexts, "existing": []}]
    out = cluster_groups(claude, CHEAP_MODEL, groups)

    g = out["afflict"]
    # Precondition: same sense collapses to one card.
    assert len(g["new_cards"]) == 1

    primary = min(contexts, key=lambda c: c["timestamp"])["sentence"]
    card = g["new_cards"][0]
    assert card["span"], "span must not be empty"
    for piece in card["span"]:
        assert piece in primary, f"span piece {piece!r} not verbatim in primary sentence"


def test_batch_of_groups_each_returns_one_result(claude):
    # Batching is the real production path. Every input group must come back
    # exactly once (keyed by normalized stem) with exactly one assignment per
    # input context and the lookup_ids preserved — a prompt/schema regression
    # would silently drop, merge, or renumber groups.
    groups = [
        {
            "stem": "afflict",
            "contexts": [
                one_context("A1", "The diseases that afflict us have changed.")
            ],
            "existing": [],
        },
        {
            "stem": "winston",
            "contexts": [
                one_context("W1", "Winston walked to the Ministry of Truth.")
            ],
            "existing": [],
        },
        {
            "stem": "bank",
            "contexts": [
                one_context("B1", "We sat on the grassy bank of the river."),
                one_context("B2", "She deposited the cheque at the bank downtown."),
            ],
            "existing": [],
        },
    ]
    out = cluster_groups(claude, CHEAP_MODEL, groups)

    # Exactly one result per input group.
    assert set(out) >= {"afflict", "winston", "bank"}

    # Exactly one assignment per input context, ids preserved, within each group.
    expected_ids = {"afflict": {"A1"}, "winston": {"W1"}, "bank": {"B1", "B2"}}
    for stem, ids in expected_ids.items():
        got = [a["lookup_id"] for a in out[stem]["assignments"]]
        assert len(got) == len(ids), f"{stem}: expected {len(ids)} assignments, got {len(got)}"
        assert set(got) == ids

    # Verdicts survived batching alongside the other groups.
    assert out["winston"]["assignments"][0]["verdict"] == "junk"
    assert out["afflict"]["assignments"][0]["verdict"] == "new"


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
    headword = g["new_cards"][a["card_index"]]["headword"].lower().strip()
    # Promoted to the phrasal expression. "make off" and "make off with" are
    # both legitimate readings of the idiom, so accept either — but nothing else.
    assert headword in {"make off", "make off with"}


# --- French (the learning axis) ------------------------------------------
# Same loose-property style, but with learning=FR: the base-form and
# expression rules must fire on French morphology, and definitions come back
# in French rather than English.


def test_fr_inflected_verb_headword_is_infinitive(claude):
    # Imperfect tense in the sentence; the headword must be the infinitive.
    groups = [
        {
            "stem": "manger",
            "contexts": [one_context("L1", "Il mangeait une pomme au soleil.")],
            "existing": [],
        }
    ]
    out = cluster_groups(claude, CHEAP_MODEL, groups, learning=FR)

    g = out["manger"]
    a = g["assignments"][0]
    assert a["verdict"] == "new"
    card = g["new_cards"][a["card_index"]]
    assert card["headword"].lower() == "manger"
    assert card["definition"].strip(), "definition must be present (in French)"


def test_fr_expression_promoted_to_locution(claude):
    # "faire" used inside the locution "faire la queue" (to queue up) must be
    # promoted to the whole expression, not left as the bare verb.
    groups = [
        {
            "stem": "faire",
            "contexts": [
                one_context(
                    "L1", "Nous avons fait la queue pendant une heure au guichet."
                )
            ],
            "existing": [],
        }
    ]
    out = cluster_groups(claude, CHEAP_MODEL, groups, learning=FR)

    g = out["faire"]
    a = g["assignments"][0]
    assert a["verdict"] == "new"
    headword = g["new_cards"][a["card_index"]]["headword"].lower().strip()
    # Unambiguous locution — assert the exact expression (tolerant of case only).
    assert headword == "faire la queue"


# --- Japanese (the "cjk" script class) -----------------------------------
# blank_out's cjk path matches the span verbatim with NO word/stem fallback,
# so for CJK the "span is an exact substring of the sentence" invariant is
# load-bearing in a way it isn't for spaced languages. These guard that.


def test_ja_span_is_verbatim_substring(claude):
    # The whole point: whatever span the model picks MUST occur verbatim in the
    # sentence, or the cjk blanking silently no-ops (there is no fallback).
    sentence = "彼は昨日図書館で本を読んだ。"
    groups = [
        {
            "stem": "読む",
            "contexts": [one_context("L1", sentence)],
            "existing": [],
        }
    ]
    out = cluster_groups(claude, CHEAP_MODEL, groups, learning=JA)

    g = out["読む"]
    a = g["assignments"][0]
    assert a["verdict"] == "new"
    span = g["new_cards"][a["card_index"]]["span"]
    assert span, "a span must be returned"
    for piece in span:
        assert piece in sentence, f"span piece {piece!r} is not verbatim in the sentence"


def test_ja_headword_is_dictionary_form(claude):
    # Sentence uses the past tense (読んだ); the headword must be the dictionary
    # form (辞書形) 読む — the CJK analogue of the infinitive test.
    groups = [
        {
            "stem": "読む",
            "contexts": [one_context("L1", "彼は昨日図書館で本を読んだ。")],
            "existing": [],
        }
    ]
    out = cluster_groups(claude, CHEAP_MODEL, groups, learning=JA)

    g = out["読む"]
    a = g["assignments"][0]
    assert a["verdict"] == "new"
    card = g["new_cards"][a["card_index"]]
    assert card["headword"].strip() == "読む"
    assert card["definition"].strip(), "definition must be present (in Japanese)"
