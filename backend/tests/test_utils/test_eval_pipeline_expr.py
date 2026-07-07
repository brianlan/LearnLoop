"""Characterization tests for _eval_pipeline_expr.

These tests lock down the current behavior of the aggregation expression
evaluator used by FakeCollection's pipeline-style update operations. They
are written against the existing implementation in tests.conftest and
must continue to pass after the helper is moved to tests.test_utils.db_fakes.
"""
from __future__ import annotations

from tests.test_utils.db_fakes import _eval_pipeline_expr


# ---------------------------------------------------------------------------
# $eq
# ---------------------------------------------------------------------------

def test_eq_returns_true_when_equal() -> None:
    doc = {"status": "active"}
    expr = {"$eq": ["$status", "active"]}
    assert _eval_pipeline_expr(expr, doc) is True


def test_eq_returns_false_when_not_equal() -> None:
    doc = {"status": "active"}
    expr = {"$eq": ["$status", "deleted"]}
    assert _eval_pipeline_expr(expr, doc) is False


def test_eq_with_plain_values() -> None:
    expr = {"$eq": [42, 42]}
    assert _eval_pipeline_expr(expr, {}) is True
    assert _eval_pipeline_expr({"$eq": [42, 43]}, {}) is False


# ---------------------------------------------------------------------------
# $ne
# ---------------------------------------------------------------------------

def test_ne_returns_true_when_not_equal() -> None:
    doc = {"status": "active"}
    expr = {"$ne": ["$status", "deleted"]}
    assert _eval_pipeline_expr(expr, doc) is True


def test_ne_returns_false_when_equal() -> None:
    doc = {"status": "active"}
    expr = {"$ne": ["$status", "active"]}
    assert _eval_pipeline_expr(expr, doc) is False


# ---------------------------------------------------------------------------
# $cond
# ---------------------------------------------------------------------------

def test_cond_true_branch() -> None:
    doc = {"value": 10}
    expr = {"$cond": [{"$eq": ["$value", 10]}, "is_ten", "not_ten"]}
    assert _eval_pipeline_expr(expr, doc) == "is_ten"


def test_cond_false_branch() -> None:
    doc = {"value": 5}
    expr = {"$cond": [{"$eq": ["$value", 10]}, "is_ten", "not_ten"]}
    assert _eval_pipeline_expr(expr, doc) == "not_ten"


def test_cond_truthy_non_boolean_condition() -> None:
    """A truthy non-boolean condition selects the then-branch."""
    doc = {"items": [1, 2, 3]}
    expr = {"$cond": ["$items", "has_items", "empty"]}
    assert _eval_pipeline_expr(expr, doc) == "has_items"


def test_cond_falsy_non_boolean_condition() -> None:
    """A falsy non-boolean condition selects the else-branch."""
    doc = {"items": []}
    expr = {"$cond": ["$items", "has_items", "empty"]}
    assert _eval_pipeline_expr(expr, doc) == "empty"


# ---------------------------------------------------------------------------
# $map
# ---------------------------------------------------------------------------

def test_map_transforms_array() -> None:
    doc = {"items": [1, 2, 3]}
    expr = {
        "$map": {
            "input": "$items",
            "as": "num",
            "in": "$$num",
        }
    }
    assert _eval_pipeline_expr(expr, doc) == [1, 2, 3]


def test_map_with_field_reference() -> None:
    doc = {"items": [{"name": "a"}, {"name": "b"}]}
    expr = {
        "$map": {
            "input": "$items",
            "as": "item",
            "in": "$$item.name",
        }
    }
    # $$item resolves to doc["$$item"] which is the dict {"name": "a"}
    # Then .name is NOT evaluated by _eval_pipeline_expr — the full
    # "$$item.name" string is looked up via doc.get("$$item.name") which
    # is None.  This is a characterization of the actual behavior.
    assert _eval_pipeline_expr(expr, doc) == [None, None]


def test_map_with_eq_expression() -> None:
    doc = {"items": [1, 2, 3]}
    expr = {
        "$map": {
            "input": "$items",
            "as": "num",
            "in": {"$eq": ["$$num", 2]},
        }
    }
    assert _eval_pipeline_expr(expr, doc) == [False, True, False]


# ---------------------------------------------------------------------------
# $filter
# ---------------------------------------------------------------------------

def test_filter_keeps_matching_items() -> None:
    doc = {"items": [1, 2, 3, 4]}
    expr = {
        "$filter": {
            "input": "$items",
            "as": "num",
            "cond": {"$eq": ["$$num", 2]},
        }
    }
    assert _eval_pipeline_expr(expr, doc) == [2]


def test_filter_with_gt_condition() -> None:
    """$$num is truthy for non-zero numbers, so cond evaluates via $eq."""
    doc = {"items": [10, 20, 30]}
    expr = {
        "$filter": {
            "input": "$items",
            "as": "num",
            "cond": {"$eq": ["$$num", 30]},
        }
    }
    assert _eval_pipeline_expr(expr, doc) == [30]


def test_filter_returns_empty_when_no_match() -> None:
    doc = {"items": [1, 2, 3]}
    expr = {
        "$filter": {
            "input": "$items",
            "as": "num",
            "cond": {"$eq": ["$$num", 99]},
        }
    }
    assert _eval_pipeline_expr(expr, doc) == []


# ---------------------------------------------------------------------------
# $$ variables
# ---------------------------------------------------------------------------

def test_double_dollar_variable_lookup() -> None:
    """$$name looks up doc["$$name"] (the full $$-prefixed string is the key)."""
    doc = {"$$item": "hello"}
    assert _eval_pipeline_expr("$$item", doc) == "hello"


def test_double_dollar_variable_missing_returns_none() -> None:
    assert _eval_pipeline_expr("$$missing", {}) is None


def test_double_dollar_variable_in_scoped_doc() -> None:
    """$map creates scoped docs with $$-prefixed keys."""
    doc = {"items": [1, 2]}
    expr = {
        "$map": {
            "input": "$items",
            "as": "x",
            "in": "$$x",
        }
    }
    assert _eval_pipeline_expr(expr, doc) == [1, 2]


# ---------------------------------------------------------------------------
# $ field references
# ---------------------------------------------------------------------------

def test_single_dollar_field_lookup() -> None:
    doc = {"name": "test", "value": 42}
    assert _eval_pipeline_expr("$name", doc) == "test"
    assert _eval_pipeline_expr("$value", doc) == 42


def test_single_dollar_missing_field_returns_none() -> None:
    assert _eval_pipeline_expr("$missing", {}) is None


def test_single_dollar_with_dotted_path_returns_none() -> None:
    """$a.b looks up doc["a.b"] (the full string after $ is the key, not nested)."""
    doc = {"a": {"b": 1}}
    # doc.get("a.b") is None — dotted paths are NOT resolved for $ refs
    assert _eval_pipeline_expr("$a.b", doc) is None


# ---------------------------------------------------------------------------
# Plain values
# ---------------------------------------------------------------------------

def test_plain_integer() -> None:
    assert _eval_pipeline_expr(42, {}) == 42


def test_plain_string_without_dollar() -> None:
    assert _eval_pipeline_expr("hello", {}) == "hello"


def test_plain_list() -> None:
    assert _eval_pipeline_expr([1, 2, 3], {}) == [1, 2, 3]


def test_plain_dict_without_operators() -> None:
    """A dict without recognized operators is returned as-is."""
    expr = {"key": "value", "$unknown": "something"}
    result = _eval_pipeline_expr(expr, {})
    assert result == {"key": "value", "$unknown": "something"}


def test_none_value() -> None:
    assert _eval_pipeline_expr(None, {}) is None


def test_boolean_value() -> None:
    assert _eval_pipeline_expr(True, {}) is True
    assert _eval_pipeline_expr(False, {}) is False


# ---------------------------------------------------------------------------
# Nested expressions
# ---------------------------------------------------------------------------

def test_nested_cond_inside_eq() -> None:
    """$cond result used as argument to $eq."""
    doc = {"value": 10}
    expr = {
        "$eq": [
            {"$cond": [{"$eq": ["$value", 10]}, "match", "no_match"]},
            "match",
        ]
    }
    assert _eval_pipeline_expr(expr, doc) is True


def test_nested_map_inside_eq() -> None:
    """$map result compared with a list."""
    doc = {"items": [1, 2]}
    expr = {
        "$eq": [
            {"$map": {"input": "$items", "as": "n", "in": "$$n"}},
            [1, 2],
        ]
    }
    assert _eval_pipeline_expr(expr, doc) is True


def test_nested_ne_inside_cond() -> None:
    doc = {"status": "active"}
    expr = {
        "$cond": [
            {"$ne": ["$status", "deleted"]},
            "alive",
            "dead",
        ]
    }
    assert _eval_pipeline_expr(expr, doc) == "alive"


def test_nested_filter_inside_eq() -> None:
    doc = {"items": [1, 2, 3]}
    expr = {
        "$eq": [
            {
                "$filter": {
                    "input": "$items",
                    "as": "n",
                    "cond": {"$eq": ["$$n", 2]},
                }
            },
            [2],
        ]
    }
    assert _eval_pipeline_expr(expr, doc) is True
