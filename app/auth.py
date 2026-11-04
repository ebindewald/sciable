"""Operator authentication for the control-plane API.

v1 is single-operator. When ``operator_token`` is configured, API submission and
result access require a matching bearer token; when unset, the API is open for a
single operator running on localhost. Per-user ownership is deferred with
multi-tenancy (see IMPLEMENTATION_PLAN deferred list).
"""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings

_bearer = HTTPBearer(auto_error=False)


def require_operator(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    settings: Settings = request.app.state.settings
    expected = settings.operator_token
    if not expected:
        return  # open mode (single operator, localhost)
    if creds is None or not secrets.compare_digest(creds.credentials, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Operator authorization required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
