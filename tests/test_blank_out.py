"""blank_out: hide the answer in the example sentence.

New (PLAN decision 5): Claude returns the exact surface `span` to blank — this
lets a phrasal-verb card ("make off") hide the whole expression, not just the
looked-up stem. blank_out tries the span first, then falls back to the
inflected word, then the stem, and leaves the sentence intact if none match.
"""

from kindle_anki import BLANK, blank_out


def test_blanks_multiword_span_verbatim():
    sentence = "They planned to make off with the jewels."
    result = blank_out(sentence, span="make off", word="make", stem="make")
    assert result == f"They planned to {BLANK} with the jewels."


def test_span_matches_verbatim_not_as_a_prefix():
    # A span is the exact surface form to hide, so unlike the word/stem
    # fallback it must not also blank longer words that merely start with it.
    sentence = "The cat sat on the caterpillar."
    result = blank_out(sentence, span="cat", word="cat", stem="cat")
    assert result == f"The {BLANK} sat on the caterpillar."
