"""Shared fixtures. The `claude` fixture is only used by `-m llm` tests."""

import os

import pytest

import kindle_anki

# Cheap model for behavioral evals (does not support the `effort` param).
CHEAP_MODEL = "claude-haiku-4-5-20251001"

# The real shipped language profiles, loaded once from languages.yaml so tests
# exercise the same data the tool ships. Individual codes are exposed for
# convenience (EN/FR/JA are the ones tests reference).
LANGS = kindle_anki.load_languages()
EN = LANGS["en"]
FR = LANGS["fr"]
JA = LANGS["ja"]


@pytest.fixture(scope="session")
def claude():
    import kindle_anki

    kindle_anki.load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — skipping real-LLM test")
    import anthropic

    return anthropic.Anthropic()
