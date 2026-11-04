"""Ticket request/response schemas."""

from pydantic import BaseModel, Field


class TicketRequest(BaseModel):
    scope: str = Field(pattern=r"^(stream:[A-Za-z0-9_-]{1,64}|crowd:live)$")


class TicketOut(BaseModel):
    ticket: str
    expiresIn: int
