import re
import unicodedata
from .models import CorrectAnswer, ProblemType


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

    if problem_type == ProblemType.MULTI_CHOICE:
        # Split on commas first, then normalize each token individually
        raw_tokens = [token.strip() for token in raw_text.split(',') if token.strip()]
        normalized_tokens = [normalize_token(token) for token in raw_tokens]
        unique_tokens = sorted(list(set(normalized_tokens)))
        normalized_set = unique_tokens
        normalized_text = ','.join(unique_tokens)
        format_ = "set"
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
