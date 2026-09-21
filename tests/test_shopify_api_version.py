"""
Guards for the single source of truth for the Shopify Admin API version
(shopify_version.py).

1. Resolution order: SHOPIFY_API_VERSION > API_VERSION > default.
2. Drift guard: no other Python file may contain a version-shaped string literal
   (e.g. "2026-07") or read the version env vars directly. The literal rule is
   deliberately strict; if a legitimate non-API "YYYY-MM" string is ever needed,
   build it from parts or add a narrowly scoped allowance here.

Before shopify_version.py existed, six call sites did this with three
different defaults.
"""

import pathlib
import re

import pytest

import shopify_version
from shopify_version import DEFAULT_SHOPIFY_API_VERSION, get_api_version

REPO = pathlib.Path(__file__).resolve().parent.parent

# Shopify stable versions are YYYY-01/04/07/10.
VERSION_LITERAL = re.compile(r"""["'/]20\d\d-(?:01|04|07|10)["'/]""")
ENV_READ = re.compile(r"""(getenv|environ(\.get)?)\s*[\(\[]\s*["'](SHOPIFY_)?API_VERSION["']""")

EXCLUDED_DIRS = {"tests", ".git", "node_modules", "__pycache__", ".venv", "venv"}
ALLOWED_FILES = {"shopify_version.py"}


def _python_sources():
    for path in REPO.rglob("*.py"):
        rel = path.relative_to(REPO)
        if EXCLUDED_DIRS.intersection(rel.parts):
            continue
        if rel.as_posix() in ALLOWED_FILES:
            continue
        yield rel, path.read_text(errors="ignore")


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("SHOPIFY_API_VERSION", raising=False)
    monkeypatch.delenv("API_VERSION", raising=False)
    return monkeypatch


def test_default_when_no_env(clean_env):
    assert get_api_version() == DEFAULT_SHOPIFY_API_VERSION == "2026-07"


def test_canonical_env_var_wins(clean_env):
    clean_env.setenv("API_VERSION", "2026-01")
    clean_env.setenv("SHOPIFY_API_VERSION", "2026-04")
    assert get_api_version() == "2026-04"


def test_legacy_env_var_used_when_canonical_absent(clean_env):
    clean_env.setenv("API_VERSION", "2026-01")
    assert get_api_version() == "2026-01"


def test_empty_canonical_falls_through(clean_env):
    clean_env.setenv("SHOPIFY_API_VERSION", "")
    assert get_api_version() == DEFAULT_SHOPIFY_API_VERSION


def test_default_is_a_well_formed_stable_version():
    assert re.fullmatch(r"20\d\d-(01|04|07|10)", shopify_version.DEFAULT_SHOPIFY_API_VERSION)


def test_no_hardcoded_api_version_outside_shopify_version():
    offenders = [
        f"{rel}:{i}: {line.strip()}"
        for rel, text in _python_sources()
        for i, line in enumerate(text.splitlines(), 1)
        if VERSION_LITERAL.search(line)
    ]
    assert offenders == [], "Use shopify_version.get_api_version():\n" + "\n".join(offenders)


def test_no_direct_version_env_reads_outside_shopify_version():
    offenders = [
        f"{rel}:{i}: {line.strip()}"
        for rel, text in _python_sources()
        for i, line in enumerate(text.splitlines(), 1)
        if ENV_READ.search(line)
    ]
    assert offenders == [], "Use shopify_version.get_api_version():\n" + "\n".join(offenders)
