"""Graph/whiteboard DSL sanitizer policy.

This module owns the complete existing sanitizer policy that was previously
embedded in the VLM coaching client. It exposes a single public function,
``sanitize_whiteboard_dsl``, which validates and cleans a raw whiteboard DSL
string so it can safely be executed as JSXGraph DSL inside the browser sandbox.
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


def _validate_dsl_object(value: str, declared_names: set[str]) -> bool:
    if not value.startswith("{") or not value.endswith("}"):
        return False
    inner = value[1:-1].strip()
    if not inner:
        return True
    entries = _split_top_level(inner, ",")
    if entries is None:
        return False
    for entry in entries:
        pair = _split_top_level(entry, ":")
        if pair is None or len(pair) != 2:
            return False
        key = pair[0].strip().strip('""')
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or key not in _ALLOWED_DSL_OPTION_KEYS:
            return False
        if not _validate_dsl_value(pair[1], declared_names):
            return False
    return True


def _validate_dsl_value(value: str, declared_names: set[str]) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if re.fullmatch(r"-?\d+(?:\.\d+)?", stripped):
        return True
    if _is_js_string_literal(stripped):
        return True
    if stripped in {"true", "false", "null"}:
        return True
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", stripped):
        return stripped in declared_names
    if stripped.startswith("[") and stripped.endswith("]"):
        inner = stripped[1:-1].strip()
        if not inner:
            return True
        items = _split_top_level(inner, ",")
        return items is not None and all(_validate_dsl_value(item, declared_names) for item in items)
    if stripped.startswith("{") and stripped.endswith("}"):
        return _validate_dsl_object(stripped, declared_names)
    return False


def _validate_dsl_create_call(call: str, declared_names: set[str]) -> bool:
    if not call.startswith("board.create(") or not call.endswith(")"):
        return False
    args = _split_top_level(call[len("board.create(") : -1], ",")
    if args is None or len(args) < 2 or len(args) > 3:
        return False
    element_type_arg = args[0].strip()
    if not _is_js_string_literal(element_type_arg):
        return False
    element_type = element_type_arg[1:-1]
    if element_type not in _ALLOWED_DSL_ELEMENT_TYPES:
        return False
    if not _validate_dsl_value(args[1], declared_names):
        return False
    if len(args) == 3 and not _validate_dsl_object(args[2].strip(), declared_names):
        return False
    return True


def _is_allowed_graph_dsl(dsl: str) -> bool:
    if len(dsl) > _MAX_DSL_LENGTH:
        return False
    unquoted = _strip_quoted_strings(dsl)
    if re.search(r"=>|`|//|/\*|\*/|\+\+|--", unquoted):
        return False
    for token in _BLOCKED_DSL_TOKENS:
        if re.search(rf"\b{re.escape(token)}\b", unquoted, re.IGNORECASE):
            return False

    statements = _split_top_level(dsl, ";")
    if statements is None:
        return False

    declared_names: set[str] = set()
    for statement in [part for part in statements if part]:
        bbox_match = re.fullmatch(r"board\.setBoundingBox\((.*)\)", statement)
        if bbox_match:
            value = bbox_match.group(1).strip()
            parts = (
                _split_top_level(value[1:-1], ",")
                if value.startswith("[") and value.endswith("]")
                else None
            )
            if parts is None or len(parts) != 4 or not all(re.fullmatch(r"-?\d+(?:\.\d+)?", part.strip()) for part in parts):
                return False
            continue

        declaration_match = re.fullmatch(
            r"var\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(board\.create\(.*\))",
            statement,
        )
        if declaration_match:
            name = declaration_match.group(1)
            if name in declared_names:
                return False
            if not _validate_dsl_create_call(declaration_match.group(2), declared_names):
                return False
            declared_names.add(name)
            continue

        if statement.startswith("board.create("):
            if not _validate_dsl_create_call(statement, declared_names):
                return False
            continue

        return False

    return True


def sanitize_whiteboard_dsl(dsl: str | None) -> str | None:
    """Return cleaned DSL or None if the input is unsafe, empty, or malformed.

    The policy strips markdown code fences, removes ``JXG.JSXGraph.initBoard``
    boilerplate, then validates the remaining DSL against the allowed grammar,
    element types, option keys, and blocked-token list.
    """
    if dsl is None:
        return None

    stripped = dsl.strip()
    if not stripped:
        return None

    # Remove markdown code fences: ```js ... ``` or ``` ... ```
    fence_match = re.match(r"^```(?:\w*)\n?(.*?)\n?```$", stripped, re.DOTALL)
    if fence_match:
        stripped = fence_match.group(1).strip()

    # Strip initBoard calls — board already exists in the sandbox
    stripped = re.sub(r"var\s+\w+\s*=\s*JXG\.JSXGraph\.initBoard\([^)]*\)\s*;?", "", stripped)
    stripped = stripped.strip()

    if stripped and not _is_allowed_graph_dsl(stripped):
        return None

    return stripped if stripped else None
