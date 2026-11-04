"""Ticket minting endpoint for browser-based stream and WebSocket auth."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from plong_elven.api.deps import get_db, require_role
from plong_elven.db.models import User
from plong_elven.schemas.tickets import TicketOut, TicketRequest
from plong_elven.services.auth_service import AuthService

router = APIRouter(prefix="/api/v1", tags=["tickets"])


@router.post("/tickets", response_model=TicketOut)
async def mint_ticket(
    body: TicketRequest,
    user: User = Depends(require_role("admin")),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> TicketOut:
    """Mint a short-lived ticket JWT for browser endpoints.

    Requires admin role. Returns a ticket valid for 60 seconds,
    accepted only as ?ticket= on stream and WebSocket endpoints
    for the specified scope.
    """
    return AuthService(session).mint_ticket(user, body.scope)
