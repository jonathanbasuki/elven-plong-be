from datetime import datetime
from enum import IntEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from plong_elven.db.base import Base

# Lookup-table ids. Seeded by migration 5c8d2e1f9a63 and fixed: the code and the
# partial index `one_open_task_per_zone` compare against these values. The API
# exposes the lowercase member name ("open", "ongoing", "idle", ...).


class TaskStatusId(IntEnum):
    OPEN = 1
    CLOSED = 2
    FINISHED = 3
    COMPLETE = 4
    ONGOING = 5


class TaskRespondentStatusId(IntEnum):
    ONGOING = 1
    COMPLETED = 2


class UserStatusId(IntEnum):
    IDLE = 1
    DELEGATING = 2


class Role(Base):
    __tablename__ = "roles"

    role_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    users: Mapped[list["User"]] = relationship(back_populates="role")


class UserStatus(Base):
    __tablename__ = "user_status"

    user_status_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TaskStatus(Base):
    __tablename__ = "task_status"

    task_status_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TaskRespondentStatus(Base):
    __tablename__ = "task_respondent_status"

    task_respondent_status_id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True
    )
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
        Index("ix_users_role_id", "role_id"),
    )

    user_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    role_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("roles.role_id", ondelete="RESTRICT"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Dispatch fields (a floor_staff user is also "staff" for tasks).
    user_status_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("user_status.user_status_id"),
        nullable=False,
        default=UserStatusId.IDLE,
        server_default="1",
    )
    push_token: Mapped[str | None] = mapped_column(Text)
    # Home zone: staff are delegated from their own zone to a crowded one, so a
    # task never notifies (or accepts) staff whose home zone is the task's zone.
    zone_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("zone.zone_id", ondelete="SET NULL")
    )
    # Last zone a beacon saw this user in. Not wired yet: always null for now.
    last_location: Mapped[str | None] = mapped_column(
        Text, ForeignKey("zone.zone_id", ondelete="SET NULL")
    )

    role: Mapped[Role] = relationship(back_populates="users")
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    task_responses: Mapped[list["TaskRespondent"]] = relationship(
        back_populates="staff"
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_user_id", "user_id"),
        Index(
            "ix_refresh_tokens_active",
            "user_id",
            "expires_at",
            postgresql_where="revoked_at IS NULL",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(CHAR(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String)
    ip_address: Mapped[str | None] = mapped_column(INET)

    user: Mapped[User] = relationship(back_populates="refresh_tokens")


class Zone(Base):
    __tablename__ = "zone"
    __table_args__ = (CheckConstraint("threshold > 0", name="zone_threshold_check"),)

    zone_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # Normalized 0..1 [x, y] points in the CAMERA frame; the camera process reads
    # these to assign people to zones. The frontend draws its own floorplan shape.
    polygon: Mapped[list[list[float]]] = mapped_column(JSONB, nullable=False)
    # count < threshold -> "safe", otherwise "crowded".
    threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    # True from a broadcast until that task is finished (or it closes with no
    # respondents). While true, the zone cannot be broadcast again.
    is_active_task: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    tasks: Mapped[list["Task"]] = relationship(back_populates="zone")


class ZoneLog(Base):
    """One row per zone per 5-minute window: avg/peak of the detected-person counts
    the camera posted during that window. Written by CrowdService as windows roll
    over, not by any endpoint directly."""

    __tablename__ = "zone_logs"
    __table_args__ = (
        UniqueConstraint("zone_id", "bucket_start", name="uq_zone_logs_zone_bucket"),
    )

    zone_log_id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    zone_id: Mapped[str] = mapped_column(
        String, ForeignKey("zone.zone_id", ondelete="CASCADE"), nullable=False
    )
    bucket_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    avg_count: Mapped[float] = mapped_column(Float, nullable=False)
    peak_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Task(Base):
    __tablename__ = "task"
    __table_args__ = (
        CheckConstraint("total_staff > 0", name="task_total_staff_check"),
        Index(
            "one_open_task_per_zone",
            "zone_id",
            unique=True,
            postgresql_where=text("task_status_id = 1"),
        ),
    )

    task_id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    zone_id: Mapped[str] = mapped_column(
        Text, ForeignKey("zone.zone_id"), nullable=False
    )
    total_staff: Mapped[int] = mapped_column(Integer, nullable=False)
    instruction: Mapped[str] = mapped_column(String(50), nullable=False)
    task_status_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("task_status.task_status_id"),
        nullable=False,
        default=TaskStatusId.OPEN,
        server_default="1",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    zone: Mapped[Zone] = relationship(back_populates="tasks")
    respondents: Mapped[list["TaskRespondent"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class TaskRespondent(Base):
    __tablename__ = "task_respondents"
    __table_args__ = (
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= responded_at",
            name="task_respondents_completed_check",
        ),
        Index("idx_task_respondents_task", "task_id"),
    )

    task_id: Mapped[UUID] = mapped_column(ForeignKey("task.task_id"), primary_key=True)
    staff_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.user_id"), primary_key=True
    )
    task_respondent_status_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("task_respondent_status.task_respondent_status_id"),
        nullable=False,
        default=TaskRespondentStatusId.ONGOING,
        server_default="1",
    )
    responded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    task: Mapped[Task] = relationship(back_populates="respondents")
    staff: Mapped[User] = relationship(back_populates="task_responses")


class TaskFootage(Base):
    __tablename__ = "task_footage"

    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("task.task_id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="processing"
    )
    object_path: Mapped[str | None] = mapped_column(Text)
    public_url: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
