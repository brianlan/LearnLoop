import pytest
from app.domain import ProblemType, normalize_answer


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


def test_preserve_math_operators():
    result = normalize_answer("x + y = z * (2 / 3)", ProblemType.FILL_IN_THE_BLANK)
    assert result.normalizedText == "x + y = z * (2 / 3)"
