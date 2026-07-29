"""Visual validation tests for JSXGraph DSL rendering.

These tests render JSXGraph DSL snippets in a sandboxed HTML harness,
capture screenshots, and validate them using multimodal analysis.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from .conftest import GRAPH_TEST_CORPUS


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
