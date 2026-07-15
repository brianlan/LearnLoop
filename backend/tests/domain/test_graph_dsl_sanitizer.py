import pytest

from app.domain.whiteboard.graph_dsl import (
    sanitize_whiteboard_dsl,
    _is_allowed_graph_dsl,
    _split_top_level,
    _strip_quoted_strings,
    _validate_dsl_create_call,
)


class TestStripQuotedStrings:
    def test_removes_single_quoted_string(self) -> None:
        assert _strip_quoted_strings("'hello' world") == " world"

    def test_removes_double_quoted_string(self) -> None:
        assert _strip_quoted_strings('hello "world"') == "hello "

    def test_removes_multiple_quoted_strings(self) -> None:
        assert _strip_quoted_strings("'a' \"b\" 'c'") == "  "

    def test_preserves_unquoted_content(self) -> None:
        assert _strip_quoted_strings("board.create('point', [0, 0])") == "board.create(, [0, 0])"

    def test_handles_empty_string(self) -> None:
        assert _strip_quoted_strings("") == ""


class TestSplitTopLevel:
    def test_splits_by_comma(self) -> None:
        assert _split_top_level("a, b, c", ",") == ["a", "b", "c"]

    def test_respects_brackets(self) -> None:
        assert _split_top_level("[a, b], c", ",") == ["[a, b]", "c"]

    def test_respects_braces(self) -> None:
        assert _split_top_level("{a: 1}, {b: 2}", ",") == ["{a: 1}", "{b: 2}"]

    def test_respects_parentheses(self) -> None:
        assert _split_top_level("(a, b), c", ",") == ["(a, b)", "c"]

    def test_respects_quotes(self) -> None:
        assert _split_top_level("'a, b', c", ",") == ["'a, b'", "c"]

    def test_respects_escaped_quotes(self) -> None:
        assert _split_top_level(r"'a\'b', c", ",") == [r"'a\'b'", "c"]

    def test_returns_none_for_unmatched_bracket(self) -> None:
        assert _split_top_level("[a, b", ",") is None

    def test_returns_none_for_unmatched_quote(self) -> None:
        assert _split_top_level("'a, b", ",") is None

    def test_returns_none_for_extra_closing_bracket(self) -> None:
        assert _split_top_level("a], b", ",") is None

    def test_splits_by_semicolon(self) -> None:
        assert _split_top_level("a; b; c", ";") == ["a", "b", "c"]

    def test_omits_empty_last_part(self) -> None:
        assert _split_top_level("a, b, ", ",") == ["a", "b"]

    def test_returns_empty_list_for_empty_string(self) -> None:
        assert _split_top_level("", ",") == []


class TestValidateDslCreateCall:
    def test_valid_point_creation(self) -> None:
        assert _validate_dsl_create_call("board.create('point', [0, 0])", set()) is True

    def test_valid_segment_with_options(self) -> None:
        assert _validate_dsl_create_call("board.create('segment', [A, B], {strokeWidth: 2})", {"A", "B"}) is True

    def test_rejects_invalid_element_type(self) -> None:
        assert _validate_dsl_create_call("board.create('invalid', [0, 0])", set()) is False

    def test_rejects_too_few_args(self) -> None:
        assert _validate_dsl_create_call("board.create('point')", set()) is False

    def test_rejects_too_many_args(self) -> None:
        assert _validate_dsl_create_call("board.create('point', [0, 0], {}, extra)", set()) is False

    def test_rejects_non_string_element_type(self) -> None:
        assert _validate_dsl_create_call("board.create(point, [0, 0])", set()) is False

    def test_rejects_missing_board_prefix(self) -> None:
        assert _validate_dsl_create_call("create('point', [0, 0])", set()) is False


class TestIsAllowedGraphDsl:
    def test_allows_supported_subset(self) -> None:
        dsl = ";".join([
            "board.setBoundingBox([-1, 2, 6, -2])",
            "var A = board.create('point', [0, 0], {name:'A'})",
            "var B = board.create('point', [5, 0], {name:'B'})",
            "board.create('segment', [A, B], {strokeWidth:2})",
            "board.create('text', [2.5, 0.3, '490米'], {anchorX:'middle', fontSize:12})",
        ])
        assert _is_allowed_graph_dsl(dsl) is True

    def test_rejects_fetch(self) -> None:
        assert _is_allowed_graph_dsl("fetch('/api/private'); board.create('point', [0, 0]);") is False

    def test_rejects_while(self) -> None:
        assert _is_allowed_graph_dsl("while (true) { board.create('point', [0, 0]); }") is False

    def test_rejects_window(self) -> None:
        assert _is_allowed_graph_dsl("window.location = 'https://example.com';") is False

    def test_rejects_new_function(self) -> None:
        assert _is_allowed_graph_dsl("new Function('return document.cookie')();") is False

    def test_rejects_constructor(self) -> None:
        assert _is_allowed_graph_dsl("board.constructor.constructor('return window')();") is False

    def test_rejects_functiongraph(self) -> None:
        assert _is_allowed_graph_dsl("board.create('functiongraph', [function(x) { return x; }, -1, 1]);") is False

    def test_rejects_arithmetic_expressions(self) -> None:
        assert _is_allowed_graph_dsl("board.create('point', [1 + 2, 0]);") is False

    def test_allows_dsl_at_maximum_length(self) -> None:
        prefix = "board.create('text', [0, 0, '"
        suffix = "']);"
        padding_length = 16384 - len(prefix) - len(suffix)
        dsl = f"{prefix}{'x' * padding_length}{suffix}"
        assert len(dsl) == 16384
        assert _is_allowed_graph_dsl(dsl) is True

    def test_rejects_dsl_above_maximum_length(self) -> None:
        assert _is_allowed_graph_dsl("board.create('point', [0, 0]);" * 1000) is False

    def test_rejects_arrow_function_syntax(self) -> None:
        assert _is_allowed_graph_dsl("board.create('point', [0, 0]); var f = () => {};") is False

    def test_rejects_template_literal(self) -> None:
        assert _is_allowed_graph_dsl("board.create('text', [0, 0, `hello`]);") is False

    def test_rejects_line_comment(self) -> None:
        assert _is_allowed_graph_dsl("board.create('point', [0, 0]); // comment") is False

    def test_rejects_block_comment(self) -> None:
        assert _is_allowed_graph_dsl("/* comment */ board.create('point', [0, 0]);") is False

    def test_rejects_increment_decrement(self) -> None:
        assert _is_allowed_graph_dsl("var i = 0; i++; board.create('point', [i, 0]);") is False

    def test_rejects_eval(self) -> None:
        assert _is_allowed_graph_dsl("eval('malicious');") is False

    def test_rejects_document(self) -> None:
        assert _is_allowed_graph_dsl("document.cookie;") is False

    def test_rejects_xmlhttprequest(self) -> None:
        assert _is_allowed_graph_dsl("new XMLHttpRequest();") is False

    def test_allows_bounding_box_only(self) -> None:
        assert _is_allowed_graph_dsl("board.setBoundingBox([-1, 2, 6, -2])") is True

    def test_allows_unnamed_create_calls(self) -> None:
        assert _is_allowed_graph_dsl("board.create('point', [0, 0]);") is True

    def test_allows_declared_name_reuse_in_args(self) -> None:
        dsl = "var A = board.create('point', [0, 0]); board.create('segment', [A, A]);"
        assert _is_allowed_graph_dsl(dsl) is True

    def test_rejects_undeclared_name_in_args(self) -> None:
        assert _is_allowed_graph_dsl("board.create('segment', [A, B]);") is False

    def test_rejects_invalid_bounding_box(self) -> None:
        assert _is_allowed_graph_dsl("board.setBoundingBox([0, 0])") is False

    def test_allows_single_quoted_option_key(self) -> None:
        dsl = "var p = board.create('point', [0, 0], {'name': 'P'});"
        assert _is_allowed_graph_dsl(dsl) is True

    def test_rejects_duplicate_declaration(self) -> None:
        dsl = "var A = board.create('point', [0, 0]); var A = board.create('point', [1, 1]);"
        assert _is_allowed_graph_dsl(dsl) is False

    def test_allows_empty_dsl(self) -> None:
        assert _is_allowed_graph_dsl("") is True


class TestSanitizeWhiteboardDsl:
    def test_returns_none_for_none(self) -> None:
        assert sanitize_whiteboard_dsl(None) is None

    def test_returns_none_for_empty_string(self) -> None:
        assert sanitize_whiteboard_dsl("") is None

    def test_returns_none_for_whitespace_only(self) -> None:
        assert sanitize_whiteboard_dsl("   \n\t  ") is None

    def test_strips_markdown_code_fence(self) -> None:
        raw = "```js\nvar p = board.create('point', [0, 0]);\n```"
        assert sanitize_whiteboard_dsl(raw) == "var p = board.create('point', [0, 0]);"

    def test_strips_plain_code_fence(self) -> None:
        raw = "```\nvar p = board.create('point', [0, 0]);\n```"
        assert sanitize_whiteboard_dsl(raw) == "var p = board.create('point', [0, 0]);"

    def test_strips_initboard_call(self) -> None:
        raw = "var board = JXG.JSXGraph.initBoard('box', {boundingbox: [-5, 5, 5, -5]}); var p = board.create('point', [0, 0]);"
        assert sanitize_whiteboard_dsl(raw) == "var p = board.create('point', [0, 0]);"

    def test_returns_none_for_disallowed_dsl(self) -> None:
        assert sanitize_whiteboard_dsl("fetch('/api/private');") is None

    def test_returns_sanitized_dsl_for_allowed_input(self) -> None:
        raw = "var p = board.create('point', [0, 0]);"
        assert sanitize_whiteboard_dsl(raw) == raw

    def test_strips_initboard_without_semicolon(self) -> None:
        raw = "var board = JXG.JSXGraph.initBoard('box', {boundingbox: [-5, 5, 5, -5]}) var p = board.create('point', [0, 0]);"
        assert sanitize_whiteboard_dsl(raw) == "var p = board.create('point', [0, 0]);"

    def test_returns_none_after_initboard_strips_to_empty(self) -> None:
        raw = "var board = JXG.JSXGraph.initBoard('box', {boundingbox: [-5, 5, 5, -5]});"
        assert sanitize_whiteboard_dsl(raw) is None

    def test_strips_initboard_with_extra_whitespace(self) -> None:
        raw = "var  board  =  JXG.JSXGraph.initBoard(  'box'  )  ;  var p = board.create('point', [0, 0]);"
        assert sanitize_whiteboard_dsl(raw) == "var p = board.create('point', [0, 0]);"
