from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.auth import (
    check_app_password,
    check_rate_limit,
    create_access_token,
    get_current_user,
)
from app.config import settings
from app.models.schemas import LoginRequest, LoginResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])

_COOKIE_NAME = "audiobook_session"
_COOKIE_MAX_AGE = 720 * 3600  # 30 days
_SECURE_COOKIE = settings.domain not in ("localhost", "127.0.0.1")


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request, response: Response) -> LoginResponse:
    client_ip = request.client.host if request.client else "unknown"
    check_rate_limit(client_ip)

    if not check_app_password(body.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password")

    token = create_access_token()
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=_SECURE_COOKIE,
        samesite="lax",
        max_age=_COOKIE_MAX_AGE,
    )
    return LoginResponse(ok=True)


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(_COOKIE_NAME)
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)) -> dict:
    return {"authenticated": True, "sub": user.get("sub")}
