"""Every exception the API answers with, mapped to a status code in one place.

Routes raise or propagate; nothing about a status code is decided per-endpoint. That is
what stops the mapping from drifting -- a second route raising `InvalidHierarchy` gets the
same 409 without repeating a `try/except`, and a failure mode nobody thought about does not
quietly become a different code on a different path.

Every body built here is an `ErrorBody`, so all of them parse the same way.
"""
import logging
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.controllers.hierarchy import InvalidHierarchy
from app.schemas.error import ErrorBody

logger = logging.getLogger(__name__)

# Fixed text, and deliberately so. `str(IntegrityError)` carries the failing statement and
# its bound parameters, which is exactly what an error body must not hand a caller. The
# specifics go to the log, where the operator can see them and the client cannot.
INTEGRITY_DETAIL = 'The request conflicts with what is already stored'

# A malformed payload can fail validation in as many places as it has nodes. Reporting the
# first few names what to fix without turning one bad request into a response larger than
# the request that caused it.
MAX_REPORTED_VALIDATION_ERRORS = 5

# Says nothing, on purpose: an exception nobody anticipated is the one most likely to have
# a connection string or a statement inside it. Same wording Starlette uses, so the change
# is the content type and not what a caller reads.
UNHANDLED_DETAIL = 'Internal Server Error'


def error_response(status_code: int, detail: str) -> JSONResponse:
    """One body shape, built in one place, for every refusal."""
    return JSONResponse(status_code=status_code, content=ErrorBody(detail=detail).model_dump())


def describe_validation_error(error: Mapping[str, Any]) -> str:
    """Render one Pydantic error as `where: what`, e.g. `body.children.0.type: ...`.

    `loc` and `msg` only. The raw error also carries `input` -- the offending value itself,
    which for a nested payload is the whole subtree -- and serialising that is what makes
    FastAPI's stock handler raise RecursionError on a deeply nested body, answering 500 to
    what should be a 422. Naming the location instead is both smaller and more useful.
    """
    location = '.'.join(str(part) for part in error['loc'])
    return f'{location}: {error["msg"]}' if location else str(error['msg'])


async def handle_invalid_hierarchy(request: Request, exception: InvalidHierarchy) -> JSONResponse:
    """409: a payload that cannot be stored, as opposed to one that failed to store.

    The controller's message names the node at fault and nothing else, so it goes through
    as the detail.
    """
    return error_response(409, str(exception))


async def handle_integrity_error(request: Request, exception: IntegrityError) -> JSONResponse:
    """409: the database refused the write.

    The live case is `uq_node_single_parent`, the partial unique index that permits one
    depth-1 closure row per node. It is the last line of defence for the invariant the
    write path is supposed to maintain on its own, so reaching it means a bug -- but a
    refused request is the honest answer to the caller either way, and a stack trace is
    not. The traceback is logged rather than returned.
    """
    logger.error('a write was refused by a database constraint', exc_info=exception)
    return error_response(409, INTEGRITY_DETAIL)


async def handle_validation_error(
    request: Request, exception: RequestValidationError
) -> JSONResponse:
    """422: the request did not match the schema.

    Replaces FastAPI's default handler, which answers with a list of error objects rather
    than the string every other error uses. Same information, one shape.
    """
    errors = exception.errors()
    reported = [
        describe_validation_error(error) for error in errors[:MAX_REPORTED_VALIDATION_ERRORS]
    ]
    unreported = len(errors) - len(reported)
    if unreported:
        reported.append(f'and {unreported} more')
    return error_response(422, '; '.join(reported))


async def handle_unhandled(request: Request, exception: Exception) -> JSONResponse:
    """500: something nobody mapped. Still JSON, still `{"detail": ...}`.

    Without this the one response a caller cannot parse is the one they most need to
    report: Starlette answers an unhandled exception with the plain string
    `Internal Server Error`, so a client parsing the error body has to special-case it.

    Nothing is swallowed. Starlette re-raises after the response is sent, so the traceback
    still reaches the server log; all that changes is what the caller reads.
    """
    logger.error('unhandled exception serving %s %s', request.method, request.url.path)
    return error_response(500, UNHANDLED_DETAIL)


def register_error_handlers(app: FastAPI) -> None:
    """Wire the handlers above onto an app.

    Starlette dispatches on the exception's MRO, so registering the base class is enough --
    every `IntegrityError` subclass Postgres can produce lands on the same handler.
    """
    app.add_exception_handler(InvalidHierarchy, handle_invalid_hierarchy)
    app.add_exception_handler(IntegrityError, handle_integrity_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_unhandled)
