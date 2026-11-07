"""Common Pydantic validators and patterns shared across schemas."""

# Camera ID validation: alphanumeric plus dash/underscore, 1-64 chars.
# Rejects XSS payloads like <script>, quotes, and excessive lengths.
CAMERA_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
