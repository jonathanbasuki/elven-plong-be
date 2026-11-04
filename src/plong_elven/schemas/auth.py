"""Authentication request/response schemas."""

from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class RefreshRequest(BaseModel):
    refreshToken: str = Field(min_length=1)


class PushTokenRequest(BaseModel):
    pushToken: str = Field(min_length=1)


class UserResponse(BaseModel):
    id: UUID
    email: EmailStr
    fullName: str
    role: str
    isActive: bool


class TokenResponse(BaseModel):
    accessToken: str
    refreshToken: str
    tokenType: str = "bearer"
    expiresIn: int
    user: UserResponse


class RefreshResponse(BaseModel):
    accessToken: str
    refreshToken: str
    tokenType: str = "bearer"
    expiresIn: int
