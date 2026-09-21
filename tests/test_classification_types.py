"""
Guard: classification/types.py holds type definitions only.

Until rev2 Move 0.3 it also contained a pasted "contract-only skeleton" of
`classify_preorder_product` that returned a placeholder `anomaly_missing_tag`
for every product (docs/DOCS_STATUS.md Landmine 5). Nothing imported it, but
importing it by mistake would have silently misclassified everything. The real
classifier lives in classification/engine.py.
"""

import inspect

import classification.types as types_module


def test_types_module_defines_no_functions():
    own_functions = [
        name
        for name, obj in vars(types_module).items()
        if inspect.isfunction(obj) and obj.__module__ == types_module.__name__
    ]
    assert own_functions == [], f"classification/types.py must hold types only; found {own_functions}"


def test_no_classifier_reachable_from_types_module():
    assert not hasattr(types_module, "classify_preorder_product")


def test_expected_types_are_defined_here_once():
    from classification import engine

    assert types_module.ClassificationInput.__module__ == "classification.types"
    assert types_module.ClassificationResult.__module__ == "classification.types"
    # engine re-exports the same classes, not its own copies
    assert engine.ClassificationInput is types_module.ClassificationInput
    assert engine.ClassificationResult is types_module.ClassificationResult
