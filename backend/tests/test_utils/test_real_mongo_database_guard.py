from __future__ import annotations

import pytest

from tests.integration.test_ingestion_atomicity import (
    REAL_MONGO_DATABASE_ENV,
    validate_real_mongo_database_name,
)


# ---------------------------------------------------------------------------
# Pure validation tests — no Mongo I/O.
# ---------------------------------------------------------------------------

VALID_NAMES = [
    "learnloop_test_a",
    "learnloop_test_A1",
    "learnloop_test_run-uuid-42",
    "learnloop_test_deadbeef",
    "learnloop_test_a_b_c",
]


@pytest.mark.parametrize("name", VALID_NAMES)
def test_validate_accepts_sentinel_names(name: str) -> None:
    assert validate_real_mongo_database_name(name) == name


INVALID_NAMES = [
    None,
    "",
    "learnloop",
    "learnlooptest_x",
    "learnloop_test",
    "learnloop_test_",
    "prod",
    "learnloop_test_a.b",
    "learnloop_test_a b",
    "learnloop_test_a\n",
    " learnloop_test_a",
    "learnloop_test_a ",
    "learnloop_test_a\t",
    "LEARNLOOP_TEST_A",
    "learnloop-Test-a",
]


@pytest.mark.parametrize("name", INVALID_NAMES, ids=map(repr, INVALID_NAMES))
def test_validate_rejects_absent_empty_production_and_malformed(name: object) -> None:
    with pytest.raises(RuntimeError, match=REAL_MONGO_DATABASE_ENV):
        validate_real_mongo_database_name(name)  # type: ignore[arg-type]


def test_validate_rejects_substring_without_full_match() -> None:
    # A sentinel substring embedded in a longer unsafe name must not pass.
    with pytest.raises(RuntimeError):
        validate_real_mongo_database_name("xlearnloop_test_ay")