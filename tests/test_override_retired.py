"""
Guard: the preorder override is retired (rev2 Move 1g).

The override metafield, preorder.product_overrides and override_service were
removed after the values were folded into pub_date on 2026-10-27. pub_date is
the single source of truth (decision D1).

This fails if any of it comes back, because a half-restored override is exactly
the dual-source state the phase existed to remove.
"""

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "tests", "docs", "db"}

#: Files allowed to mention the override: the one-off migration scripts, which
#: document and perform the retirement itself.
ALLOWED = {
    "scripts/fold_override_into_pubdate.py",
    "scripts/delete_override_metafield.py",
}


def production_sources():
    """
    Yields (path, code_lines). Comment lines are excluded: notes explaining why
    the override was retired are useful and must not trip these guards. Only
    live code counts.
    """
    for path in REPO.rglob("*.py"):
        rel = path.relative_to(REPO)
        if SKIP_DIRS.intersection(rel.parts) or rel.as_posix() in ALLOWED:
            continue
        lines = [
            (i, line)
            for i, line in enumerate(path.read_text(errors="ignore").splitlines(), 1)
            if not line.lstrip().startswith("#")
        ]
        yield rel, lines


def test_no_module_reads_the_override_metafield():
    offenders = [
        f"{rel}:{i}"
        for rel, lines in production_sources()
        for i, line in lines
        if "preorder_override_date" in line
    ]
    assert offenders == [], f"custom.preorder_override_date is retired; read pub_date: {offenders}"


def test_no_module_reads_the_product_overrides_table():
    offenders = [
        f"{rel}:{i}"
        for rel, lines in production_sources()
        for i, line in lines
        if "product_overrides" in line
    ]
    assert offenders == [], f"preorder.product_overrides is dropped: {offenders}"


def test_override_service_is_gone():
    assert not (REPO / "override_service.py").exists()
    assert not (REPO / "reclassification.py").exists(), (
        "the root reclassification.py existed only for override_service"
    )


def test_classification_input_has_no_override_field():
    from classification.types import ClassificationInput

    assert not hasattr(ClassificationInput("x", [], False, [], None, 0), "override_date")


def test_product_metadata_has_no_override_field():
    import inspect

    from domain_models import ProductMetadata

    assert "override_date_raw" not in inspect.getsource(ProductMetadata)


def test_the_override_conflict_status_cannot_be_produced():
    import inspect

    from classification import engine

    assert "anomaly_override_conflict" not in inspect.getsource(engine.classify_preorder_product)


def test_resolver_takes_only_date_tags_and_pub_date():
    import inspect

    from classification.utils import resolve_effective_pub_date

    params = set(inspect.signature(resolve_effective_pub_date).parameters)
    assert params == {"date_tags", "pub_date"}
