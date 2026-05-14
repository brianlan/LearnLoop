from typing import List
from .models import ExamItem, ExamSummary, GradingStatus


def compute_summary(items: List[ExamItem]) -> ExamSummary:
    total = len(items)
    answered = sum(1 for item in items if item.answer.raw is not None)
    pending = sum(1 for item in items if item.grading.status == GradingStatus.PENDING_REVIEW)
    correct = sum(1 for item in items if item.grading.status == GradingStatus.CORRECT)
    failed = sum(1 for item in items if item.grading.status == GradingStatus.INCORRECT)
    graded = correct + failed

    score = None
    if graded > 0:
        score = correct / graded

    return ExamSummary(
        totalProblems=total,
        answeredProblems=answered,
        gradedProblems=graded,
        pendingProblems=pending,
        correctProblems=correct,
        failedProblems=failed,
        score=score
    )
