import logging
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId

from app.domain.models import CoachingConversation, CoachingMessage, CoachingRole, ExamState
from app.infrastructure.config.settings import Settings
from app.infrastructure.vlm.solution_coaching_client import (
    CoachingMessage as VLMCoachingMessage,
    CoachingVLMClient,
    CoachingVLMRequest,
    SolutionCoachingVLMError,
)
from app.infrastructure.storage.mongo import (
    CANONICAL_SOLUTIONS_COLLECTION,
    COACHING_CONVERSATIONS_COLLECTION,
)

logger = logging.getLogger(__name__)

class CoachingError(Exception):
    def __init__(self, message: str, code: str = "COACHING_ERROR", status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class CoachingService:
    def __init__(self, database: Any, settings: Settings | None = None, vlm_client: CoachingVLMClient | None = None):
        self.db = database
        self._settings = settings
        self._vlm_client = vlm_client

    async def get_conversation(self, problem_id: str, user_id: str) -> CoachingConversation | None:
        doc = await self.db[COACHING_CONVERSATIONS_COLLECTION].find_one({
            "problem_id": problem_id,
            "user_id": user_id
        })
        if not doc:
            return None
        doc["id"] = str(doc["_id"])
        return CoachingConversation.model_validate(doc)

    async def clear_conversation(self, problem_id: str, user_id: str) -> None:
        await self.db[COACHING_CONVERSATIONS_COLLECTION].delete_one({
            "problem_id": problem_id,
            "user_id": user_id
        })

    async def send_message(self, problem_id: str, user_id: str, message: str) -> CoachingConversation:
        start_time = datetime.now(UTC)
        # 1. Enforce exam safety
        active_exams = await self.db["exams"].count_documents({
            "userId": ObjectId(user_id) if isinstance(user_id, str) and len(user_id) == 24 else user_id,
            "state": ExamState.IN_PROGRESS.value,
            "items.problemId": ObjectId(problem_id)
        })
        if active_exams > 0:
            raise CoachingError(
                "Cannot access coaching for a problem in an active exam.",
                code="ACTIVE_EXAM_RESTRICTION",
                status_code=403
            )

        # 2. Fetch Problem
        problem = await self.db["problems"].find_one({
            "_id": ObjectId(problem_id),
            "userId": ObjectId(user_id) if isinstance(user_id, str) and len(user_id) == 24 else user_id,
            "isDeleted": False
        })
        if not problem:
            raise CoachingError("Problem not found", code="NOT_FOUND", status_code=404)

        # 3. Fetch canonical solution
        solution = await self.db[CANONICAL_SOLUTIONS_COLLECTION].find_one({
            "problem_id": problem_id
        })
        if not solution:
            steps_markdown = "No canonical steps available."
            canonical_final_answer = problem.get("correctAnswer", {}).get("display", "Unknown")
            level_classification = "unknown"
        else:
            steps_markdown = solution.get("steps_markdown", "")
            canonical_final_answer = solution.get("final_answer", "")
            level_classification = solution.get("level_classification") or solution.get("math_level_classification", "unknown")

        # 4. Fetch or create Conversation
        conversation = await self.get_conversation(problem_id, user_id)
        if not conversation:
            conversation = CoachingConversation(
                problem_id=problem_id,
                user_id=user_id
            )

        # Enforce max 20 messages
        if len(conversation.messages) >= 20:
            raise CoachingError(
                "Conversation has reached the maximum limit of 20 messages.",
                code="MESSAGE_CAP_EXCEEDED",
                status_code=400
            )

        # 5. Call VLM
        history = [
            VLMCoachingMessage(role=msg.role.value, text=msg.content)
            for msg in conversation.messages
        ]

        request = CoachingVLMRequest(
            problem_text=problem.get("text", ""),
            correct_answer=problem.get("correctAnswer", {}).get("display", ""),
            canonical_steps_markdown=steps_markdown,
            canonical_final_answer=canonical_final_answer,
            level_classification=level_classification,
            conversation_history=history,
            new_message=message
        )

        vlm_client = self._vlm_client
        try:
            if vlm_client is not None:
                vlm_result = await vlm_client.send_message(request)
            else:
                subject = problem.get("subject", "math")
                vlm_client = CoachingVLMClient(settings=self._settings, subject=subject)
                vlm_result = await vlm_client.send_message(request)
        except SolutionCoachingVLMError as exc:
            logger.error(f"Coaching VLM error: {exc}")
            raise CoachingError(
                "Coaching service is currently unavailable. Please try again later.",
                code="VLM_FAILURE",
                status_code=503
            )
        finally:
            if self._vlm_client is None and vlm_client is not None:
                await vlm_client.aclose()

        # 6. Add messages
        try:
            conversation.add_message(CoachingMessage(role=CoachingRole.STUDENT, content=message))
            conversation.add_message(CoachingMessage(
                role=CoachingRole.COACH,
                content=vlm_result.text,
                whiteboard_dsl=vlm_result.whiteboard_dsl,
                reasoning_content=vlm_result.reasoning_content
            ))
        except ValueError as exc:
            raise CoachingError(str(exc), code="MESSAGE_CAP_EXCEEDED", status_code=400)

        # 7. Save
        now = datetime.now(UTC)
        conversation.updated_at = now

        doc = conversation.model_dump(exclude={"id"})
        await self.db[COACHING_CONVERSATIONS_COLLECTION].update_one(
            {"problem_id": problem_id, "user_id": user_id},
            {"$set": doc},
            upsert=True
        )

        result = await self.get_conversation(problem_id, user_id)
        if result is None:
            raise CoachingError("Failed to save conversation.", code="INTERNAL_ERROR", status_code=500)
            
        end_time = datetime.now(UTC)
        response_time_ms = (end_time - start_time).total_seconds() * 1000
        
        from app.observability import log_coaching_event
        log_coaching_event(
            event="request",
            conversation_id=result.id,
            message_count=len(result.messages),
            response_time_ms=response_time_ms
        )
        
        return result
