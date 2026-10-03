from pydantic import BaseModel, Field, field_validator


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: str
    password: str = Field(..., min_length=8)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not v or "@" not in v:
            raise ValueError("Invalid email format")
        parts = v.split("@")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError("Invalid email format")
        if "." not in parts[1]:
            raise ValueError("Invalid email format")
        return v.lower()


class LoginRequest(BaseModel):
    username: str
    password: str
