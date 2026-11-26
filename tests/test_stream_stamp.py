import json
import math
from collections.abc import AsyncIterator

from promptpotter.application.cycle_reads import stamped_frames
from promptpotter.domain.strict_model import StrictModel


class _Level(StrictModel):
    value: float


async def _one_nan() -> AsyncIterator[StrictModel]:
    yield _Level(value=math.nan)


# A browser's `JSON.parse` refuses `NaN`, and the client drops a frame it cannot parse.
async def test_a_stream_frame_writes_a_nan_as_every_answer_does() -> None:
    raw = await anext(stamped_frames(_one_nan(), "c"))
    assert json.loads(raw, parse_constant=str) == {"value": None, "contract": "c"}, raw
