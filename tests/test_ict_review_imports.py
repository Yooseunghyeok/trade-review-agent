import importlib


def test_ict_review_package_imports():
    assert importlib.import_module("ict_review")


def test_ict_review_subpackages_import():
    for name in [
        "ict_review.cli",
        "ict_review.evaluation",
        "ict_review.features",
        "ict_review.ingestion",
        "ict_review.ledger",
        "ict_review.narrative",
        "ict_review.rendering",
        "ict_review.validation",
    ]:
        assert importlib.import_module(name)
