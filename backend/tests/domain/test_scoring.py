from datetime import datetime
from app.domain import (
    ExamItem,
    ExamItemAnswer,
    ExamItemGrading,
    GradingStatus,
    ProblemSnapshot,
    CorrectAnswer,
    ProblemType,
    compute_summary,
)


def create_test_item(
    item_id: str,
    has_answer: bool = False,
    grading_status: GradingStatus = GradingStatus.UNGRADED,
) -> ExamItem:
    return ExamItem(
        itemId=item_id,
        order=0,
        problemId="p1",
        problemSnapshot=ProblemSnapshot(
            text="test",
            problemType=ProblemType.SINGLE_CHOICE,
            correctAnswer=CorrectAnswer(display="a", normalizedText="a", normalizedSet=[], format="single")
        ),
        answer=ExamItemAnswer(raw="a" if has_answer else None, savedAt=datetime.utcnow() if has_answer else None),
        grading=ExamItemGrading(status=grading_status)
    )


def test_score_null_when_all_pending():
    items = [
        create_test_item("1", has_answer=True, grading_status=GradingStatus.PENDING_REVIEW),
        create_test_item("2", has_answer=True, grading_status=GradingStatus.PENDING_REVIEW),
    ]
    summary = compute_summary(items)
    assert summary.totalProblems == 2
    assert summary.gradedProblems == 0
    assert summary.score is None


def test_score_calculation():
    items = [
        create_test_item("1", has_answer=True, grading_status=GradingStatus.CORRECT),
        create_test_item("2", has_answer=True, grading_status=GradingStatus.CORRECT),
        create_test_item("3", has_answer=True, grading_status=GradingStatus.INCORRECT),
        create_test_item("4", has_answer=True, grading_status=GradingStatus.PENDING_REVIEW),
    ]
    summary = compute_summary(items)
    assert summary.totalProblems == 4
    assert summary.gradedProblems == 3
    assert summary.correctProblems == 2
    assert summary.failedProblems == 1
    assert summary.pendingProblems == 1
    assert summary.score == 2 / 3
