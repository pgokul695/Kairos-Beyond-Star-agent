"""
Auth router — /auth prefix.
Handles: register, login, logout, token refresh, forgot-password, reset-password.

Auth scheme per endpoint:
  POST /auth/register        — public
  POST /auth/login           — public
  POST /auth/refresh         — public (refresh token in body)
  POST /auth/logout          — Bearer access token required (Authorization header)
  POST /auth/forgot-password — public
  POST /auth/reset-password  — public (OTP in body)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
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
from app.services.auth_service import (
    decode_token,
    login_user,
    logout_user,
    refresh_tokens,
    register_user,
    reset_password,
    send_password_reset,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a new Kairos account."""
    return await register_user(db, body)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate and receive access + refresh tokens."""
    return await login_user(db, body)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    """Exchange a valid refresh token for a new token pair."""
    return await refresh_tokens(db, body)


@router.post("/logout", response_model=MessageResponse)
async def logout(
    authorization: str = Header(..., alias="Authorization"),
    db: AsyncSession = Depends(get_db),
):
    """Invalidate the current refresh token. Requires a valid Bearer access token."""
    token = authorization.removeprefix("Bearer ").strip()
    uid = decode_token(token, expected_type="access")
    return await logout_user(db, uid)


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(
    body: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Trigger a password reset OTP email via ProtoPost. Always returns 200."""
    return await send_password_reset(db, body)


@router.post("/reset-password", response_model=MessageResponse)
async def reset_pwd(
    body: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Verify OTP and update password."""
    return await reset_password(db, body)
