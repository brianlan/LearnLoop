import pytest

from app.domain.whiteboard.graph_dsl import (
    explain_graph_dsl,
    sanitize_graph_dsl_for_storage,
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
    # The validator returns the rejection reason (None when valid) so the
    # sanitizer and explain_graph_dsl share one grammar (issue #692).
    def test_valid_point_creation(self) -> None:
        assert _validate_dsl_create_call("board.create('point', [0, 0])", set()) is None

    def test_valid_segment_with_options(self) -> None:
        assert (
            _validate_dsl_create_call(
                "board.create('segment', [A, B], {strokeWidth: 2})", {"A", "B"}
            )
            is None
        )

    def test_rejects_invalid_element_type(self) -> None:
        reason = _validate_dsl_create_call("board.create('invalid', [0, 0])", set())
        assert reason == "disallowed element type 'invalid'"

    def test_rejects_too_few_args(self) -> None:
        assert _validate_dsl_create_call("board.create('point')", set()) is not None

    def test_rejects_too_many_args(self) -> None:
        assert _validate_dsl_create_call("board.create('point', [0, 0], {}, extra)", set()) is not None

    def test_rejects_non_string_element_type(self) -> None:
        assert _validate_dsl_create_call("board.create(point, [0, 0])", set()) is not None

    def test_rejects_missing_board_prefix(self) -> None:
        assert _validate_dsl_create_call("create('point', [0, 0])", set()) is not None

    def test_rejects_unknown_option_key(self) -> None:
        reason = _validate_dsl_create_call(
            "board.create('polygon', [0, 0, 1, 0], {borders: {evil: 1}})", set()
        )
        assert reason == "unknown option key 'evil'"

    def test_rejects_undeclared_name(self) -> None:
        reason = _validate_dsl_create_call("board.create('segment', [A, B])", set())
        assert reason == "undeclared name 'A'"


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

    def test_allows_bounding_box_with_boolean_keep_aspect_ratio(self) -> None:
        assert _is_allowed_graph_dsl("board.setBoundingBox([-1, 7, 11, -1], true)") is True
        assert _is_allowed_graph_dsl("board.setBoundingBox([-1, 7, 11, -1], false)") is True

    def test_rejects_three_argument_bounding_box(self) -> None:
        assert _is_allowed_graph_dsl("board.setBoundingBox([-1, 7, 11, -1], true, 'keep')") is False

    def test_rejects_non_boolean_second_argument(self) -> None:
        assert _is_allowed_graph_dsl("board.setBoundingBox([-1, 7, 11, -1], 'keep')") is False
        assert _is_allowed_graph_dsl("board.setBoundingBox([-1, 7, 11, -1], 1)") is False
        assert _is_allowed_graph_dsl("board.setBoundingBox([-1, 7, 11, -1], keep)") is False

    def test_rejects_capitalized_true_in_bounding_box(self) -> None:
        assert _is_allowed_graph_dsl("board.setBoundingBox([-1, 7, 11, -1], True)") is False

    def test_rejects_non_bracketed_bounding_box(self) -> None:
        assert _is_allowed_graph_dsl("board.setBoundingBox(-1, 7, 11, -1)") is False

    def test_allows_single_quoted_option_key(self) -> None:
        dsl = "var p = board.create('point', [0, 0], {'name': 'P'});"
        assert _is_allowed_graph_dsl(dsl) is True

    def test_rejects_duplicate_declaration(self) -> None:
        dsl = "var A = board.create('point', [0, 0]); var A = board.create('point', [1, 1]);"
        assert _is_allowed_graph_dsl(dsl) is False

    def test_allows_empty_dsl(self) -> None:
        assert _is_allowed_graph_dsl("") is True

    def test_allows_borders_nested_option(self) -> None:
        dsl = (
            "var A = board.create('polygon', [0, 0, 5, 0, 2.5, 4], "
            "{fillColor:'#a8d4f050', borders:{strokeColor:'#000000', strokeWidth:1}});"
        )
        assert _is_allowed_graph_dsl(dsl) is True

    def test_rejects_unknown_key_inside_borders(self) -> None:
        dsl = "var A = board.create('polygon', [0, 0, 5, 0, 2.5, 4], {borders:{evil:1}});"
        assert _is_allowed_graph_dsl(dsl) is False


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

    def test_returns_two_argument_bounding_box_dsl_unchanged(self) -> None:
        statements = ["board.setBoundingBox([-1, 7, 11, -1], true)"]
        statements += [f"var P{i} = board.create('point', [{i}, {i + 1}])" for i in range(8)]
        statements += [f"board.create('segment', [P{i}, P{(i + 1) % 8}])" for i in range(8)]
        assert len(statements) == 17
        dsl = ";".join(statements)
        assert sanitize_whiteboard_dsl(dsl) == dsl

    def test_strips_initboard_without_semicolon(self) -> None:
        raw = "var board = JXG.JSXGraph.initBoard('box', {boundingbox: [-5, 5, 5, -5]}) var p = board.create('point', [0, 0]);"
        assert sanitize_whiteboard_dsl(raw) == "var p = board.create('point', [0, 0]);"

    def test_returns_none_after_initboard_strips_to_empty(self) -> None:
        raw = "var board = JXG.JSXGraph.initBoard('box', {boundingbox: [-5, 5, 5, -5]});"
        assert sanitize_whiteboard_dsl(raw) is None

    def test_strips_initboard_with_extra_whitespace(self) -> None:
        raw = "var  board  =  JXG.JSXGraph.initBoard(  'box'  )  ;  var p = board.create('point', [0, 0]);"
        assert sanitize_whiteboard_dsl(raw) == "var p = board.create('point', [0, 0]);"

    def test_stores_borders_dsl_unchanged(self) -> None:
        dsl = (
            "var A = board.create('polygon', [0, 0, 5, 0, 2.5, 4], "
            "{fillColor:'#a8d4f050', borders:{strokeColor:'#000000', strokeWidth:1}});"
        )
        assert sanitize_whiteboard_dsl(dsl) == dsl

    def test_strips_line_comment_statement(self) -> None:
        raw = "var p = board.create('point', [0, 0]); // draw a new triangle"
        assert sanitize_whiteboard_dsl(raw) == "var p = board.create('point', [0, 0]);"

    def test_strips_block_comment_statement(self) -> None:
        raw = "/* draw a new triangle */ var p = board.create('point', [0, 0]);"
        assert sanitize_whiteboard_dsl(raw) == "var p = board.create('point', [0, 0]);"

    def test_strips_orphaned_comment_left_by_initboard_removal(self) -> None:
        raw = (
            "var board = JXG.JSXGraph.initBoard('box', {boundingbox: [-5, 5, 5, -5]});\n"
            "// draw a new triangle\n"
            "var p = board.create('point', [0, 0]);"
        )
        assert sanitize_whiteboard_dsl(raw) == "var p = board.create('point', [0, 0]);"

    def test_preserves_double_slash_inside_string_literal(self) -> None:
        dsl = "board.create('text', [0, 0, 'https://example.com/a//b']);"
        assert sanitize_whiteboard_dsl(dsl) == dsl

    def test_block_comment_preserves_token_separation(self) -> None:
        raw = "var/**/p = board.create('point', [0, 0]);"
        assert sanitize_whiteboard_dsl(raw) == "var p = board.create('point', [0, 0]);"

    def test_rejects_numbers_joined_across_block_comment(self) -> None:
        raw = "board.create('point', [1/**/2, 0]);"
        assert sanitize_whiteboard_dsl(raw) is None

    @pytest.mark.parametrize("terminator", ["\r", "\n", "\r\n", "\u2028", "\u2029"])
    def test_strips_line_comment_at_every_js_terminator(self, terminator: str) -> None:
        raw = f"// draw a new triangle{terminator}var p = board.create('point', [0, 0]);"
        assert sanitize_whiteboard_dsl(raw) == "var p = board.create('point', [0, 0]);"

    def test_rejects_unterminated_block_comment(self) -> None:
        raw = "var p = board.create('point', [0, 0]); /* unterminated"
        assert sanitize_whiteboard_dsl(raw) is None

    def test_sanitize_is_idempotent_with_comments_and_borders(self) -> None:
        raw = (
            "var board = JXG.JSXGraph.initBoard('box', {boundingbox: [-5, 5, 5, -5]});\n"
            "// draw a new triangle\n"
            "var A = board.create('polygon', [0, 0, 5, 0, 2.5, 4], "
            "{fillColor:'#a8d4f050', borders:{strokeColor:'#000000', strokeWidth:1}});"
        )
        once = sanitize_whiteboard_dsl(raw)
        assert once is not None
        assert sanitize_whiteboard_dsl(once) == once


BORDERS_DSL = (
    "var A = board.create('polygon', [0, 0, 5, 0, 2.5, 4], "
    "{fillColor:'#a8d4f050', borders:{strokeColor:'#000000', strokeWidth:1}});"
)


class TestExplainGraphDsl:
    """explain_graph_dsl mirrors the sanitizer verdict with named reasons."""

    def test_compliant_dsl_has_no_reasons(self) -> None:
        assert explain_graph_dsl(BORDERS_DSL) == []

    def test_unknown_option_key_named(self) -> None:
        reasons = explain_graph_dsl("board.create('polygon', [0,0,1,0], {borders:{evil:1}});")
        assert reasons == [
            "rejected statement 'board.create('polygon', [0,0,1,0], {borders:{evil:1}})': "
            "unknown option key 'evil'"
        ]

    def test_disallowed_element_type_named(self) -> None:
        reasons = explain_graph_dsl("board.create('rectangle', [0, 0, 1, 1]);")
        assert reasons == [
            "rejected statement 'board.create('rectangle', [0, 0, 1, 1])': "
            "disallowed element type 'rectangle'"
        ]

    def test_blocked_token_named(self) -> None:
        reasons = explain_graph_dsl("fetch('http://evil');")
        assert "disallowed token 'fetch'" in reasons

    def test_mixed_blocked_tokens_reasons_are_deterministically_ordered(self) -> None:
        """Issue #692: _BLOCKED_DSL_TOKENS is a set — the returned order must
        not depend on the process hash seed (single-token membership cannot
        catch this). Exact order is sorted(_BLOCKED_DSL_TOKENS); statement
        diagnostics stay in source order."""
        reasons = explain_graph_dsl("eval(1); fetch(2); window; document;")
        assert [r for r in reasons if r.startswith("disallowed token")] == [
            "disallowed token 'document'",
            "disallowed token 'eval'",
            "disallowed token 'fetch'",
            "disallowed token 'window'",
        ]
        assert [r for r in reasons if r.startswith("rejected statement")] == [
            "rejected statement 'eval(1)': statement is not an allowed declaration or board.create call",
            "rejected statement 'fetch(2)': statement is not an allowed declaration or board.create call",
            "rejected statement 'window': statement is not an allowed declaration or board.create call",
            "rejected statement 'document': statement is not an allowed declaration or board.create call",
        ]

    def test_undeclared_name_named(self) -> None:
        reasons = explain_graph_dsl("board.create('segment', [A, B]);")
        assert reasons == [
            "rejected statement 'board.create('segment', [A, B])': undeclared name 'A'"
        ]

    def test_unrecognized_statement_named_with_statement_text(self) -> None:
        reasons = explain_graph_dsl("board.create('point', [0, 0]); garbage statement")
        assert reasons == [
            "rejected statement 'garbage statement': "
            "statement is not an allowed declaration or board.create call"
        ]

    def test_unbalanced_input_rejected_without_statement_split(self) -> None:
        reasons = explain_graph_dsl("var p = board.create('point', [0, 0]")
        assert reasons == ["unbalanced brackets or an unterminated string literal"]

    def test_missing_and_empty_input(self) -> None:
        assert explain_graph_dsl(None) == ["graphDsl is missing"]
        assert explain_graph_dsl("   ") == ["graphDsl is empty"]

    def test_comment_only_input_has_no_statements_left(self) -> None:
        assert explain_graph_dsl("// draw a new triangle") == [
            "no DSL statements remain after comment and fence stripping"
        ]

    @pytest.mark.parametrize(
        "dsl",
        [
            None,
            "",
            "   ",
            "board.create('point', [0, 0]);",
            BORDERS_DSL,
            "board.create('polygon', [0,0,1,0], {borders:{evil:1}});",
            "board.create('rectangle', [0, 0, 1, 1]);",
            "fetch('http://evil');",
            "board.create('segment', [A, B]);",
            "var p = board.create('point', [0, 0]",
            "// only a comment",
            "board.setBoundingBox([-1, 7, 11, -1], true);",
            "board.create('point', [0, 0]);\n// draw a new triangle\nvar p = board.create('point', [1, 1]);",
        ],
    )
    def test_explanation_agrees_with_sanitizer_verdict(self, dsl: str | None) -> None:
        # Drift guard: empty explanation <=> sanitizer accepts.
        assert bool(explain_graph_dsl(dsl)) == (sanitize_whiteboard_dsl(dsl) is None)


class TestSanitizeGraphDslForStorage:
    """Write-path seam (issue #694): canonical-or-null plus reasons."""

    def test_absent_input_is_not_a_failure(self) -> None:
        assert sanitize_graph_dsl_for_storage(None) == (None, [])
        assert sanitize_graph_dsl_for_storage("") == (None, [])
        assert sanitize_graph_dsl_for_storage("   \n ") == (None, [])

    def test_compliant_input_returns_canonical_value(self) -> None:
        dsl = (
            "var b = JXG.JSXGraph.initBoard('box', {boundingbox: [-5, 5, 5, -5]});\n"
            "board.create('point', [0, 0], {name: 'A'}); // origin"
        )
        canonical, reasons = sanitize_graph_dsl_for_storage(dsl)
        assert reasons == []
        assert canonical == sanitize_whiteboard_dsl(dsl)
        assert canonical is not None
        assert "initBoard" not in canonical and "//" not in canonical

    def test_canonical_value_is_idempotent(self) -> None:
        dsl = "board.create('point', [0, 0]); /* note */"
        canonical, _ = sanitize_graph_dsl_for_storage(dsl)
        assert canonical is not None
        assert sanitize_graph_dsl_for_storage(canonical) == (canonical, [])

    def test_non_compliant_input_returns_explain_reasons(self) -> None:
        dsl = "fetch('http://evil'); graph TD; A-->B"
        canonical, reasons = sanitize_graph_dsl_for_storage(dsl)
        assert canonical is None
        assert reasons == explain_graph_dsl(dsl)
        assert reasons

    def test_borders_style_incident_dsl_is_stored_canonically(self) -> None:
        canonical, reasons = sanitize_graph_dsl_for_storage(BORDERS_DSL)
        assert reasons == []
        assert canonical == sanitize_whiteboard_dsl(BORDERS_DSL)
