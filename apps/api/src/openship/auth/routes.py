from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..auth.models import User
from ..auth.service import create_jwt, hash_password, verify_password, verify_jwt
from ..database.session import get_db
from .schemas import LoginRequest, RegisterRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])


def require_jwt(request: Request) -> User:
    """Extract and verify JWT token, return the authenticated user."""
    authorization = request.headers.get("Authorization")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "unauthorized",
                    "message": "Missing or invalid authorization header",
                }
            },
        )

    token = authorization.split(" ", 1)[1]
    try:
        payload = verify_jwt(token)
    except Exception:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "unauthorized",
                    "message": "Invalid or expired token",
                }
            },
        )

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "unauthorized",
                    "message": "Token missing user ID",
                }
            },
        )

    import uuid as _uuid

    db = next(get_db())
    try:
        user = db.query(User).filter(User.id == _uuid.UUID(user_id_str)).first()
        if not user:
            raise HTTPException(
                status_code=401,
                detail={
                    "error": {
                        "code": "unauthorized",
                        "message": "User not found",
                    }
                },
            )
        return user
    finally:
        db.close()


@router.post("/register", status_code=201)
async def register(req: RegisterRequest, db: Session = Depends(get_db)):
    # Check for duplicate username
    existing_user = (
        db.query(User).filter(User.username == req.username).first()
    )
    if existing_user:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "username_taken",
                    "message": "Username already taken",
                }
            },
        )

    # Check for duplicate email
    existing_email = (
        db.query(User).filter(User.email == req.email).first()
    )
    if existing_email:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "email_taken",
                    "message": "Email already registered",
                }
            },
        )

    # Create new user
    user = User(
        username=req.username,
        email=req.email,
        password_hash=hash_password(req.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "access_token": create_jwt(user.id, user.username),
        "user": {"id": str(user.id), "username": user.username},
    }


@router.post("/login", status_code=200)
async def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = (
        db.query(User).filter(User.username == req.username).first()
    )
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "invalid_credentials",
                    "message": "Invalid username or password",
                }
            },
        )

    return {
        "access_token": create_jwt(user.id, user.username),
        "user": {"id": str(user.id), "username": user.username},
    }
