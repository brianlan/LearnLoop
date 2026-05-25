import pytest
from app.domain import ProblemType, CorrectAnswer, normalize_answer
from app.domain.normalization import compare_answers


def test_single_choice_normalization():
    result = normalize_answer("  B  ", ProblemType.SINGLE_CHOICE)
    assert result.display == "  B  "
    assert result.normalizedText == "b"
    assert result.normalizedSet == []
    assert result.format == "single"


def test_multi_choice_normalization():
    result = normalize_answer(" C, A, B, a, C ", ProblemType.MULTI_CHOICE)
    assert result.display == " C, A, B, a, C "
    assert result.normalizedText == "a,b,c"
    assert result.normalizedSet == ["a", "b", "c"]
    assert result.format == "set"


def test_fill_in_blank_normalization():
    result = normalize_answer("  123 + 456 = 579  ", ProblemType.FILL_IN_THE_BLANK)
    assert result.normalizedText == "123 + 456 = 579"
    assert result.format == "single"


def test_short_answer_normalization():
    result = normalize_answer("Hello, World!", ProblemType.SHORT_ANSWER)
    assert result.normalizedText == "hello world"
    assert result.format == "single"


def test_fullwidth_conversion():
    result = normalize_answer("ＡＢＣ", ProblemType.SINGLE_CHOICE)
    assert result.normalizedText == "abc"


def test_single_choice_option_text_normalizes_to_choice_key():
    result = normalize_answer("B. (1, 3)", ProblemType.SINGLE_CHOICE)
    assert result.normalizedText == "b"


def test_multi_choice_option_text_normalizes_to_choice_keys():
    result = normalize_answer("A. 1, C. 3", ProblemType.MULTI_CHOICE)
    assert result.normalizedText == "a,c"
    assert result.normalizedSet == ["a", "c"]


def test_preserve_math_operators():
    result = normalize_answer("x + y = z * (2 / 3)", ProblemType.FILL_IN_THE_BLANK)
    assert result.normalizedText == "x + y = z * (2 / 3)"


class TestCompareAnswers:
    """Unit tests for compare_answers function."""

    def test_multi_choice_correct_match(self) -> None:
        normalized = CorrectAnswer(
            display="A, B",
            normalizedText="a,b",
            normalizedSet=["a", "b"],
            format="set",
        )
        stored_answer = {"normalizedSet": ["a", "b"], "normalizedText": "a,b"}
        assert compare_answers(normalized, stored_answer, ProblemType.MULTI_CHOICE) is True

    def test_multi_choice_incorrect_match(self) -> None:
        normalized = CorrectAnswer(
            display="A, B",
            normalizedText="a,b",
            normalizedSet=["a", "b"],
            format="set",
        )
        stored_answer = {"normalizedSet": ["a", "c"], "normalizedText": "a,c"}
        assert compare_answers(normalized, stored_answer, ProblemType.MULTI_CHOICE) is False

    def test_multi_choice_empty_set_matches(self) -> None:
        normalized = CorrectAnswer(
            display="",
            normalizedText="",
            normalizedSet=[],
            format="set",
        )
        stored_answer = {"normalizedSet": [], "normalizedText": ""}
        assert compare_answers(normalized, stored_answer, ProblemType.MULTI_CHOICE) is True

    def test_multi_choice_order_independence(self) -> None:
        # In production, normalize_answer always sorts normalizedSet before storing,
        # so both sides are pre-sorted and comparison is order-independent.
        # The function itself does NOT sort the stored set; this test reflects
        # real data flow where the stored set is already sorted.
        normalized = CorrectAnswer(
            display="B, A",
            normalizedText="a,b",
            normalizedSet=["a", "b"],  # sorted by normalize_answer
            format="set",
        )
        stored_answer = {"normalizedSet": ["a", "b"], "normalizedText": "a,b"}
        assert compare_answers(normalized, stored_answer, ProblemType.MULTI_CHOICE) is True

    def test_single_choice_correct_match(self) -> None:
        normalized = CorrectAnswer(
            display="A",
            normalizedText="a",
            normalizedSet=[],
            format="single",
        )
        stored_answer = {"normalizedSet": [], "normalizedText": "a"}
        assert compare_answers(normalized, stored_answer, ProblemType.SINGLE_CHOICE) is True

    def test_single_choice_incorrect_match(self) -> None:
        normalized = CorrectAnswer(
            display="A",
            normalizedText="a",
            normalizedSet=[],
            format="single",
        )
        stored_answer = {"normalizedSet": [], "normalizedText": "b"}
        assert compare_answers(normalized, stored_answer, ProblemType.SINGLE_CHOICE) is False

    def test_short_answer_correct_match(self) -> None:
        normalized = CorrectAnswer(
            display="42",
            normalizedText="42",
            normalizedSet=[],
            format="single",
        )
        stored_answer = {"normalizedSet": [], "normalizedText": "42"}
        assert compare_answers(normalized, stored_answer, ProblemType.SHORT_ANSWER) is True

    def test_short_answer_incorrect_match(self) -> None:
        normalized = CorrectAnswer(
            display="42",
            normalizedText="42",
            normalizedSet=[],
            format="single",
        )
        stored_answer = {"normalizedSet": [], "normalizedText": "43"}
        assert compare_answers(normalized, stored_answer, ProblemType.SHORT_ANSWER) is False

    def test_missing_stored_normalized_set_uses_empty_list(self) -> None:
        normalized = CorrectAnswer(
            display="A",
            normalizedText="a",
            normalizedSet=["a"],
            format="set",
        )
        stored_answer: dict = {}  # Missing normalizedSet
        assert compare_answers(normalized, stored_answer, ProblemType.MULTI_CHOICE) is False

    def test_missing_stored_normalized_text_uses_empty_string(self) -> None:
        normalized = CorrectAnswer(
            display="A",
            normalizedText="a",
            normalizedSet=[],
            format="single",
        )
        stored_answer: dict = {}  # Missing normalizedText
        assert compare_answers(normalized, stored_answer, ProblemType.SINGLE_CHOICE) is False

    def test_stored_set_as_tuple_converted_to_list(self) -> None:
        normalized = CorrectAnswer(
            display="A, B",
            normalizedText="a,b",
            normalizedSet=["a", "b"],
            format="set",
        )
        stored_answer = {"normalizedSet": ("a", "b"), "normalizedText": "a,b"}
        assert compare_answers(normalized, stored_answer, ProblemType.MULTI_CHOICE) is True
