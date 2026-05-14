EXTRACTION_PROMPT_VERSION = "2026-05-14.extraction.v1"
GRADING_PROMPT_VERSION = "2026-05-14.grading.v1"

EXTRACTION_SCHEMA_VERSION = "1.0"
GRADING_SCHEMA_VERSION = "1.0"

EXTRACTION_PROMPT_TEMPLATE = """You are extracting a study problem from an image.
Return only JSON that matches the expected schema.

Fields:
- text: the problem statement as plain text
- problemType: one of single-choice, multi-choice, fill-in-the-blank, short-answer
- graphDsl: nullable string for any graph/diagram DSL reconstruction
"""

GRADING_PROMPT_TEMPLATE = """You are grading a short-answer response against a stored answer key.
Return only JSON that matches the expected schema.

Fields:
- isCorrect: boolean
- feedback: concise explanation for the learner
"""
