"""Language config split: the learning axis (--learning) vs the translation
axis (--language).

The learning language gates which vocab.db lookups are read (WORDS.lang), and
parametrizes the clustering prompt (definition language + base-form rules). It
is independent of the back-of-card translation language.
"""

import sqlite3

import pytest

import kindle_anki
from kindle_anki import (
    DEFAULT_LEARNING,
    Fatal,
    LANGUAGES,
    cluster_system_prompt,
    read_lookups,
    resolve_learning,
)


# --- resolve_learning -----------------------------------------------------


def test_defaults_to_english(monkeypatch):
    monkeypatch.delenv("LEARNING_LANGUAGE", raising=False)
    assert resolve_learning(None) is LANGUAGES["en"]
    assert DEFAULT_LEARNING is LANGUAGES["en"]


def test_cli_code_selects_profile(monkeypatch):
    monkeypatch.delenv("LEARNING_LANGUAGE", raising=False)
    assert resolve_learning("fr") is LANGUAGES["fr"]


def test_code_is_case_insensitive(monkeypatch):
    monkeypatch.delenv("LEARNING_LANGUAGE", raising=False)
    assert resolve_learning("FR") is LANGUAGES["fr"]


def test_env_fallback(monkeypatch):
    monkeypatch.setenv("LEARNING_LANGUAGE", "de")
    assert resolve_learning(None) is LANGUAGES["de"]


def test_cli_overrides_env(monkeypatch):
    monkeypatch.setenv("LEARNING_LANGUAGE", "de")
    assert resolve_learning("ja") is LANGUAGES["ja"]


def test_unknown_language_is_fatal(monkeypatch):
    monkeypatch.delenv("LEARNING_LANGUAGE", raising=False)
    with pytest.raises(Fatal, match="Unknown --learning 'xx'"):
        resolve_learning("xx")


# --- the read gate --------------------------------------------------------


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE WORDS (id TEXT, word TEXT, stem TEXT, lang TEXT);
        CREATE TABLE BOOK_INFO (id TEXT, title TEXT, authors TEXT);
        CREATE TABLE LOOKUPS (id TEXT, word_key TEXT, book_key TEXT,
                              usage TEXT, timestamp INTEGER);
        """
    )
    conn.execute("INSERT INTO WORDS VALUES ('w1','banks','bank','en')")
    conn.execute("INSERT INTO WORDS VALUES ('w2','mangeait','manger','fr')")
    conn.execute("INSERT INTO BOOK_INFO VALUES ('b1','Some Book','An Author')")
    conn.executemany(
        "INSERT INTO LOOKUPS VALUES (?,?,?,?,?)",
        [
            ("L1", "w1", "b1", "The river banks flooded.", 1000),
            ("L2", "w2", "b1", "Il mangeait une pomme.", 2000),
        ],
    )
    conn.commit()
    conn.close()


def test_gate_reads_only_the_learning_language(tmp_path):
    db = tmp_path / "vocab.db"
    _make_db(db)

    en = read_lookups(db, "en")
    assert [lk.stem for lk in en] == ["bank"]

    fr = read_lookups(db, "fr")
    assert [lk.stem for lk in fr] == ["manger"]


def test_gate_defaults_to_english(tmp_path):
    db = tmp_path / "vocab.db"
    _make_db(db)
    assert [lk.stem for lk in read_lookups(db)] == ["bank"]


# --- prompt parametrization ----------------------------------------------


def test_prompt_uses_learning_language_name():
    prompt = cluster_system_prompt(LANGUAGES["fr"])
    assert "a French learner" in prompt
    assert "one concise French definition" in prompt
    assert "Monolingual French" in prompt
    # the profile's morphology fragment is spliced into the base-form rule
    assert LANGUAGES["fr"].morphology in prompt


def test_prompt_defaults_to_english():
    prompt = cluster_system_prompt()
    assert "an English learner" in prompt or "a English learner" in prompt
    assert "Monolingual English" in prompt


def test_translation_axis_is_independent_of_learning():
    # Learning French, native tongue Polish: the back-of-card gloss is Polish
    # while definitions stay monolingual French.
    prompt = cluster_system_prompt(LANGUAGES["fr"], language="Polish")
    assert "a Polish translation" in prompt
    assert "Monolingual French" in prompt


def test_main_learning_flag_selects_lookups(tmp_path, monkeypatch, capsys):
    db = tmp_path / "vocab.db"
    _make_db(db)
    monkeypatch.setattr(kindle_anki, "CACHE_DB", tmp_path / "cache.db")
    monkeypatch.setattr(kindle_anki, "anki", lambda action, **p: [])

    rc = kindle_anki.main(["--db", str(db), "--learning", "fr"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "1 French lookup(s)" in out  # only the fr row survived the gate
