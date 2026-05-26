"""EventStreamView Profile A invariants — bundled.

One canonical case per contract (per `tests/CLAUDE.md`). Asserts the
sole-writer / snapshot-then-tail / heartbeat / drain semantics declared in
``docs/specs/m12-events-asyncapi.yaml`` and ``docs/developer/event-stream.md``.

We exercise ``EventStreamView`` directly (not through HTTP) so the test is
deterministic and fast; the SSE handler is a thin async adapter over this
API and the same invariants apply transitively.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.projection_envelope import ProjectionEnvelope
from promptpotter.domain.run_records import PhaseRecord, TokenUsageRecord
from promptpotter.infrastructure.projections import EventStreamView


def test_event_stream_view_contract() -> None:
    """Bundled assertion: snapshot offset capture, sequence monotonicity,
    fan-out to multiple subscribers, drain closes everyone, heartbeat ticks."""

    async def _body() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cycle_root = Path(tmp) / "cycle_abc123"
            cycle_root.mkdir()
            # Pre-seed dashboard.json so the snapshot frame has real content;
            # the view resolves to the session root via root_cycle_id, which
            # strips fork suffixes — for a root-shaped id the resolved path
            # is the cycle dir itself.
            (cycle_root / "dashboard.json").write_text(
                json.dumps({"campaign_id": "x", "rounds": []}),
                encoding="utf-8",
            )
            view = EventStreamView(
                CycleDir(cycle_root),
                cycle_id="cycle_abc123",
                initial_offset=42,
            )

            # --- Subscribe captures the initial offset (resume semantics) ---
            sub_a = view.subscribe()
            assert sub_a.start_offset == 42, (
                f"snapshot offset must mirror ledger.next_offset at subscribe, "
                f"got {sub_a.start_offset}"
            )
            assert view.subscriber_count() == 1

            # --- Snapshot payload reflects dashboard.json + snapshot_at_offset ---
            snapshot_payload = view.snapshot_payload()
            assert snapshot_payload["snapshot_at_offset"] == 42
            assert snapshot_payload["campaign_id"] == "x"

            # --- Multi-subscriber fan-out ---
            sub_b = view.subscribe()
            assert sub_b.start_offset == 42
            assert view.subscriber_count() == 2

            # --- Live tail: feed two records, both subscribers get both ---
            sub_a.attach_loop(asyncio.get_running_loop())
            sub_b.attach_loop(asyncio.get_running_loop())
            view.on_record(
                PhaseRecord(phase="round", event="start", round=1),
                offset=42,
            )
            view.on_record(
                TokenUsageRecord(
                    kind="optimizer",
                    node="l1_generate",
                    input_tokens=100,
                    output_tokens=50,
                ),
                offset=43,
            )

            # next_offset must advance past the last broadcast record
            assert view.next_offset == 44

            # Drain each subscriber's queue — strict order + sequence + kind
            received_a: list[ProjectionEnvelope] = []
            received_b: list[ProjectionEnvelope] = []
            for _ in range(2):
                received_a.append(await asyncio.wait_for(sub_a._queue.get(), timeout=1.0))
                received_b.append(await asyncio.wait_for(sub_b._queue.get(), timeout=1.0))

            for envs in (received_a, received_b):
                assert [e.kind for e in envs] == ["phase", "token_usage"]
                assert [e.sequence for e in envs] == [42, 43]
                assert all(e.cycle_id == "cycle_abc123" for e in envs)

            # --- Heartbeat: when idle, stream yields None per cadence ---
            heartbeat_seen = False

            async def _watch_heartbeat() -> None:
                nonlocal heartbeat_seen
                async for frame in sub_a.stream(heartbeat_interval_s=0.05):
                    if frame is None:
                        heartbeat_seen = True
                        return

            await asyncio.wait_for(_watch_heartbeat(), timeout=1.0)
            assert heartbeat_seen, "idle subscriber must yield None for heartbeat"

            # --- Drain closes every subscriber + clears registry-side fan-out ---
            view.drain()
            assert sub_a.closed and sub_b.closed
            assert view.subscriber_count() == 0

            # Post-drain publishes are no-ops (no exception)
            view.on_record(
                PhaseRecord(phase="round", event="complete", round=1),
                offset=44,
            )

    asyncio.run(_body())
