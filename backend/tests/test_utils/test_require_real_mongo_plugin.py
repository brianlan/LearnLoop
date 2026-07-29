from __future__ import annotations

import pytest

pytest_plugins = ["pytester"]

# The --require-real-mongo enforcement lives in tests/conftest.py. pytester
# runs in an isolated tree without that conftest, so replicate the minimal
# plugin here via makeconftest. The production behavior is covered by the
# real backend suite; these tests verify the plugin contract in isolation.

_PLUGIN_CONFTEST = """
import pytest

def pytest_addoption(parser):
    parser.addoption(
        "--require-real-mongo",
        action="store_true",
        default=False,
        help="Fail when zero real_mongo tests are selected or any selected real_mongo test skips.",
    )

def pytest_configure(config):
    config.addinivalue_line("markers", "real_mongo: requires real Mongo")
    config._require_real_mongo_enabled = config.getoption("--require-real-mongo", default=False)
    config._require_real_mongo_selected = []
    config._require_real_mongo_skip_seen = False

def pytest_collection_modifyitems(config, items):
    if not config._require_real_mongo_enabled:
        return
    config._require_real_mongo_selected = [
        i for i in items if i.get_closest_marker("real_mongo") is not None
    ]

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    config = item.config
    if not config._require_real_mongo_enabled:
        return
    report = outcome.get_result()
    if item.get_closest_marker("real_mongo") is not None and report.when == "call" and report.skipped:
        config._require_real_mongo_skip_seen = True

def pytest_sessionfinish(session):
    config = session.config
    if not config._require_real_mongo_enabled:
        return
    if not config._require_real_mongo_selected:
        session.exitstatus = 2
        print("\\n--require-real-mongo: no real_mongo tests were selected")
    elif config._require_real_mongo_skip_seen:
        session.exitstatus = 2
        print("\\n--require-real-mongo: a selected real_mongo test was skipped")
"""


def _write_marked(pytester: pytest.Pytester, *, skip: bool) -> None:
    pytester.makeconftest(_PLUGIN_CONFTEST)
    pytester.makepyfile(
        test_sample=f"""
import pytest
pytestmark = pytest.mark.real_mongo

def test_run():
    {'pytest.skip("no mongo")' if skip else 'assert True'}
"""
    )


def test_require_real_mongo_fails_when_no_marked_tests_selected(pytester: pytest.Pytester) -> None:
    pytester.makeconftest(_PLUGIN_CONFTEST)
    pytester.makepyfile(
        test_sample="""
def test_run():
    assert True
"""
    )
    result = pytester.runpytest("-m", "real_mongo", "--require-real-mongo")
    assert result.ret != 0


def test_require_real_mongo_passes_when_marked_tests_run(pytester: pytest.Pytester) -> None:
    _write_marked(pytester, skip=False)
    result = pytester.runpytest("--require-real-mongo")
    assert result.ret == 0
    result.assert_outcomes(passed=1)


def test_require_real_mongo_fails_when_selected_marked_test_skips(pytester: pytest.Pytester) -> None:
    _write_marked(pytester, skip=True)
    result = pytester.runpytest("--require-real-mongo")
    assert result.ret != 0
    result.assert_outcomes(skipped=1)


def test_strict_markers_reject_unknown_marker(pytester: pytest.Pytester) -> None:
    pytester.makeconftest(_PLUGIN_CONFTEST)
    pytester.makepyfile(
        test_unknown="""
import pytest

@pytest.mark.nonexistent_marker
def test_x():
    assert True
"""
    )
    result = pytester.runpytest("--strict-markers")
    assert result.ret != 0