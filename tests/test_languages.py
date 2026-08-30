"""Language config split: the learning axis (--learning) vs the translation
axis (--translation).

The learning language gates which vocab.db lookups are read (WORDS.lang), and
parametrizes the clustering prompt (definition language + base-form rules). It
is independent of the back-of-card translation language. Profiles are loaded
from languages.yaml (no in-code table) — see load_languages tests below.
"""

import dataclasses
import sqlite3

import pytest

import kindle_anki
from kindle_anki import (
    PRODUCTION_PAIRS,
    Fatal,
    cluster_schema,
    cluster_system_prompt,
    load_languages,
    read_lookups,
    resolve_learning,
    resolve_level,
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


# --- resolve_level: the CEFR level for --production sentences --------------


def _with_level(level):
    return dataclasses.replace(LANGS["en"], level=level)


def test_level_none_when_unset():
    assert resolve_level(None, _with_level(None)) is None


def test_level_from_cli_is_uppercased():
    assert resolve_level("b1", _with_level(None)) == "B1"


def test_level_falls_back_to_profile():
    assert resolve_level(None, _with_level("A2")) == "A2"


def test_level_cli_overrides_profile():
    assert resolve_level("C1", _with_level("A2")) == "C1"


def test_level_blank_profile_is_none():
    assert resolve_level(None, _with_level("  ")) is None


def test_unknown_cli_level_is_fatal():
    with pytest.raises(Fatal, match="Unknown CEFR level 'ZZ'"):
        resolve_level("ZZ", _with_level(None))


def test_unknown_profile_level_is_fatal():
    # A bad `level:` in the YAML only surfaces when --production consults it.
    with pytest.raises(Fatal, match="Unknown CEFR level 'X9'"):
        resolve_level(None, _with_level("X9"))


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


def test_level_is_optional_and_defaults_none(tmp_path):
    # VALID_EN carries no `level:`, so existing configs stay valid.
    path = _write(tmp_path, VALID_EN)
    assert load_languages(path)["en"].level is None


def test_level_is_read_when_present(tmp_path):
    path = _write(tmp_path, VALID_EN + "  level: B1\n")
    assert load_languages(path)["en"].level == "B1"


def test_non_text_level_is_fatal(tmp_path):
    path = _write(tmp_path, VALID_EN + "  level: true\n")
    with pytest.raises(Fatal, match="'level' must be text"):
        load_languages(path)


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


def test_no_production_bullet_without_level():
    prompt = cluster_system_prompt(LANGS["fr"], language="Polish")
    assert "production" not in prompt
    assert "class=\"focus\"" not in prompt


def test_production_bullet_appears_with_level():
    # Learning French, native Polish, level B1: ask for production pairs whose
    # native side is Polish and whose non-target words sit at B1.
    prompt = cluster_system_prompt(LANGS["fr"], language="Polish", level="B1")
    assert "`production`" in prompt
    assert f"exactly {PRODUCTION_PAIRS} sentence pairs" in prompt
    assert "a natural Polish sentence" in prompt
    assert 'class="focus"' in prompt  # native side markup
    assert 'class="target"' in prompt  # target side markup
    assert "CEFR B1" in prompt


# --- cluster_schema: fields requested per run ----------------------------


def test_schema_base_has_no_translation_or_production():
    card = cluster_schema()["properties"]["groups"]["items"]["properties"][
        "new_cards"
    ]["items"]
    assert "translation" not in card["properties"]
    assert "production" not in card["properties"]


def test_schema_with_translation_requires_translation():
    card = cluster_schema(with_translation=True)["properties"]["groups"]["items"][
        "properties"
    ]["new_cards"]["items"]
    assert "translation" in card["properties"]
    assert "translation" in card["required"]
    assert "production" not in card["properties"]


def test_schema_with_production_requires_pair_array():
    card = cluster_schema(with_production=True)["properties"]["groups"]["items"][
        "properties"
    ]["new_cards"]["items"]
    prod = card["properties"]["production"]
    assert "production" in card["required"]
    # The pair count is enforced in the prompt, not the schema: the structured-
    # output format rejects array minItems/maxItems > 1.
    assert "minItems" not in prod and "maxItems" not in prod
    assert prod["items"]["required"] == ["native", "target"]
    assert prod["items"]["additionalProperties"] is False


def test_main_learning_flag_selects_lookups(tmp_path, monkeypatch, capsys):
    db = tmp_path / "vocab.db"
    _make_db(db)
    monkeypatch.setattr(kindle_anki, "CACHE_DB", tmp_path / "cache.db")
    monkeypatch.setattr(kindle_anki, "anki", lambda action, **p: [])

    rc = kindle_anki.main(["--db", str(db), "--learning", "fr"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "1 French lookup(s)" in out  # only the fr row survived the gate
