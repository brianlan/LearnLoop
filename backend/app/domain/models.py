from datetime import UTC, datetime
from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


# Enumerations
class ProblemType(str, Enum):
    SINGLE_CHOICE = "single-choice"
    MULTI_CHOICE = "multi-choice"
    FILL_IN_THE_BLANK = "fill-in-the-blank"
    SHORT_ANSWER = "short-answer"


class ExamState(str, Enum):
    IN_PROGRESS = "in-progress"
    SUBMITTED = "submitted"
    DISCARDED = "discarded"


class IngestionPreviewStatus(str, Enum):
    UPLOADED = "uploaded"
    EXTRACTING = "extracting"
    READY = "ready"
    VLM_FAILED = "vlm-failed"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"


class GradingStatus(str, Enum):
    UNGRADED = "ungraded"
    CORRECT = "correct"
    INCORRECT = "incorrect"
    PENDING_REVIEW = "pending-review"


class GradingMethod(str, Enum):
    NORMALIZED_MATCH = "normalized-match"
    VLM = "vlm"


class SolutionGenerationStatus(str, Enum):
    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class CoachingRole(str, Enum):
    STUDENT = "student"
    COACH = "coach"


# Nested Models
class CorrectAnswer(BaseModel):
    display: str
    normalizedText: str
    normalizedSet: List[str]
    format: str  # "single" or "set"


class SourceImage(BaseModel):
    bucket: str
    objectKey: str
    contentType: Optional[str] = None
    sizeBytes: Optional[int] = None
    sha256: Optional[str] = None
    uploadedAt: Optional[datetime] = None


class Extraction(BaseModel):
    requestModel: Optional[str] = None
    requestStartedAt: Optional[datetime] = None
    requestFinishedAt: Optional[datetime] = None
    success: Optional[bool] = None
    rawText: Optional[str] = None
    rawProblemType: Optional[ProblemType] = None
    rawGraphDsl: Optional[str] = None
    rawProviderResponse: Optional[Dict[str, Any]] = None
    failureCode: Optional[str] = None
    failureMessage: Optional[str] = None


class EditableDraft(BaseModel):
    text: Optional[str] = None
    problemType: Optional[ProblemType] = None
    graphDsl: Optional[str] = None
    correctAnswer: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


class Origin(BaseModel):
    previewId: Optional[str] = None
    vlmModel: Optional[str] = None
    rawExtractedText: Optional[str] = None
    rawExtractedProblemType: Optional[str] = None
    rawExtractedGraphDsl: Optional[str] = None


class Tracking(BaseModel):
    exposureCount: int = 0
    correctCount: int = 0
    failedCount: int = 0
    lastTestedAt: Optional[datetime] = None
    lastAttemptCorrect: Optional[bool] = None


class ClientMeta(BaseModel):
    ip: Optional[str] = None
    userAgent: Optional[str] = None


class SelectionPolicyConfig(BaseModel):
    recencyWeight: float
    failureWeight: float


class ExamConfigSnapshot(BaseModel):
    maxProblemCount: int
    selectionPolicy: SelectionPolicyConfig
    generatedAt: datetime


class ProblemSnapshot(BaseModel):
    text: str
    problemType: ProblemType
    graphDsl: Optional[str] = None
    correctAnswer: CorrectAnswer
    sourceImage: Optional[SourceImage] = None


class ExamItemAnswer(BaseModel):
    raw: Optional[str] = None
    savedAt: Optional[datetime] = None


class ExamItemGrading(BaseModel):
    status: GradingStatus = GradingStatus.UNGRADED
    method: Optional[str] = None  # "normalized-match", "vlm", "self-report"
    isCorrect: Optional[bool] = None
    score: Optional[float] = None
    feedback: Optional[str] = None
    providerModel: Optional[str] = None
    rawProviderResponse: Optional[Dict[str, Any]] = None
    gradedAt: Optional[datetime] = None
    retryCount: int = 0
    selfReportedCorrect: Optional[bool] = None


class ExamItem(BaseModel):
    itemId: str
    order: int
    problemId: str
    problemSnapshot: ProblemSnapshot
    answer: ExamItemAnswer = Field(default_factory=ExamItemAnswer)
    grading: ExamItemGrading = Field(default_factory=ExamItemGrading)


class ExamSummary(BaseModel):
    totalProblems: int
    answeredProblems: int
    gradedProblems: int
    pendingProblems: int
    correctProblems: int
    failedProblems: int
    score: Optional[float] = None


# Main Domain Models
class User(BaseModel):
    id: Optional[str] = None
    username: str
    passwordHash: str
    teacherPasswordHash: Optional[str] = None
    createdAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updatedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    lastLoginAt: Optional[datetime] = None
    status: str = "active"


class Session(BaseModel):
    id: Optional[str] = None
    userId: str
    createdAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expiresAt: datetime
    lastSeenAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    invalidatedAt: Optional[datetime] = None
    clientMeta: ClientMeta = Field(default_factory=ClientMeta)


class Tag(BaseModel):
    id: Optional[str] = None
    userId: str
    name: str
    createdAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updatedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))


class IngestionPreview(BaseModel):
    id: Optional[str] = None
    userId: str
    status: IngestionPreviewStatus
    sourceImage: SourceImage
    extraction: Extraction = Field(default_factory=Extraction)
    editableDraft: EditableDraft = Field(default_factory=EditableDraft)
    createdAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updatedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expiresAt: datetime


class Problem(BaseModel):
    id: Optional[str] = None
    userId: str
    text: str
    problemType: ProblemType
    graphDsl: Optional[str] = None
    correctAnswer: CorrectAnswer
    tags: List[str] = Field(default_factory=list)
    sourceImage: Optional[SourceImage] = None
    origin: Origin = Field(default_factory=Origin)
    tracking: Tracking = Field(default_factory=Tracking)
    isDeleted: bool = False
    deletedAt: Optional[datetime] = None
    createdAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updatedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Exam(BaseModel):
    id: Optional[str] = None
    userId: str
    state: ExamState
    configSnapshot: ExamConfigSnapshot
    items: List[ExamItem] = Field(default_factory=list)
    summary: ExamSummary
    createdAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    startedAt: Optional[datetime] = None
    submittedAt: Optional[datetime] = None
    discardedAt: Optional[datetime] = None
    updatedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PracticeAttempt(BaseModel):
    id: Optional[str] = None
    userId: str
    problemId: str
    submittedAnswer: Optional[str] = None
    gradingStatus: GradingStatus = GradingStatus.UNGRADED
    gradingMethod: Optional[GradingMethod] = None
    createdAt: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SolutionGenerationTask(BaseModel):
    id: Optional[str] = None
    problem_id: str
    user_id: str
    status: SolutionGenerationStatus
    retry_count: int = 0
    failure_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    process_after: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: Optional[datetime] = None


class CanonicalSolution(BaseModel):
    id: Optional[str] = None
    problem_id: str
    user_id: str
    steps_markdown: str
    final_answer: str
    math_level_classification: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CoachingMessage(BaseModel):
    role: CoachingRole
    content: str
    whiteboard_dsl: Optional[str] = None
    reasoning_content: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CoachingConversation(BaseModel):
    id: Optional[str] = None
    problem_id: str
    user_id: str
    messages: List[CoachingMessage] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def add_message(self, message: CoachingMessage) -> None:
        """Add a message, enforcing the 20-message cap."""
        if len(self.messages) >= 20:
            raise ValueError("Conversation cannot have more than 20 messages")
        self.messages.append(message)
        self.updated_at = datetime.now(UTC)

    def clear_messages(self) -> None:
        """Clear all messages from the conversation."""
        self.messages = []
        self.updated_at = datetime.now(UTC)
