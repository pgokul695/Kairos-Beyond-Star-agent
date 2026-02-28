"""
Auth service — registration, login, token management, and password reset.

Invariants:
  - Passwords are SHA-256 pre-hashed then bcrypt-hashed — never stored plain.
  - Refresh tokens are stored as SHA-256 hashes only — never the raw token.
  - decode_token always validates the "type" claim — access ≠ refresh.
  - ProtoPost delivery errors are swallowed — never propagate to the client.
  - OTP expiry is always validated server-side.
  - Issuing a new refresh token always overwrites the previous one.
  - auth_service never modifies the users table except during register_user.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import httpx
from fastapi import Depends, Header, HTTPException
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.models.user_credentials import UserCredentials
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    ResetPasswordRequest,
    TokenResponse,
)

logger = logging.getLogger(__name__)

# ── Password hashing ──────────────────────────────────────────────────────────
# SHA-256 pre-hash sidesteps bcrypt's hard 72-byte limit while keeping full
# entropy.  digest() → 32 bytes, always well within bcrypt's safe range.


def _pre_hash(plain: str) -> bytes:
    """Return SHA-256 digest of *plain* as bytes (32 bytes)."""
    return hashlib.sha256(plain.encode("utf-8")).digest()


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of *plain* (SHA-256 pre-hashed)."""
    return bcrypt.hashpw(_pre_hash(plain), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches *hashed*."""
    return bcrypt.checkpw(_pre_hash(plain), hashed.encode("utf-8"))


# ── JWT helpers ───────────────────────────────────────────────────────────────


def create_access_token(uid: str) -> tuple[str, int]:
    """
    Create a short-lived access token.
    Returns (encoded_jwt, expires_in_seconds).
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    payload = {"sub": uid, "exp": expire, "type": "access"}
    token = jwt.encode(
        payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )
    return token, settings.access_token_expire_minutes * 60


def create_refresh_token(uid: str) -> tuple[str, datetime]:
    """
    Create a long-lived refresh token.
    Returns (encoded_jwt, expiry_datetime).
    """
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.refresh_token_expire_days
    )
    payload = {"sub": uid, "exp": expire, "type": "refresh"}
    token = jwt.encode(
        payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )
    return token, expire


def decode_token(token: str, expected_type: str) -> str:
    """
    Decode and validate a JWT.
    Returns the uid (str) on success.
    Raises HTTPException(401) on any validation failure, including wrong type.
    """
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        if payload.get("type") != expected_type:
            raise ValueError("Wrong token type")
        uid: str = payload["sub"]
        return uid
    except (JWTError, KeyError, ValueError):
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── ProtoPost email helper ─────────────────────────────────────────────────────


async def _send_protopost_email(to_email: str, otp: str) -> None:
    """
    Send a password reset OTP email via ProtoPost.
    POST {protopost_base_url}/send
    Authorization: Bearer {protopost_auth_token}

    Raises httpx.HTTPError on failure — caller must swallow and log.
    """
    payload = {
        "to": to_email,
        "from": settings.protopost_from_email,
        "subject": "Your Kairos Password Reset Code",
        "html": (
            f"<p>Your Kairos password reset code is:</p>"
            f"<h2 style='letter-spacing:8px'>{otp}</h2>"
            f"<p>This code expires in <strong>15 minutes</strong>.</p>"
            f"<p>If you did not request this, ignore this email.</p>"
        ),
        "text": (
            f"Your Kairos password reset code is: {otp}. "
            "It expires in 15 minutes."
        ),
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{settings.protopost_base_url}/send",
            json=payload,
            headers={
                "Authorization": f"Bearer {settings.protopost_auth_token}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()


# ── Core auth functions ───────────────────────────────────────────────────────


async def register_user(
    db: AsyncSession, req: RegisterRequest
) -> RegisterResponse:
    """
    Create a new Kairos user account.

    - Generates a new uid (uuid4).
    - Checks email uniqueness in user_credentials — 409 if taken.
    - Inserts a User row (ON CONFLICT DO NOTHING for idempotency).
    - Inserts a UserCredentials row with the bcrypt-hashed password.
    """
    try:
        # Email uniqueness check
        existing = await db.execute(
            select(UserCredentials).where(UserCredentials.email == req.email)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=409, detail="An account with this email already exists."
            )

        new_uid = uuid.uuid4()

        # Insert User row — ON CONFLICT DO NOTHING so re-runs are safe
        await db.execute(
            insert(User)
            .values(
                uid=new_uid,
                preferences={},
                allergies={},
            )
            .on_conflict_do_nothing(index_elements=["uid"])
        )

        # Insert UserCredentials row
        creds = UserCredentials(
            uid=new_uid,
            email=req.email,
            hashed_password=hash_password(req.password),
        )
        db.add(creds)

        await db.commit()

        logger.info("Registered new user uid=%s email=%s", new_uid, req.email)
        return RegisterResponse(uid=new_uid, email=req.email)

    except HTTPException:
        raise
    except Exception as exc:
        await db.rollback()
        logger.exception("register_user failed: %s", exc)
        raise HTTPException(
            status_code=500, detail="Registration failed — please try again."
        )


async def login_user(db: AsyncSession, req: LoginRequest) -> TokenResponse:
    """
    Authenticate a user and issue a token pair.

    Never distinguishes "email not found" from "wrong password" — always 401.
    """
    try:
        result = await db.execute(
            select(UserCredentials).where(UserCredentials.email == req.email)
        )
        creds: UserCredentials | None = result.scalar_one_or_none()

        # 401 on miss or wrong password — never leak "email not found"
        if creds is None or not verify_password(req.password, creds.hashed_password):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        if not creds.is_active:
            raise HTTPException(status_code=403, detail="Account deactivated")

        access_token, expires_in = create_access_token(str(creds.uid))
        refresh_token, refresh_expiry = create_refresh_token(str(creds.uid))

        # Store SHA-256 hash of refresh token — never the raw token
        creds.refresh_token_hash = hashlib.sha256(
            refresh_token.encode()
        ).hexdigest()
        creds.refresh_token_expiry = refresh_expiry

        await db.commit()

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
        )

    except HTTPException:
        raise
    except Exception as exc:
        await db.rollback()
        logger.exception("login_user failed: %s", exc)
        raise HTTPException(
            status_code=500, detail="Login failed — please try again."
        )


async def refresh_tokens(
    db: AsyncSession, req: RefreshRequest
) -> TokenResponse:
    """
    Validate a refresh token and rotate the token pair.

    - Decodes token → uid.
    - Compares SHA-256(token) against stored hash — 401 on mismatch.
    - Validates expiry — 401 if expired.
    - Always issues a new access + refresh pair (rotation).
    """
    try:
        uid_str = decode_token(req.refresh_token, expected_type="refresh")

        creds: UserCredentials | None = await db.get(
            UserCredentials, uuid.UUID(uid_str)
        )
        if creds is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token",
                                headers={"WWW-Authenticate": "Bearer"})

        incoming_hash = hashlib.sha256(req.refresh_token.encode()).hexdigest()
        if creds.refresh_token_hash != incoming_hash:
            raise HTTPException(status_code=401, detail="Invalid or expired token",
                                headers={"WWW-Authenticate": "Bearer"})

        if (
            creds.refresh_token_expiry is None
            or creds.refresh_token_expiry < datetime.now(timezone.utc)
        ):
            raise HTTPException(status_code=401, detail="Invalid or expired token",
                                headers={"WWW-Authenticate": "Bearer"})

        # Rotate
        access_token, expires_in = create_access_token(uid_str)
        new_refresh_token, new_refresh_expiry = create_refresh_token(uid_str)

        creds.refresh_token_hash = hashlib.sha256(
            new_refresh_token.encode()
        ).hexdigest()
        creds.refresh_token_expiry = new_refresh_expiry

        await db.commit()

        return TokenResponse(
            access_token=access_token,
            refresh_token=new_refresh_token,
            expires_in=expires_in,
        )

    except HTTPException:
        raise
    except Exception as exc:
        await db.rollback()
        logger.exception("refresh_tokens failed: %s", exc)
        raise HTTPException(
            status_code=500, detail="Token refresh failed — please log in again."
        )


async def logout_user(db: AsyncSession, uid: str) -> MessageResponse:
    """
    Invalidate the stored refresh token for *uid*.
    Safe to call multiple times (idempotent).
    """
    try:
        creds: UserCredentials | None = await db.get(
            UserCredentials, uuid.UUID(uid)
        )
        if creds is not None:
            creds.refresh_token_hash = None
            creds.refresh_token_expiry = None
            await db.commit()

        return MessageResponse(message="Logged out successfully")

    except Exception as exc:
        await db.rollback()
        logger.exception("logout_user failed uid=%s: %s", uid, exc)
        raise HTTPException(status_code=500, detail="Logout failed — please try again.")


async def send_password_reset(
    db: AsyncSession, req: ForgotPasswordRequest
) -> MessageResponse:
    """
    Generate and send a 6-digit OTP for password reset.

    Always returns the same generic 200 message — never leaks whether
    the email is registered.
    """
    _GENERIC = MessageResponse(
        message="If that email is registered, a reset code has been sent."
    )

    try:
        result = await db.execute(
            select(UserCredentials).where(UserCredentials.email == req.email)
        )
        creds: UserCredentials | None = result.scalar_one_or_none()

        if creds is None:
            # Email not registered — return generic response, do nothing
            return _GENERIC

        otp = str(secrets.randbelow(900000) + 100000)
        expiry = datetime.now(timezone.utc) + timedelta(minutes=15)

        creds.reset_otp = otp
        creds.reset_otp_expiry = expiry
        await db.commit()

        # Attempt delivery — swallow any failure
        try:
            await _send_protopost_email(req.email, otp)
        except Exception as delivery_exc:
            logger.warning(
                "ProtoPost delivery failed for %s: %s", req.email, delivery_exc
            )

        return _GENERIC

    except Exception as exc:
        await db.rollback()
        logger.exception("send_password_reset failed for %s: %s", req.email, exc)
        # Return generic message even on unexpected errors — never leak state
        return _GENERIC


async def reset_password(
    db: AsyncSession, req: ResetPasswordRequest
) -> MessageResponse:
    """
    Verify OTP and update the user's password.

    - 400 if email not found, OTP mismatch, or OTP expired.
    - Rotates refresh token to None (forces re-login on all devices).
    """
    _BAD = HTTPException(status_code=400, detail="Invalid or expired reset code")

    try:
        result = await db.execute(
            select(UserCredentials).where(UserCredentials.email == req.email)
        )
        creds: UserCredentials | None = result.scalar_one_or_none()

        if creds is None:
            raise _BAD

        if creds.reset_otp != req.otp:
            raise _BAD

        if (
            creds.reset_otp_expiry is None
            or creds.reset_otp_expiry < datetime.now(timezone.utc)
        ):
            raise _BAD

        creds.hashed_password = hash_password(req.new_password)
        creds.reset_otp = None
        creds.reset_otp_expiry = None
        # Force re-login on all devices
        creds.refresh_token_hash = None
        creds.refresh_token_expiry = None

        await db.commit()

        return MessageResponse(
            message="Password reset successfully. Please log in again."
        )

    except HTTPException:
        raise
    except Exception as exc:
        await db.rollback()
        logger.exception("reset_password failed for %s: %s", req.email, exc)
        raise HTTPException(
            status_code=500, detail="Password reset failed — please try again."
        )


# ── FastAPI dependency ────────────────────────────────────────────────────────


async def get_current_user(
    authorization: str = Header(..., alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> UserCredentials:
    """
    FastAPI dependency for JWT-protected endpoints.

    Usage::

        current_user: UserCredentials = Depends(get_current_user)

    Returns the UserCredentials row for the authenticated user.
    Raises HTTP 401 if the token is missing, malformed, or expired.
    """
    token = authorization.removeprefix("Bearer ").strip()
    uid_str = decode_token(token, expected_type="access")

    creds: UserCredentials | None = await db.get(
        UserCredentials, uuid.UUID(uid_str)
    )
    if creds is None or not creds.is_active:
        raise HTTPException(
            status_code=401,
            detail="User not found or deactivated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return creds
