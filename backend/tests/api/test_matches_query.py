"""Tests for matches_query utility function."""
from __future__ import annotations

from tests.api.conftest import matches_query


def test_matches_query_simple_equality():
    doc = {"name": "test", "value": 42}
    assert matches_query(doc, {"name": "test"})
    assert not matches_query(doc, {"name": "other"})


def test_matches_query_nested_key():
    doc = {"correctAnswer": {"display": "42"}}
    assert matches_query(doc, {"correctAnswer.display": "42"})
    assert not matches_query(doc, {"correctAnswer.display": "other"})


def test_matches_query_in_operator():
    doc = {"status": "active"}
    assert matches_query(doc, {"status": {"$in": ["active", "pending"]}})
    assert not matches_query(doc, {"status": {"$in": ["deleted"]}})


def test_matches_query_ne_operator():
    doc = {"value": 10}
    assert matches_query(doc, {"value": {"$ne": 5}})
    assert not matches_query(doc, {"value": {"$ne": 10}})


def test_matches_query_exists_operator():
    doc = {"name": "test"}
    assert matches_query(doc, {"name": {"$exists": True}})
    assert not matches_query(doc, {"name": {"$exists": False}})
    assert not matches_query(doc, {"missing": {"$exists": True}})
    assert matches_query(doc, {"missing": {"$exists": False}})


def test_matches_query_combined_operators():
    """Test that all operators in a dict are evaluated (AND logic)."""
    doc = {"correctAnswer": {"display": "42"}}

    # Both conditions pass
    assert matches_query(doc, {"correctAnswer.display": {"$exists": True, "$ne": ""}})

    # $ne fails (display is "42", not "other")
    assert not matches_query(doc, {"correctAnswer.display": {"$exists": True, "$ne": "42"}})

    # $exists fails for missing key
    doc_missing = {"correctAnswer": {"display": ""}}
    assert matches_query(doc_missing, {"correctAnswer.display": {"$exists": True}})
    assert not matches_query(doc_missing, {"correctAnswer.display": {"$exists": True, "$ne": ""}})


def test_matches_query_combined_in_and_ne():
    """Test combining $in and $ne operators."""
    doc = {"status": "active"}
    # Note: This is a valid test case even if MongoDB semantics might differ
    # We're testing our fake implementation
    assert matches_query(doc, {"status": {"$in": ["active", "pending"], "$ne": "deleted"}})
    assert not matches_query(doc, {"status": {"$in": ["active", "pending"], "$ne": "active"}})
