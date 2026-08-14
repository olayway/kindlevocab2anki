"""Shared fixtures. The `claude` fixture is only used by `-m llm` tests."""

import os

import pytest

# Cheap model for behavioral evals (does not support the `effort` param).
CHEAP_MODEL = "claude-haiku-4-5-20251001"


@pytest.fixture(scope="session")
def claude():
    import kindle_anki

    kindle_anki.load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — skipping real-LLM test")
    import anthropic

    return anthropic.Anthropic()
