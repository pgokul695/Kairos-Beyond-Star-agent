"""
UserCredentials — stores hashed password and refresh token metadata.
Separate table from User so the Agent's core profile model stays clean.
One-to-one with users.uid.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class UserCredentials(Base):
    __tablename__ = "user_credentials"

    uid = Column(
        UUID(as_uuid=True),
        ForeignKey("users.uid", ondelete="CASCADE"),
        primary_key=True,
    )
    email = Column(String(320), nullable=False, unique=True, index=True)
    hashed_password = Column(String(255), nullable=False)
    is_verified = Column(Boolean, nullable=False, server_default="false")
    is_active = Column(Boolean, nullable=False, server_default="true")

    # Refresh token — single active token per user, rotated on every refresh
    refresh_token_hash = Column(String(255), nullable=True)
    refresh_token_expiry = Column(DateTime(timezone=True), nullable=True)

    # Password reset OTP — 6-digit, short-lived (15 min)
    reset_otp = Column(String(6), nullable=True)
    reset_otp_expiry = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
    )
