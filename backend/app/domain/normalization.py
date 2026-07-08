import re
import unicodedata
from .models import CorrectAnswer, ProblemType


# Matches single-dollar inline math ($...$) while avoiding display math ($$...$$):
# neither delimiter may be adjacent to another ``$``.
_INLINE_MATH_PATTERN = r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)"
_INLINE_MATH_RE = re.compile(_INLINE_MATH_PATTERN)
_UNSIGNED_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def normalize_extracted_problem_text(text: str) -> str:
    """Normalize inline LaTeX in extracted problem text.

    Applied to VLM-extracted draft text only; the raw extraction text must be
    preserved separately. Two rules run in order:

    1. Unwrap pure unsigned numbers: ``$45$`` -> ``45``, ``$3.14$`` -> ``3.14``.
       Negatives, comma-grouped numbers, percentages, fractions, variables and
       expressions are left wrapped.
    2. Normalize spacing around remaining inline ``$...$``: exactly one ASCII
       space before and after the expression unless it is at the start or end of
       a line.
    """
    text = _unwrap_numeric_inline_math(text)
    text = _normalize_inline_math_spacing(text)
    return text


def _unwrap_numeric_inline_math(text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        if _UNSIGNED_NUMBER_RE.fullmatch(inner):
            return inner
        return match.group(0)

    return _INLINE_MATH_RE.sub(_replace, text)


def _normalize_inline_math_spacing(text: str) -> str:
    # Exactly one space before ``$`` when preceded by a non-space character.
    text = re.sub(r"(?<=\S)[ \t]*(" + _INLINE_MATH_PATTERN + r")", r" \1", text)
    # Exactly one space after ``$`` when followed by a non-space character.
    text = re.sub(r"(" + _INLINE_MATH_PATTERN + r")[ \t]*(?=\S)", r"\1 ", text)
    return text


def compare_answers(normalized: CorrectAnswer, stored_answer: dict, problem_type: ProblemType) -> bool:
    """Compare a normalized answer against the stored correct answer."""
    if problem_type == ProblemType.MULTI_CHOICE:
        return normalized.normalizedSet == list(stored_answer.get("normalizedSet", []))
    else:
        return normalized.normalizedText == str(stored_answer.get("normalizedText", ""))


def normalize_answer(raw_text: str, problem_type: ProblemType) -> CorrectAnswer:
    def normalize_token(text: str) -> str:
        # Step 1: Convert fullwidth ASCII to standard ASCII
        norm = unicodedata.normalize('NFKC', text)
        # Step 2: Convert to lowercase
        norm = norm.lower()
        # Step 3: Trim leading/trailing whitespace
        norm = norm.strip()
        # Step 4: Collapse internal whitespace to single space
        norm = re.sub(r'\s+', ' ', norm)
        # Step 5: Preserve math operators and remove other punctuation
        # Preserved: +, -, =, *, /, ×, ÷, (, ), .
        preserved = r'[+\-=*/×÷().\w\s]'
        norm = re.sub(r'[^' + preserved[1:-1] + r']', '', norm)
        return norm

    def normalize_choice_token(text: str) -> str:
        choice_match = re.match(r'^\s*([A-Za-z]|\d+)\s*[.):\-]?(?:\s|$)', text)
        if choice_match:
            return normalize_token(choice_match.group(1))
        return normalize_token(text)

    if problem_type == ProblemType.MULTI_CHOICE:
        # Split on commas first, then normalize each token individually
        raw_tokens = [token.strip() for token in raw_text.split(',') if token.strip()]
        normalized_tokens = [normalize_choice_token(token) for token in raw_tokens]
        unique_tokens = sorted(list(set(normalized_tokens)))
        normalized_set = unique_tokens
        normalized_text = ','.join(unique_tokens)
        format_ = "set"
    elif problem_type == ProblemType.SINGLE_CHOICE:
        normalized_text = normalize_choice_token(raw_text)
        normalized_set = []
        format_ = "single"
    else:
        normalized_text = normalize_token(raw_text)
        normalized_set = []
        format_ = "single"
    
    return CorrectAnswer(
        display=raw_text,
        normalizedText=normalized_text,
        normalizedSet=normalized_set,
        format=format_
    )
