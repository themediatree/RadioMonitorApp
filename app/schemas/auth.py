"""Pydantic schemas for the auth endpoints."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    """JSON login (used by the API). HTML form login uses Form() directly."""
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class CurrentUserResponse(BaseModel):
    user_id: int
    email: str
    full_name: Optional[str] = None
    user_type: str
    subscriber_id: Optional[int] = None
    last_login: Optional[datetime] = None
