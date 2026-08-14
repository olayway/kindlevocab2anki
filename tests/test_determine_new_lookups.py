"""determine_new_lookups: which lookups still need a card (PLAN step 3).

`consumed` is the union of every existing card's `Lookups` field (the ids that
already produced/were-absorbed-by a card). A lookup is *new* iff its id is
neither consumed nor in skipped.json. Pure logic over already-fetched
notesInfo — no Anki call, no LLM.
"""

from kindle_anki import Lookup, determine_new_lookups


def mklookup(id, stem="x"):
    return Lookup(
        id=id, stem=stem, word=stem, sentence="", title="", authors="", timestamp=0
    )


def note(lookups_value, stem="x"):
    return {"fields": {"Stem": {"value": stem}, "Lookups": {"value": lookups_value}}}


def test_excludes_lookups_already_consumed_by_a_card():
    lookups = [mklookup("L1"), mklookup("L2")]
    notes_info = [note("L1")]
    new = determine_new_lookups(lookups, notes_info, skipped_ids=set())
    assert [lk.id for lk in new] == ["L2"]


def test_excludes_skipped_ids():
    lookups = [mklookup("L1"), mklookup("L2"), mklookup("L3")]
    new = determine_new_lookups(lookups, notes_info=[], skipped_ids={"L2"})
    assert [lk.id for lk in new] == ["L1", "L3"]


def test_one_card_can_consume_several_ids():
    # A shared card joins its lookups comma-separated in the Lookups field.
    lookups = [mklookup("L1"), mklookup("L2"), mklookup("L3")]
    notes_info = [note("L1, L2")]
    new = determine_new_lookups(lookups, notes_info, skipped_ids=set())
    assert [lk.id for lk in new] == ["L3"]
