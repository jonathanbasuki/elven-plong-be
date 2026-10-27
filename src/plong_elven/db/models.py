from datetime import datetime
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
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from plong_elven.db.base import Base


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    users: Mapped[list["User"]] = relationship(back_populates="role")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
        Index("ix_users_role_id", "role_id"),
        CheckConstraint("status IN ('idle', 'delegating')", name="users_status_check"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    role_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Dispatch fields (this is also "staff" for the delegation workflow): a
    # floor_staff user's own id is what POST /broadcasts/{id}/respond etc. use.
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="idle", server_default="idle"
    )
    push_token: Mapped[str | None] = mapped_column(String)

    role: Mapped[Role] = relationship(back_populates="users")
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    broadcast_responses: Mapped[list["BroadcastResponse"]] = relationship(
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
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
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
    __tablename__ = "zones"
    __table_args__ = (
        CheckConstraint("warning_threshold > 0", name="zones_warning_threshold_check"),
        CheckConstraint(
            "crowded_threshold > warning_threshold",
            name="zones_crowded_threshold_check",
        ),
    )

    zone_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    map_image_url: Mapped[str] = mapped_column(String, nullable=False)
    polygon: Mapped[list[list[float]]] = mapped_column(JSONB, nullable=False)
    warning_threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    crowded_threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    broadcasts: Mapped[list["Broadcast"]] = relationship(back_populates="zone")


class ZoneCountHistory(Base):
    """One row per zone per 5-minute window: avg/peak of the detected-person counts
    the cctv worker posted during that window. Written by detections.py as windows
    roll over, not by any endpoint directly."""

    __tablename__ = "zone_count_history"
    __table_args__ = (
        UniqueConstraint(
            "zone_id", "bucket_start", name="uq_zone_count_history_zone_bucket"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    zone_id: Mapped[str] = mapped_column(
        String, ForeignKey("zones.zone_id"), nullable=False
    )
    bucket_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    avg_count: Mapped[float] = mapped_column(Float, nullable=False)
    peak_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Broadcast(Base):
    __tablename__ = "broadcasts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'closed', 'finished')", name="broadcast_status_check"
        ),
        CheckConstraint("staff_needed > 0", name="broadcast_staff_needed_check"),
        Index(
            "one_open_broadcast_per_zone",
            "zone_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )

    broadcast_id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    zone_id: Mapped[str] = mapped_column(
        String, ForeignKey("zones.zone_id"), nullable=False
    )
    staff_needed: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="open", server_default="open"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    zone: Mapped[Zone] = relationship(back_populates="broadcasts")
    responses: Mapped[list["BroadcastResponse"]] = relationship(
        back_populates="broadcast", cascade="all, delete-orphan"
    )


class BroadcastResponse(Base):
    __tablename__ = "broadcast_responses"
    __table_args__ = (
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= responded_at",
            name="broadcast_response_completed_check",
        ),
        Index("idx_broadcast_responses_broadcast", "broadcast_id"),
    )

    broadcast_id: Mapped[UUID] = mapped_column(
        ForeignKey("broadcasts.broadcast_id"), primary_key=True
    )
    staff_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    responded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    broadcast: Mapped[Broadcast] = relationship(back_populates="responses")
    staff: Mapped[User] = relationship(back_populates="broadcast_responses")
