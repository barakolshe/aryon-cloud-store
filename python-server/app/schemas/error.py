"""The one body shape every error response uses."""
from pydantic import BaseModel


class ErrorBody(BaseModel):
    """What a client gets back from any refused request, whatever refused it.

    One key holding one string. FastAPI ships two different error bodies out of the box --
    `{"detail": "..."}` from `HTTPException` and `{"detail": [{"loc": ..., "msg": ...}, ...]}`
    from request validation -- so a client that wants to show the reason has to branch on
    which layer produced the failure. Everything is normalised onto this shape instead, and
    `detail` is always a string, so reading an error is the same line of code every time.

    The string is for a human to read; the status code is what a client should branch on.
    """

    detail: str
