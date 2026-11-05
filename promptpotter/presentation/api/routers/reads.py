"""Every `ReadKind` row, mounted by one loop at the route its params declare: named by its kind,
described by its params' docstring, its input its params (`deps.py::read_input`) and its answer
their `answer`.

What varies per row is only how HTTP frames the answer (`_FRAMED`): a validator answering 304
before the work, a file or a stream in place of JSON, the arrival `me` records. Every other row
answers its model.
"""

from __future__ import annotations

import hashlib
import inspect
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import PurePosixPath
from typing import Annotated, Any, cast

from fastapi import APIRouter, BackgroundTasks, Request, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sse_starlette import EventSourceResponse

from promptpotter.application.caller import Caller
from promptpotter.application.hierarchy import hierarchy_version
from promptpotter.application.hierarchy_subtree import subtree_version
from promptpotter.application.read_params import (
    PARAMS_FOR_READ,
    CampaignPipelineParams,
    CycleDashboardParams,
    CycleEventsParams,
    CycleExportParams,
    DatasetCellsParams,
    EvidenceParams,
    HierarchyParams,
    HierarchyStepsParams,
    HierarchySubtreeParams,
    MeParams,
    ReadParams,
    off_loop,
)
from promptpotter.domain.command_kinds import ReadKind
from promptpotter.domain.projection_envelope import ProjectionEnvelope
from promptpotter.presentation.api.deps import CallerDep, read_input
from promptpotter.presentation.api.routers.auth import arrive
from promptpotter.shared.named_rows import returned

reads_router = APIRouter(tags=["Reads"])

# A restart may serve new code over unchanged inputs, so no ETag outlives its process.
_PROCESS = os.urandom(8).hex()


def _http_date(epoch_seconds: float) -> str:
    return format_datetime(datetime.fromtimestamp(int(epoch_seconds), tz=UTC), usegmt=True)


def _seen_at_or_after(if_modified_since: str | None, mtime_epoch: float) -> bool:
    if not if_modified_since:
        return False
    try:
        client_dt = parsedate_to_datetime(if_modified_since)
    except (TypeError, ValueError):
        return False
    return int(client_dt.timestamp()) >= int(mtime_epoch)


def _weak(digest: str) -> str:
    return f'W/"{digest[:32]}"'


def client_has_etag(if_none_match: str | None, etag: str) -> bool:
    """Tolerates a proxy having stripped ``W/``: weak comparison ignores it."""
    if not if_none_match:
        return False
    wanted = etag.removeprefix("W/")
    for candidate in if_none_match.split(","):
        tag = candidate.strip()
        if tag == "*" or tag.removeprefix("W/") == wanted:
            return True
    return False


def _model_json(model: BaseModel, *, headers: dict[str, str] | None = None) -> Response:
    """Serialized ONCE: returned as a model, FastAPI dumps, validates and encodes it again."""
    return Response(
        content=model.model_dump_json(by_alias=True),
        media_type="application/json",
        headers=headers,
    )


def _conditional(
    request: Request, version: tuple[object, ...], build: Callable[[], BaseModel]
) -> Response:
    """A 304 before *build* runs: an input or query value left out of *version* goes stale."""
    etag = _weak(hashlib.sha256("\x1f".join(map(repr, (_PROCESS, *version))).encode()).hexdigest())
    if client_has_etag(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers={"ETag": etag})
    return _model_json(build(), headers={"ETag": etag})


def _hashed(request: Request, model: BaseModel, *, stamp: str | None = None) -> Response:
    """Decided after the work, for a read no input stamp covers; ``stamp`` moves on every request."""
    response = _model_json(model)
    validated = (
        model.model_dump_json(by_alias=True, exclude={stamp}).encode() if stamp else response.body
    )
    etag = _weak(hashlib.sha256(validated).hexdigest())
    if client_has_etag(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return response


@dataclass(frozen=True, slots=True)
class _Asked:
    """What a framed row reads of its request beside its params."""

    request: Request
    background: BackgroundTasks
    caller: Caller


def _me(asked: _Asked, read: MeParams) -> Response:
    arrive(asked.request, asked.background, asked.caller.stores)
    return _model_json(read.answer(asked.caller))


def _hierarchy(asked: _Asked, read: HierarchyParams) -> Response:
    version = hierarchy_version(asked.caller.stores, inside=read.inside)
    return _conditional(asked.request, (version, read), lambda: read.answer(asked.caller))


def _steps(asked: _Asked, read: HierarchyStepsParams) -> Response:
    steps = read.opened(asked.caller.stores)
    return _conditional(asked.request, steps.validator, steps.window)


def _subtree(asked: _Asked, read: HierarchySubtreeParams) -> Response:
    version = subtree_version(asked.caller.stores, read.address)
    return _conditional(asked.request, (version, read), lambda: read.answer(asked.caller))


def _dashboard(asked: _Asked, read: CycleDashboardParams) -> Response:
    cycle = read.viewed(asked.caller.stores)
    headers: dict[str, str] = {}
    if (mtime_epoch := cycle.dashboard_changed_at) is not None:
        headers["Last-Modified"] = _http_date(mtime_epoch)
        if _seen_at_or_after(asked.request.headers.get("if-modified-since"), mtime_epoch):
            return Response(status_code=304, headers=headers)
    return _model_json(read.served(cycle), headers=headers)


_EXPORT_MEDIA = {".txt": "text/plain", ".json": "application/json", ".zip": "application/zip"}


def _export(asked: _Asked, read: CycleExportParams) -> Response:
    files = read.answer(asked.caller)
    name = files.download_name
    single = files.single
    return Response(
        content=files.zipped() if single is None else single.encode(),
        media_type=_EXPORT_MEDIA[PurePosixPath(name).suffix],
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


def _events(asked: _Asked, read: CycleEventsParams) -> Response:
    # LF is the contract (the default is CRLF); X-Accel-Buffering is the header sse-starlette omits.
    frames = read.answer(asked.caller)
    return EventSourceResponse(frames, sep="\n", headers={"X-Accel-Buffering": "no"})


def _evidence(asked: _Asked, read: EvidenceParams) -> Response:
    return _hashed(asked.request, read.answer(asked.caller), stamp="generated_at")


def _pipeline(asked: _Asked, read: CampaignPipelineParams) -> Response:
    return _hashed(asked.request, read.answer(asked.caller))


def _cells(asked: _Asked, read: DatasetCellsParams) -> Response:
    cells = read.scoped(asked.caller.stores)
    return _conditional(asked.request, (cells.version(), read), lambda: read.logged(cells))


@dataclass(frozen=True, slots=True)
class _Framing:
    """A row's own HTTP framing: run on the reads' workers, as `answered` runs a sync answer;
    *route* overrides what the mount derives (a stream's or a file's documented answer)."""

    respond: Callable[[_Asked, Any], Response]
    route: dict[str, Any] = field(default_factory=dict)


_FRAMED: dict[ReadKind, _Framing] = {
    ReadKind.ME: _Framing(_me),
    ReadKind.HIERARCHY: _Framing(_hierarchy),
    ReadKind.HIERARCHY_STEPS: _Framing(_steps),
    ReadKind.HIERARCHY_SUBTREE: _Framing(_subtree),
    ReadKind.CYCLE_DASHBOARD: _Framing(_dashboard),
    ReadKind.CYCLE_EXPORT: _Framing(
        _export,
        {
            "response_class": Response,
            "response_model": None,
            "responses": {200: {"content": {media: {} for media in _EXPORT_MEDIA.values()}}},
        },
    ),
    # Its model documents one frame of the stream.
    ReadKind.CYCLE_EVENTS: _Framing(
        _events,
        {"response_class": EventSourceResponse, "response_model": ProjectionEnvelope},
    ),
    ReadKind.EVIDENCE: _Framing(_evidence),
    ReadKind.CAMPAIGN_PIPELINE: _Framing(_pipeline),
    ReadKind.DATASET_CELLS: _Framing(_cells),
}


def _endpoint(params: type[ReadParams], framing: _Framing | None) -> Callable[..., Any]:
    async def answer(
        request: Request, background: BackgroundTasks, caller: Caller, read: ReadParams
    ) -> object:
        if framing is None:
            return await read.answered(caller)
        return await off_loop(framing.respond, _Asked(request, background, caller), read)

    keyword = inspect.Parameter.KEYWORD_ONLY
    answer.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [
            inspect.Parameter("request", keyword, annotation=Request),
            inspect.Parameter("background", keyword, annotation=BackgroundTasks),
            inspect.Parameter("caller", keyword, annotation=CallerDep),
            inspect.Parameter("read", keyword, annotation=Annotated[params, read_input(params)]),
        ]
    )
    return answer


# Each row's one route, as the loop below made it: the surface refuses any other route a read
# names (`main.py::_off_catalogue`).
READ_ROUTES: dict[ReadKind, APIRoute] = {}
# In row order, which is route precedence: `/hierarchy/{address:path}/@` before its subtree.
for _read in ReadKind:
    _params = PARAMS_FOR_READ[_read]
    _framing = _FRAMED.get(_read)
    reads_router.add_api_route(
        _params.route,
        _endpoint(_params, _framing),
        methods=[_params.method],
        name=_read,
        description=inspect.cleandoc(_params.__doc__ or ""),
        **{"response_model": returned(_params.answer), **(_framing.route if _framing else {})},
    )
    READ_ROUTES[_read] = cast(APIRoute, reads_router.routes[-1])
