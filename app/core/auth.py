from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, settings

_bearer = HTTPBearer(auto_error=False)


async def require_service_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    config: Annotated[Settings, Depends(settings)],
) -> None:
    if not config.service_token.strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service authentication is not configured.",
        )

    if credentials is None or not compare_digest(
        credentials.credentials.encode("utf-8"),
        config.service_token.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing service token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
