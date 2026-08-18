"""Language config split: the learning axis (--learning) vs the translation
axis (--translation).

The learning language gates which vocab.db lookups are read (WORDS.lang), and
parametrizes the clustering prompt (definition language + base-form rules). It
is independent of the back-of-card translation language. Profiles are loaded
from languages.yaml (no in-code table) — see load_languages tests below.
"""

import sqlite3

import pytest

import kindle_anki
from kindle_anki import (
    Fatal,
    cluster_system_prompt,
    load_languages,
    read_lookups,
    resolve_learning,
)
from tests.conftest import LANGS


# --- resolve_learning -----------------------------------------------------


def test_defaults_to_english(monkeypatch):
    monkeypatch.delenv("LEARNING_LANGUAGE", raising=False)
    assert resolve_learning(None, LANGS) is LANGS["en"]


def test_cli_code_selects_profile(monkeypatch):
    monkeypatch.delenv("LEARNING_LANGUAGE", raising=False)
    assert resolve_learning("fr", LANGS) is LANGS["fr"]


def test_code_is_case_insensitive(monkeypatch):
    monkeypatch.delenv("LEARNING_LANGUAGE", raising=False)
    assert resolve_learning("FR", LANGS) is LANGS["fr"]


def test_env_fallback(monkeypatch):
    monkeypatch.setenv("LEARNING_LANGUAGE", "de")
    assert resolve_learning(None, LANGS) is LANGS["de"]


def test_cli_overrides_env(monkeypatch):
    monkeypatch.setenv("LEARNING_LANGUAGE", "de")
    assert resolve_learning("ja", LANGS) is LANGS["ja"]


def test_unknown_language_is_fatal(monkeypatch):
    monkeypatch.delenv("LEARNING_LANGUAGE", raising=False)
    with pytest.raises(Fatal, match="Unknown --learning 'xx'"):
        resolve_learning("xx", LANGS)


def test_default_missing_from_config_is_fatal(monkeypatch):
    # No silent fallback: if the default code isn't in the file, say so.
    monkeypatch.delenv("LEARNING_LANGUAGE", raising=False)
    without_en = {k: v for k, v in LANGS.items() if k != "en"}
    with pytest.raises(Fatal, match="Unknown --learning 'en'"):
        resolve_learning(None, without_en)


# --- load_languages: the file is the sole source, so it validates hard -----


def _write(tmp_path, text):
    path = tmp_path / "languages.yaml"
    path.write_text(text)
    return path


VALID_EN = """
en:
  name: English
  boundaries: true
  ignore_case: true
  inflection: true
  morphology: verbs to the infinitive.
"""


def test_loads_the_shipped_file():
    langs = load_languages()  # the real languages.yaml beside the script
    assert set(langs) >= {"en", "fr", "de", "es", "ja"}
    assert langs["en"].name == "English"
    assert langs["ja"].boundaries is False and langs["ja"].inflection is False
    assert langs["en"].code == "en"


def test_missing_file_is_fatal(tmp_path):
    with pytest.raises(Fatal, match="not found"):
        load_languages(tmp_path / "nope.yaml")


def test_malformed_yaml_is_fatal(tmp_path):
    path = _write(tmp_path, "en: [unclosed\n")
    with pytest.raises(Fatal, match="not valid YAML"):
        load_languages(path)


def test_non_mapping_is_fatal(tmp_path):
    path = _write(tmp_path, "- just\n- a list\n")
    with pytest.raises(Fatal, match="mapping of language code"):
        load_languages(path)


def test_empty_file_is_fatal(tmp_path):
    path = _write(tmp_path, "\n")
    with pytest.raises(Fatal, match="not found|defines no languages"):
        load_languages(path)


@pytest.mark.parametrize("field", ["name", "morphology", "boundaries", "ignore_case", "inflection"])
def test_missing_required_field_is_fatal(tmp_path, field):
    lines = [l for l in VALID_EN.strip().splitlines() if not l.strip().startswith(field + ":")]
    path = _write(tmp_path, "\n".join(lines) + "\n")
    with pytest.raises(Fatal, match=f"missing required field '{field}'"):
        load_languages(path)


def test_non_bool_flag_is_fatal(tmp_path):
    path = _write(tmp_path, VALID_EN.replace("boundaries: true", "boundaries: yesish"))
    with pytest.raises(Fatal, match="'boundaries' must be true/false"):
        load_languages(path)


def test_non_text_name_is_fatal(tmp_path):
    path = _write(tmp_path, VALID_EN.replace("name: English", "name: true"))
    with pytest.raises(Fatal, match="'name' must be text"):
        load_languages(path)


def test_entry_not_a_mapping_is_fatal(tmp_path):
    path = _write(tmp_path, "en: just-a-string\n")
    with pytest.raises(Fatal, match="must be a mapping of fields"):
        load_languages(path)


def test_code_is_lowercased_from_key(tmp_path):
    path = _write(tmp_path, VALID_EN.replace("en:", "EN:"))
    langs = load_languages(path)
    assert "en" in langs and langs["en"].code == "en"


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


def test_none_gate_reads_every_language(tmp_path):
    # --force-lang path: no WORDS.lang filter, so both langs come through.
    db = tmp_path / "vocab.db"
    _make_db(db)
    stems = sorted(lk.stem for lk in read_lookups(db, None))
    assert stems == ["bank", "manger"]


# --- prompt parametrization ----------------------------------------------


def test_prompt_uses_learning_language_name():
    prompt = cluster_system_prompt(LANGS["fr"])
    assert "a French learner" in prompt
    assert "one concise French definition" in prompt
    assert "Monolingual French" in prompt
    # the profile's morphology fragment is spliced into the base-form rule
    assert LANGS["fr"].morphology in prompt


def test_prompt_for_english():
    prompt = cluster_system_prompt(LANGS["en"])
    assert "an English learner" in prompt or "a English learner" in prompt
    assert "Monolingual English" in prompt


def test_translation_axis_is_independent_of_learning():
    # Learning French, native tongue Polish: the back-of-card gloss is Polish
    # while definitions stay monolingual French.
    prompt = cluster_system_prompt(LANGS["fr"], language="Polish")
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
