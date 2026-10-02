import hmac

from fastapi import Header, HTTPException, Request, status


def _bearer_value(authorization: str | None) -> str:
    if authorization is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_bearer_token")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_bearer_token")
    return token.strip()


async def current_user_id(authorization: str | None = Header(default=None)) -> str:
    user_id = _bearer_value(authorization)
    if len(user_id) > 200:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_bearer_token")
    return user_id


async def require_admin(request: Request, authorization: str | None = Header(default=None)) -> None:
    token = _bearer_value(authorization)
    expected = request.app.state.settings.admin_token
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin_required")