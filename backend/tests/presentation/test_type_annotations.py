from typing import get_type_hints, get_origin

from app.domain.selection import ProblemSelectionConfig
from pymongo.asynchronous.database import AsyncDatabase

from app.presentation.home import _build_score_distribution
from app.presentation.ingestion import _get_owned_preview


def test_build_score_distribution_annotations_resolve() -> None:
    hints = get_type_hints(_build_score_distribution)
    assert hints["config"] is ProblemSelectionConfig


def test_get_owned_preview_annotations_resolve() -> None:
    hints = get_type_hints(_get_owned_preview)
    assert get_origin(hints["database"]) is AsyncDatabase