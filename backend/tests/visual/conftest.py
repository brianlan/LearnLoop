"""Shared fixtures for visual validation tests."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest


@dataclass(frozen=True)
class GraphTestCase:
    """A test case for graph rendering validation.
    
    Attributes:
        name: Unique identifier for the test case
        dsl: The JSXGraph DSL code to render
        description: What should be visually present in the rendered graph
        expect_error: Whether this test case should result in an error state
    """
    name: str
    dsl: str
    description: str
    expect_error: bool = False


# Visual test corpus of representative JSXGraph DSL snippets
GRAPH_TEST_CORPUS: list[GraphTestCase] = [
    GraphTestCase(
        name="line",
        dsl="var b = JXG.JSXGraph.initBoard('jxgbox', {boundingbox: [-5,5,5,-5], axis: true, grid: true, showCopyright: false}); b.create('line', [[-3,1], [3,2]]);",
        description="A straight line segment passing through the coordinate plane, going from lower left to upper right",
        expect_error=False,
    ),
    GraphTestCase(
        name="circle",
        dsl="var b = JXG.JSXGraph.initBoard('jxgbox', {boundingbox: [-5,5,5,-5], axis: true, grid: true, showCopyright: false}); b.create('circle', [[0,0], 3]);",
        description="A circle centered at the origin with radius 3, displayed on a coordinate grid",
        expect_error=False,
    ),
    GraphTestCase(
        name="triangle",
        dsl="var b = JXG.JSXGraph.initBoard('jxgbox', {boundingbox: [-5,5,5,-5], axis: true, grid: true, showCopyright: false}); b.create('polygon', [[-2,-2], [2,-2], [0,2]]);",
        description="A triangle with vertices at (-2,-2), (2,-2), and (0,2), forming a polygon shape on the coordinate plane",
        expect_error=False,
    ),
    GraphTestCase(
        name="parabola",
        dsl="var b = JXG.JSXGraph.initBoard('jxgbox', {boundingbox: [-5,5,5,-5], axis: true, grid: true, showCopyright: false}); b.create('functiongraph', [function(x){return x*x-2;}]);",
        description="A parabola curve opening upward, representing the function y = x² - 2, with its vertex at (0, -2)",
        expect_error=False,
    ),
    GraphTestCase(
        name="invalid_throw_error",
        dsl="throw new Error('broken');",
        description="An error message indicating the graph failed to render",
        expect_error=True,
    ),
    GraphTestCase(
        name="invalid_empty",
        dsl="",
        description="An error state or empty rendering area showing no valid graph was produced",
        expect_error=True,
    ),
]


@pytest.fixture(scope="session")
def visual_evidence_dir() -> Path:
    """Returns the directory for storing visual test evidence (screenshots)."""
    evidence_dir = Path(__file__).parent.parent.parent.parent / ".sisyphus" / "evidence" / "task-15-visual"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    return evidence_dir


@pytest.fixture(scope="session")
def harness_html_path() -> Path:
    """Returns the path to the visual test harness HTML file."""
    return Path(__file__).parent.parent.parent.parent / "frontend" / "src" / "visual-test-harness.html"


@pytest.fixture
def get_graph_test_cases() -> list[GraphTestCase]:
    """Returns the full test corpus of graph DSL cases."""
    return GRAPH_TEST_CORPUS


@pytest.fixture
def encode_dsl_for_url() -> callable:
    """Returns a function to encode DSL for URL hash parameter."""
    def _encode(dsl: str) -> str:
        # Encode DSL to base64 for safe URL transport
        json_payload = json.dumps({"dsl": dsl})
        return base64.urlsafe_b64encode(json_payload.encode()).decode().rstrip("=")
    return _encode


@pytest.fixture(scope="session")
def browser_viewport_size() -> dict[str, int]:
    """Returns the fixed viewport size for deterministic screenshots."""
    return {"width": 400, "height": 400}
