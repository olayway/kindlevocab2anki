"""The active-production card template: empty-card gating, native→target
placement, and the day-seeded rotation shared by front and back."""

import re

import pytest

import kindle_anki
from kindle_anki import PRODUCTION_BACK, PRODUCTION_FRONT, PRODUCTION_ROTATE_JS


def test_front_is_gated_on_first_pair():
    # No production data (ProdNative1 empty) → the whole front is blank, so Anki
    # generates no production card. This is the natural backfill gate.
    assert PRODUCTION_FRONT.startswith("{{#ProdNative1}}")
    assert PRODUCTION_FRONT.rstrip().endswith("{{/ProdNative1}}")


def test_back_is_gated_on_first_pair():
    assert PRODUCTION_BACK.startswith("{{#ProdNative1}}")
    assert PRODUCTION_BACK.rstrip().endswith("{{/ProdNative1}}")


def test_each_extra_pair_is_conditional():
    # Pairs 2 and 3 only render when their native field is present, so a note
    # with fewer pairs shows fewer — and front/back count them identically.
    for tpl in (PRODUCTION_FRONT, PRODUCTION_BACK):
        assert "{{#ProdNative2}}" in tpl and "{{/ProdNative2}}" in tpl
        assert "{{#ProdNative3}}" in tpl and "{{/ProdNative3}}" in tpl


def test_front_prompts_native_not_target():
    # You read the native sentence and must produce the target — so the front
    # shows ProdNative and never reveals ProdTarget.
    assert "{{ProdNative1}}" in PRODUCTION_FRONT
    assert "ProdTarget" not in PRODUCTION_FRONT


def test_back_reveals_target_after_the_divider():
    assert "<hr id=answer>" in PRODUCTION_BACK
    front_part, answer_part = PRODUCTION_BACK.split("<hr id=answer>", 1)
    # The native prompt is echoed above the divider; the target answer below it.
    assert "{{ProdNative1}}" in front_part
    assert "{{ProdTarget1}}" in answer_part


def test_back_has_no_book_source_footer():
    # Production sentences are generated, not quoted from a book, so a Source
    # footer would falsely imply provenance — it must not appear.
    assert "{{Source}}" not in PRODUCTION_BACK
    assert "{{LookupDate}}" not in PRODUCTION_BACK


def test_pair_counts_match_across_front_and_back_groups():
    # The rotation index only stays in sync if every rotation group holds the
    # same number of pairs. Front has one group; back has two (prompt+answer).
    def group_pair_counts(tpl):
        return [g.count('<div class="prod-pair">') for g in tpl.split('class="prod-rotation"')[1:]]

    front_counts = group_pair_counts(PRODUCTION_FRONT)
    back_counts = group_pair_counts(PRODUCTION_BACK)
    assert front_counts == [3]
    assert back_counts == [3, 3]


def test_rotation_is_day_seeded_and_shared():
    # Both sides embed the identical script, so they compute the same index with
    # no stored state: floor(now / one day) modulo the number of pairs.
    assert PRODUCTION_ROTATE_JS in PRODUCTION_FRONT
    assert PRODUCTION_ROTATE_JS in PRODUCTION_BACK
    assert "86400000" in PRODUCTION_ROTATE_JS  # ms per day
    assert re.search(r"Math\.floor\(.*Date\.now\(\).*\)\s*%", PRODUCTION_ROTATE_JS, re.S)


def test_genanki_model_has_recognition_then_production():
    pytest.importorskip("genanki")
    model = kindle_anki.build_genanki_model()
    names = [t["name"] for t in model.templates]
    assert names == [kindle_anki.RECOGNITION_TEMPLATE, kindle_anki.PRODUCTION_TEMPLATE]
    assert "{{ProdNative1}}" in model.templates[1]["qfmt"]
