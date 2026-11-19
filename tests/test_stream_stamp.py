import json
import math
from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from promptpotter.application.caller import Caller
from promptpotter.application.cycle_reads import stamped_frames
from promptpotter.application.read_params import FeedParams
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.main import app
from promptpotter.presentation.api.middleware.headers import served_contract


# An EventSource reads no header: a stale tab learns the stream's shape moved from the frame alone.
async def test_a_stream_frame_names_the_contract_every_answer_names(built_stores: Stores) -> None:
    named = TestClient(app).get("/api/v1/health").headers["x-served-contract"]
    frames = stamped_frames(FeedParams().answer(Caller(built_stores)), served_contract(app))
    first = json.loads(await anext(frames))
    assert (first["contract"], first["subject"]) == (named, "store:")


class _Level(StrictModel):
    value: float


async def _one_nan() -> AsyncIterator[StrictModel]:
    yield _Level(value=math.nan)


# A browser's `JSON.parse` refuses `NaN`, and the client drops a frame it cannot parse.
async def test_a_stream_frame_writes_a_nan_as_every_answer_does() -> None:
    raw = await anext(stamped_frames(_one_nan(), "c"))
    assert json.loads(raw, parse_constant=str) == {"value": None, "contract": "c"}, raw
