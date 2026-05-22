from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from app.infrastructure.auth.password import hash_password, verify_password
from app.infrastructure.config.settings import Settings
from app.observability import log_teacher_password_event
from app.presentation.deps import get_app_settings, get_current_user, get_database
from app.presentation.errors import ApiError

router = APIRouter(prefix="/teacher-password", tags=["teacher-password"])


class VerifyTeacherPasswordRequest(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class VerifyTeacherPasswordResponse(BaseModel):
    ok: bool


class ChangeTeacherPasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)
    confirm_password: str = Field(min_length=1, max_length=1024)

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, confirm_password: str, info) -> str:
        if "new_password" in info.data and confirm_password != info.data["new_password"]:
            raise ValueError("Passwords do not match")
        return confirm_password


class ChangeTeacherPasswordResponse(BaseModel):
    ok: bool


@router.post("/verify", response_model=VerifyTeacherPasswordResponse)
async def verify_teacher_password(
    payload: VerifyTeacherPasswordRequest,
    user=Depends(get_current_user),
    database=Depends(get_database),
    settings: Settings = Depends(get_app_settings),
) -> VerifyTeacherPasswordResponse:
    # Get or create teacherPasswordHash for user (handle existing users without it)
    teacher_password_hash = user.get("teacherPasswordHash")
    if teacher_password_hash is None:
        # Auto-migrate existing user: set to default password hash
        teacher_password_hash = hash_password(settings.teacher_password_default)
        now = datetime.now(UTC)
        await database["users"].update_one(
            {"_id": user["_id"]},
            {"$set": {"teacherPasswordHash": teacher_password_hash, "updatedAt": now}},
        )
        log_teacher_password_event(
            "auto_migrate_existing_user",
            user_id=str(user["_id"]),
            username=user["username"],
        )

    ok = verify_password(payload.password, teacher_password_hash)
    log_teacher_password_event(
        "verify_attempt",
        user_id=str(user["_id"]),
        username=user["username"],
        success=ok,
    )
    if not ok:
        raise ApiError(401, "UNAUTHENTICATED", "Incorrect teacher password")
    return VerifyTeacherPasswordResponse(ok=True)


@router.post("/change", response_model=ChangeTeacherPasswordResponse)
async def change_teacher_password(
    payload: ChangeTeacherPasswordRequest,
    user=Depends(get_current_user),
    database=Depends(get_database),
    settings: Settings = Depends(get_app_settings),
) -> ChangeTeacherPasswordResponse:
    # Get or create teacherPasswordHash for user (handle existing users without it)
    teacher_password_hash = user.get("teacherPasswordHash")
    if teacher_password_hash is None:
        # Auto-migrate existing user: set to default password hash
        teacher_password_hash = hash_password(settings.teacher_password_default)
        now = datetime.now(UTC)
        await database["users"].update_one(
            {"_id": user["_id"]},
            {"$set": {"teacherPasswordHash": teacher_password_hash, "updatedAt": now}},
        )
        log_teacher_password_event(
            "auto_migrate_existing_user",
            user_id=str(user["_id"]),
            username=user["username"],
        )

    # Verify current password
    current_password_ok = verify_password(payload.current_password, teacher_password_hash)
    log_teacher_password_event(
        "change_verify_attempt",
        user_id=str(user["_id"]),
        username=user["username"],
        success=current_password_ok,
    )
    if not current_password_ok:
        raise ApiError(401, "UNAUTHENTICATED", "Incorrect teacher password")

    # Hash and update new password
    new_teacher_password_hash = hash_password(payload.new_password)
    now = datetime.now(UTC)
    await database["users"].update_one(
        {"_id": user["_id"]},
        {"$set": {"teacherPasswordHash": new_teacher_password_hash, "updatedAt": now}},
    )
    log_teacher_password_event(
        "change_success",
        user_id=str(user["_id"]),
        username=user["username"],
    )

    return ChangeTeacherPasswordResponse(ok=True)
