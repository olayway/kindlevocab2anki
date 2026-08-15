"""blank_out: hide the answer in the example sentence.

New (PLAN decision 5): Claude returns the exact surface `span` to blank — this
lets a phrasal-verb card ("make off") hide the whole expression, not just the
looked-up stem. `span` may be a single string, or a list of pieces for a
separable phrasal verb ("tie ... up" in "she tied her hair up"). blank_out
tries the span(s) first, then falls back to the inflected word, then the stem,
and leaves the sentence intact if none match.
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


def test_blanks_discontinuous_phrasal_verb():
    # A separable phrasal verb is split by its object; each piece is a span.
    sentence = "She tied her hair up before the run."
    result = blank_out(sentence, span=["tied", "up"], word="tie", stem="tie")
    assert result == f"She {BLANK} her hair {BLANK} before the run."


def test_span_list_falls_back_when_a_piece_is_missing():
    # All-or-nothing: if any piece can't be found we must not emit a
    # half-blanked sentence — fall back to the inflected word instead.
    sentence = "She tied her hair back."
    result = blank_out(sentence, span=["tied", "up"], word="tie", stem="tie")
    assert result == f"She {BLANK} her hair back."


def test_single_element_span_list():
    sentence = "They planned to make off with the jewels."
    result = blank_out(sentence, span=["make off"], word="make", stem="make")
    assert result == f"They planned to {BLANK} with the jewels."


# The "cjk" script class (Japanese, Chinese, Thai) has no word boundaries or
# case, so blanking matches the span verbatim and skips the inflection fallback.


def test_cjk_blanks_span_verbatim():
    sentence = "彼は昨日図書館で本を読んだ。"
    result = blank_out(sentence, span="読んだ", word="読む", stem="読む", script="cjk")
    assert result == f"彼は昨日図書館で本を{BLANK}。"


def test_cjk_all_or_nothing_when_a_piece_is_missing():
    sentence = "彼は本を読んだ。"
    result = blank_out(
        sentence, span=["読んだ", "ない"], word="読む", stem="読む", script="cjk"
    )
    assert result == sentence  # "ない" absent → leave intact, don't half-blank


def test_cjk_does_not_use_inflection_fallback():
    # No verbatim span and no match → intact. The spaced path's \\w* fallback
    # (which would be meaningless without word boundaries) must not fire here.
    sentence = "彼は本を読んだ。"
    result = blank_out(sentence, span=[], word="読む", stem="読む", script="cjk")
    assert result == sentence
