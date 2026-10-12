"""Graph/whiteboard DSL sanitizer policy.

This module owns the complete existing sanitizer policy that was previously
embedded in the VLM coaching client. It exposes ``sanitize_whiteboard_dsl``,
which validates and cleans a raw whiteboard DSL string so it can safely be
executed as JSXGraph DSL inside the browser sandbox, and
``explain_graph_dsl``, which returns the per-statement reasons a DSL would be
rejected. Both share the same preprocessing and validators, so they cannot
drift: an empty explanation means the sanitizer accepts the input.
"""

from __future__ import annotations

import re

_ALLOWED_DSL_ELEMENT_TYPES = {
    "point",
    "segment",
    "line",
    "arrow",
    "circle",
    "angle",
    "polygon",
    "text",
    "glider",
    "intersection",
    "midpoint",
    "perpendicular",
}

_ALLOWED_DSL_OPTION_KEYS = {
    "anchorX",
    "anchorY",
    "borders",
    "color",
    "dash",
    "face",
    "fillColor",
    "fillOpacity",
    "fixed",
    "fontSize",
    "highlight",
    "label",
    "name",
    "opacity",
    "radius",
    "showInfobox",
    "size",
    "strokeColor",
    "strokeOpacity",
    "strokeWidth",
    "visible",
    "withLabel",
}

_BLOCKED_DSL_TOKENS = {
    "constructor",
    "document",
    "eval",
    "fetch",
    "for",
    "function",
    "globalThis",
    "if",
    "import",
    "localStorage",
    "new",
    "prototype",
    "return",
    "sessionStorage",
    "setInterval",
    "setTimeout",
    "this",
    "while",
    "window",
    "XMLHttpRequest",
    "__proto__",
}

_MAX_DSL_LENGTH = 16384


def _strip_quoted_strings(value: str) -> str:
    return re.sub(r"""'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"""", "", value)


# ponytail: one regex alternation — string literals first so '//' inside quotes
# survives; comments are replaced by a space so token boundaries are preserved.
_STRING_OR_COMMENT = re.compile(
    r"""'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|//[^\r\n\u2028\u2029]*|/\*.*?\*/""",
    re.DOTALL,
)


def _strip_comments(value: str) -> str:
    return _STRING_OR_COMMENT.sub(
        lambda match: match.group(0) if match.group(0)[0] in {"'", '"'} else " ",
        value,
    )


def _preprocess_graph_dsl_source(stripped: str) -> str:
    """Shared preprocessing for the sanitizer and the explainer (issue #692).

    Fences, then initBoard boilerplate, then comments — one implementation so
    the two public functions cannot drift. ``stripped`` must already be
    outer-stripped and non-empty; each public function keeps its own
    missing/empty response.
    """
    # Remove markdown code fences: ```js ... ``` or ``` ... ```
    fence_match = re.match(r"^```(?:\w*)\n?(.*?)\n?```$", stripped, re.DOTALL)
    if fence_match:
        stripped = fence_match.group(1).strip()

    # Strip initBoard calls — board already exists in the sandbox
    stripped = re.sub(r"var\s+\w+\s*=\s*JXG\.JSXGraph\.initBoard\([^)]*\)\s*;?", "", stripped)

    # Strip comments before the token scan so orphaned `//` debris left behind
    # by initBoard removal cannot fail the blocked-pattern scan.
    return _strip_comments(stripped).strip()


def _split_top_level(value: str, delimiter: str) -> list[str] | None:
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue

        if char in {"'", '"'}:
            quote = char
        elif char in {"[", "{", "("}:
            depth += 1
        elif char in {"]", "}", ")"}:
            depth -= 1
            if depth < 0:
                return None
        elif char == delimiter and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1

    if quote or depth != 0:
        return None

    last = value[start:].strip()
    if last:
        parts.append(last)
    return parts


def _is_js_string_literal(value: str) -> bool:
    return bool(re.fullmatch(r"""'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"""", value))


def _validate_dsl_object(value: str, declared_names: set[str]) -> str | None:
    if not value.startswith("{") or not value.endswith("}"):
        return "expected an object literal"
    inner = value[1:-1].strip()
    if not inner:
        return None
    entries = _split_top_level(inner, ",")
    if entries is None:
        return "malformed object literal"
    for entry in entries:
        pair = _split_top_level(entry, ":")
        if pair is None or len(pair) != 2:
            return "malformed object entry"
        key = pair[0].strip().strip("'\"")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            return f"malformed option key '{key}'"
        if key not in _ALLOWED_DSL_OPTION_KEYS:
            return f"unknown option key '{key}'"
        reason = _validate_dsl_value(pair[1], declared_names)
        if reason:
            return reason
    return None


def _validate_dsl_value(value: str, declared_names: set[str]) -> str | None:
    stripped = value.strip()
    if not stripped:
        return "empty value"
    if re.fullmatch(r"-?\d+(?:\.\d+)?", stripped):
        return None
    if _is_js_string_literal(stripped):
        return None
    if stripped in {"true", "false", "null"}:
        return None
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", stripped):
        if stripped not in declared_names:
            return f"undeclared name '{stripped}'"
        return None
    if stripped.startswith("[") and stripped.endswith("]"):
        inner = stripped[1:-1].strip()
        if not inner:
            return None
        items = _split_top_level(inner, ",")
        if items is None:
            return "malformed array literal"
        for item in items:
            reason = _validate_dsl_value(item, declared_names)
            if reason:
                return reason
        return None
    if stripped.startswith("{") and stripped.endswith("}"):
        return _validate_dsl_object(stripped, declared_names)
    return "malformed value"


def _validate_dsl_create_call(call: str, declared_names: set[str]) -> str | None:
    if not call.startswith("board.create(") or not call.endswith(")"):
        return "malformed board.create call"
    args = _split_top_level(call[len("board.create(") : -1], ",")
    if args is None:
        return "malformed board.create arguments"
    if len(args) < 2 or len(args) > 3:
        return "board.create expects 2 or 3 arguments"
    element_type_arg = args[0].strip()
    if not _is_js_string_literal(element_type_arg):
        return "element type must be a string literal"
    element_type = element_type_arg[1:-1]
    if element_type not in _ALLOWED_DSL_ELEMENT_TYPES:
        return f"disallowed element type '{element_type}'"
    reason = _validate_dsl_value(args[1], declared_names)
    if reason:
        return reason
    if len(args) == 3:
        reason = _validate_dsl_object(args[2].strip(), declared_names)
        if reason:
            return reason
    return None


_DANGEROUS_PATTERNS: tuple[tuple[str, str], ...] = (
    ("=>", "arrow function syntax '=>'"),
    ("`", "template literal backtick"),
    ("//", "line comment '//'"),
    ("/*", "block comment start '/*'"),
    ("*/", "block comment end '*/'"),
    ("++", "increment operator '++'"),
    ("--", "decrement operator '--'"),
)


def _statement_label(statement: str) -> str:
    # ponytail: bound stored failure evidence; DSL statements are rarely longer.
    return statement if len(statement) <= 120 else f"{statement[:117]}..."


def _explain_statement(statement: str, declared_names: set[str]) -> str | None:
    bbox_match = re.fullmatch(r"board\.setBoundingBox\((.*)\)", statement)
    if bbox_match:
        value = bbox_match.group(1).strip()
        parts = _split_top_level(value, ",")
        if (
            parts is None
            or len(parts) not in (1, 2)
            or not (parts[0].startswith("[") and parts[0].endswith("]"))
        ):
            return "malformed board.setBoundingBox call"
        numbers = _split_top_level(parts[0][1:-1], ",")
        if (
            numbers is None
            or len(numbers) != 4
            or not all(re.fullmatch(r"-?\d+(?:\.\d+)?", part.strip()) for part in numbers)
        ):
            return "malformed board.setBoundingBox call"
        if len(parts) == 2 and parts[1] not in {"true", "false"}:
            return "malformed board.setBoundingBox call"
        return None

    declaration_match = re.fullmatch(
        r"var\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(board\.create\(.*\))",
        statement,
    )
    if declaration_match:
        name = declaration_match.group(1)
        if name in declared_names:
            return f"name '{name}' is declared more than once"
        reason = _validate_dsl_create_call(declaration_match.group(2), declared_names)
        if reason:
            return reason
        declared_names.add(name)
        return None

    if statement.startswith("board.create("):
        return _validate_dsl_create_call(statement, declared_names)

    return "statement is not an allowed declaration or board.create call"


def _explain_graph_dsl_body(stripped: str) -> list[str]:
    """Rejection reasons for already-preprocessed DSL; empty means compliant."""
    if len(stripped) > _MAX_DSL_LENGTH:
        return [f"exceeds the maximum DSL length of {_MAX_DSL_LENGTH} characters"]

    reasons: list[str] = []
    unquoted = _strip_quoted_strings(stripped)
    for pattern, label in _DANGEROUS_PATTERNS:
        if pattern in unquoted:
            reasons.append(f"disallowed syntax: {label}")
    # ponytail: sorted — _BLOCKED_DSL_TOKENS is a set, and the reason order
    # must not depend on the process hash seed (issue #692).
    for token in sorted(_BLOCKED_DSL_TOKENS):
        if re.search(rf"\b{re.escape(token)}\b", unquoted, re.IGNORECASE):
            reasons.append(f"disallowed token '{token}'")

    statements = _split_top_level(stripped, ";")
    if statements is None:
        reasons.append("unbalanced brackets or an unterminated string literal")
        return reasons

    declared_names: set[str] = set()
    for statement in [part for part in statements if part]:
        reason = _explain_statement(statement, declared_names)
        if reason:
            reasons.append(f"rejected statement '{_statement_label(statement)}': {reason}")
    return reasons


def _is_allowed_graph_dsl(dsl: str) -> bool:
    return not _explain_graph_dsl_body(dsl)


def explain_graph_dsl(dsl: str | None) -> list[str]:
    """Return per-statement reasons why ``dsl`` would be rejected.

    Runs the same preprocessing as ``sanitize_whiteboard_dsl`` and shares its
    validators, so the two cannot drift: an empty list means the sanitizer
    accepts the input. Reasons name the offending statement, option key,
    element type, or token so failures stay explainable (issue #692).
    """
    if dsl is None:
        return ["graphDsl is missing"]

    stripped = dsl.strip()
    if not stripped:
        return ["graphDsl is empty"]

    stripped = _preprocess_graph_dsl_source(stripped)
    if not stripped:
        return ["no DSL statements remain after comment and fence stripping"]

    return _explain_graph_dsl_body(stripped)


def sanitize_whiteboard_dsl(dsl: str | None) -> str | None:
    """Return cleaned DSL or None if the input is unsafe, empty, or malformed.

    The policy strips markdown code fences, removes ``JXG.JSXGraph.initBoard``
    boilerplate, strips string-literal-aware comments, then validates the
    remaining DSL against the allowed grammar, element types, option keys,
    and blocked-token list.
    """
    if dsl is None:
        return None

    stripped = _preprocess_graph_dsl_source(dsl.strip())
    if not stripped:
        return None

    if not _is_allowed_graph_dsl(stripped):
        return None

    return stripped


def sanitize_graph_dsl_for_storage(dsl: str | None) -> tuple[str | None, list[str]]:
    """Write-path seam (issue #694): one sanitize decision for every writer.

    Storage strategy: returns ``(canonical, [])`` when the DSL is absent or
    compliant, and ``(None, reasons)`` when it is present but non-compliant.
    Absent input (``None`` or blank) is not a failure. Human-review call
    sites reject on a non-empty reason list instead of storing ``None``.
    """
    if dsl is None or not dsl.strip():
        return None, []
    canonical = sanitize_whiteboard_dsl(dsl)
    if canonical is None:
        return None, explain_graph_dsl(dsl)
    return canonical, []
