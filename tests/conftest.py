"""Pytest config — adds --fast flag to skip @pytest.mark.slow tests.

Use:
    pytest tests/ -q          # full suite
    pytest tests/ -q --fast   # skip slow tests (anything that loads
                              # sentence_transformers, runs real dream
                              # cycles, or hits embeddings end-to-end)
"""
import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--fast", action="store_true", default=False,
        help="Skip tests marked @pytest.mark.slow",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "slow: long-running test (skipped with --fast)"
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--fast"):
        return
    skip = pytest.mark.skip(reason="--fast mode")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)
