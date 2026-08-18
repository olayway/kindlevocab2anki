"""book_tag + deck_name_for: the two spots that used to assume English.

book_tag preserves the title's own script (only Anki's structural characters —
whitespace and "::" — are collapsed), so non-Latin titles keep a real tag
instead of degrading to book::unknown. deck_name_for derives the deck from the
learning language rather than the hardcoded "English::Kindle".
"""

from kindle_anki import book_tag, deck_name_for
from tests.conftest import EN, FR, JA


def test_latin_title_slugs_as_before():
    assert book_tag("War and Peace") == "book::war-and-peace"


def test_punctuation_is_dropped_and_spaces_become_dashes():
    assert book_tag("Why Zebras Don't Get Ulcers!") == "book::why-zebras-dont-get-ulcers"


def test_cjk_title_is_preserved():
    assert book_tag("戦争と平和") == "book::戦争と平和"


def test_cyrillic_title_is_preserved():
    assert book_tag("Война и мир") == "book::война-и-мир"


def test_colons_collapse_to_a_single_separator():
    # "::" is Anki's deck/hierarchy separator; it must not survive inside a tag.
    assert book_tag("Dune: Part Two") == "book::dune-part-two"


def test_empty_or_symbol_only_title_falls_back_to_unknown():
    assert book_tag("") == "book::unknown"
    assert book_tag("!!!") == "book::unknown"


def test_deck_name_derives_from_learning_language():
    assert deck_name_for(EN) == "English::Kindle"
    assert deck_name_for(FR) == "French::Kindle"
    assert deck_name_for(JA) == "Japanese::Kindle"
