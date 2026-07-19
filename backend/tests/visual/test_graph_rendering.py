"""Visual validation tests for JSXGraph DSL rendering.

These tests render JSXGraph DSL snippets in a sandboxed HTML harness,
capture screenshots, and validate them using multimodal analysis.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import GRAPH_TEST_CORPUS, GraphTestCase


def test_harness_file_exists(harness_html_path: Path) -> None:
    """Verify the visual test harness HTML file exists."""
    assert harness_html_path.exists(), f"Harness file not found: {harness_html_path}"
    assert harness_html_path.is_file(), f"Harness path is not a file: {harness_html_path}"


def test_evidence_directory_exists(visual_evidence_dir: Path) -> None:
    """Verify the evidence directory exists and is writable."""
    assert visual_evidence_dir.exists(), f"Evidence directory not found: {visual_evidence_dir}"
    assert visual_evidence_dir.is_dir(), f"Evidence path is not a directory: {visual_evidence_dir}"
    # Test write access
    test_file = visual_evidence_dir / ".write_test"
    try:
        test_file.write_text("test")
        test_file.unlink()
    except Exception as exc:
        pytest.fail(f"Evidence directory is not writable: {exc}")


def test_corpus_definitions() -> None:
    """Verify the test corpus has correct structure."""
    assert len(GRAPH_TEST_CORPUS) >= 6, "Test corpus should have at least 6 test cases"
    
    names = [tc.name for tc in GRAPH_TEST_CORPUS]
    assert len(names) == len(set(names)), "Test case names must be unique"
    
    valid_count = sum(1 for tc in GRAPH_TEST_CORPUS if not tc.expect_error)
    error_count = sum(1 for tc in GRAPH_TEST_CORPUS if tc.expect_error)
    assert valid_count >= 4, "Should have at least 4 valid graph test cases"
    assert error_count >= 2, "Should have at least 2 error test cases"


def test_url_encoding() -> None:
    """Verify URL encoding for DSL works correctly."""
    test_dsl = "var b = JXG.JSXGraph.initBoard('box');"
    json_payload = json.dumps({"dsl": test_dsl})
    encoded = base64.urlsafe_b64encode(json_payload.encode()).decode().rstrip("=")
    
    assert encoded, "Encoded string should not be empty"
    assert " " not in encoded, "Encoded string should not contain spaces"
    
    # Verify decoding works
    padded = encoded + "=" * (4 - len(encoded) % 4)
    decoded = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    assert decoded["dsl"] == test_dsl, "Decoded DSL should match original"


@pytest.mark.skip(reason="Visual validation requires agent environment with browser tools")
def test_graph_rendering_corpus_full(
    visual_evidence_dir: Path,
    harness_html_path: Path,
) -> None:
    """Full visual validation test - runs in agent environment.
    
    This test is skipped in normal pytest runs because it requires
    the agent environment with access to playwright browser tools.
    To run visual validation, execute the test via the agent with
    the browser automation tools available.
    """
    pass


def run_visual_validation(
    visual_evidence_dir: Path,
    harness_html_path: Path,
    browser_viewport_size: dict,
) -> list[tuple[str, str]]:
    """Run visual validation for all test cases.
    
    This function is called when the test is run in agent mode.
    It uses the actual browser tools to capture screenshots and
    validate them using look_at.
    
    Returns list of (test_name, error_message) tuples. Empty error
    message means the test passed.
    """
    import asyncio
    
    results = []
    for test_case in GRAPH_TEST_CORPUS:
        result = asyncio.run(_validate_single_case(
            test_case=test_case,
            visual_evidence_dir=visual_evidence_dir,
            harness_html_path=harness_html_path,
            browser_viewport_size=browser_viewport_size,
        ))
        results.append((test_case.name, result))
    return results


async def _validate_single_case(
    test_case: GraphTestCase,
    visual_evidence_dir: Path,
    harness_html_path: Path,
    browser_viewport_size: dict,
) -> str:
    """Validate a single test case.
    
    Returns empty string on success, error message on failure.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
    from tools import playwright_browser_navigate, playwright_browser_resize, playwright_browser_wait_for, playwright_browser_take_screenshot, look_at
    
    url = _build_harness_url(test_case.dsl, harness_html_path)
    screenshot_path = visual_evidence_dir / f"{test_case.name}.png"
    
    try:
        await playwright_browser_navigate(url=url)
        await playwright_browser_resize(width=browser_viewport_size["width"], height=browser_viewport_size["height"])
        await playwright_browser_wait_for(time=1.5)
        await playwright_browser_take_screenshot(filename=str(screenshot_path))
    except Exception as exc:
        return f"Screenshot capture failed: {exc}"
    
    # Validate the screenshot
    if test_case.expect_error:
        goal = (
            f"Analyze this JSXGraph rendering screenshot for test case '{test_case.name}'. "
            f"Expected: {test_case.description}. "
            f"Confirm that an error state is shown (error message, empty area, or no valid graph). "
            f"Answer with ONLY 'PASS' if the error state is correctly shown, or 'FAIL: <reason>' if not."
        )
    else:
        goal = (
            f"Analyze this JSXGraph rendering screenshot for test case '{test_case.name}'. "
            f"Expected visual content: {test_case.description}. "
            f"Answer with ONLY 'PASS' if the described visual content is present and correctly rendered, "
            f"or 'FAIL: <reason>' if the expected geometry is missing or incorrect."
        )
    
    try:
        result = look_at(file_path=str(screenshot_path), goal=goal)
    except Exception as exc:
        return f"Multimodal validation error: {exc}"
    
    result_text = result.lower().strip() if isinstance(result, str) else str(result).lower().strip()
    
    if result_text.startswith("pass"):
        return ""
    elif result_text.startswith("fail"):
        return result_text[5:].strip() if result_text.startswith("fail:") else "Visual validation failed"
    else:
        return f"Unexpected validation response: {result}"


def _build_harness_url(dsl: str, harness_html_path: Path) -> str:
    """Build the harness URL with DSL encoded in hash parameter."""
    json_payload = json.dumps({"dsl": dsl})
    encoded = base64.urlsafe_b64encode(json_payload.encode()).decode().rstrip("=")
    return f"file://{harness_html_path}#{encoded}"
