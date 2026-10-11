from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any, TypeVar

import httpx

from promptpotter.config.settings import settings
from promptpotter.infrastructure.llm.send_failure import failed_send
from promptpotter.infrastructure.llm.send_pacing import SendBudget, parse_retry_after
from promptpotter.infrastructure.tls import tls_context
from promptpotter.shared.errors import ErrorCategory, graceful

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

_MAX_OPEN_OBSERVATIONS = 256

_WRITE_ATTEMPTS = 3
_LONGEST_HELD_S = 5.0

FLUSH_TIMEOUT_SEC: float = 5.0


def langfuse_trace_url(trace_id: str | None) -> str | None:
    if not trace_id:
        return None
    return f"{settings.LANGFUSE_HOST.rstrip('/')}/trace/{trace_id}"


class LangfuseLogger:
    def __init__(self, *, enabled: bool = True) -> None:
        # ``enabled=False`` is an ephemeral L4 inner campaign's: its traces are read locally.
        self.enabled = bool(
            enabled
            and settings.LANGFUSE_ENABLED
            and settings.LANGFUSE_SECRET_KEY
            and settings.LANGFUSE_PUBLIC_KEY
        )
        self.client = None
        self._trace_metadata: dict[str, Any] = {}
        self._open_observations: dict[str, Any] = {}
        self._rate_limit_until: float = 0.0

        if self.enabled:
            try:
                from langfuse import Langfuse

                self.client = Langfuse(
                    public_key=settings.LANGFUSE_PUBLIC_KEY,
                    secret_key=settings.LANGFUSE_SECRET_KEY,
                    host=settings.LANGFUSE_HOST,
                )
            except ImportError:
                logger.warning(
                    "langfuse package not installed — observability disabled. "
                    'Install: pip install -e ".[observability]"'
                )
                self.enabled = False
            except Exception:
                logger.warning("Failed to initialize Langfuse", exc_info=True)
                self.enabled = False

    def create_trace(
        self,
        name: str,
        input: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        tags: list[str] | None = None,
    ) -> str | None:
        if not self.enabled or not self.client:
            return None

        try:
            trace_id: str = self.client.create_trace_id()
            root = self.client.start_observation(
                trace_context={"trace_id": trace_id},
                as_type="chain",
                name=name,
                input=input,
                metadata=metadata or {},
            )
            root.update_trace(
                name=name,
                user_id=user_id,
                session_id=session_id,
                input=input,
                metadata=metadata,
                tags=tags or [],
            )
            self._trace_metadata[trace_id] = root
            return trace_id
        except Exception:
            logger.debug("Failed to create Langfuse trace", exc_info=True)
            return None

    def _evict_orphans(self) -> None:
        # An error mid-node skips `end_observation`; its span would hold its payload for the process's life.
        while len(self._open_observations) >= _MAX_OPEN_OBSERVATIONS:
            orphan_id, orphan = next(iter(self._open_observations.items()))
            del self._open_observations[orphan_id]
            logger.debug("Closing orphaned Langfuse observation %s", orphan_id)
            with graceful("Failed to close orphaned Langfuse observation"):
                orphan.end()

    def _resolve_parent(self, trace_id: str, parent_observation_id: str | None) -> Any | None:
        if parent_observation_id:
            parent = self._open_observations.get(parent_observation_id)
            if parent is not None:
                return parent
        return self._trace_metadata.get(trace_id)

    def start_span(
        self,
        trace_id: str,
        name: str,
        input: Any = None,
        metadata: dict[str, Any] | None = None,
        *,
        parent_observation_id: str | None = None,
        as_type: str = "span",
    ) -> str | None:
        if not self.enabled or not self.client or not trace_id:
            return None

        try:
            parent = self._resolve_parent(trace_id, parent_observation_id)
            if parent is None:
                return None
            child = parent.start_observation(
                as_type=as_type,
                name=name,
                input=input,
                metadata=metadata or {},
            )
            observation_id = getattr(child, "id", uuid.uuid4().hex[:12])
            self._evict_orphans()
            self._open_observations[observation_id] = child
            return observation_id
        except Exception:
            logger.debug("Failed to start Langfuse span", exc_info=True)
            return None

    def end_observation(
        self,
        observation_id: str,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled or not observation_id:
            return

        with graceful("Failed to end Langfuse observation"):
            child = self._open_observations.pop(observation_id, None)
            if child is None:
                return
            kwargs: dict[str, Any] = {}
            if output is not None:
                kwargs["output"] = output
            if metadata is not None:
                kwargs["metadata"] = metadata
            if kwargs:
                child.update(**kwargs)
            child.end()

    def create_span(
        self,
        trace_id: str,
        name: str,
        input: Any,
        output: Any,
        metadata: dict[str, Any] | None = None,
        *,
        parent_observation_id: str | None = None,
        as_type: str = "span",
        model: str | None = None,
        usage_details: dict[str, int] | None = None,
    ) -> str | None:
        if not self.enabled or not self.client or not trace_id:
            return None

        try:
            parent = self._resolve_parent(trace_id, parent_observation_id)
            if parent is None:
                return None
            kwargs: dict[str, Any] = {
                "as_type": as_type,
                "name": name,
                "input": input,
                "output": output,
                "metadata": metadata or {},
            }
            if model:
                kwargs["model"] = model
            if usage_details:
                kwargs["usage_details"] = usage_details

            child = parent.start_observation(**kwargs)
            child.end()
            return getattr(child, "id", uuid.uuid4().hex[:12])
        except Exception:
            logger.debug("Failed to log Langfuse span", exc_info=True)
            return None

    def create_score(
        self,
        trace_id: str,
        name: str,
        value: float,
        data_type: str = "NUMERIC",
        comment: str | None = None,
    ) -> bool:
        if not self.enabled or not self.client or not trace_id:
            return False

        try:
            self.client.create_score(
                trace_id=trace_id,
                name=name,
                value=value,
                data_type=data_type,
                comment=comment,
            )
            return True
        except Exception:
            logger.debug("Failed to log Langfuse score", exc_info=True)
            return False

    def update_trace(
        self,
        trace_id: str,
        output: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if not self.enabled or not self.client or not trace_id:
            return False

        try:
            root = self._trace_metadata.get(trace_id)
            if root:
                kwargs: dict[str, Any] = {}
                if output is not None:
                    kwargs["output"] = output
                if metadata is not None:
                    kwargs["metadata"] = metadata
                if kwargs:
                    root.update_trace(**kwargs)
            return True
        except Exception:
            logger.debug("Failed to update Langfuse trace", exc_info=True)
            return False

    def end_trace(self, trace_id: str) -> None:
        if not self.enabled or not self.client or not trace_id:
            return

        # POP, not get: the root span carries the trace's full I/O payload.
        with graceful("Failed to end Langfuse trace"):
            root = self._trace_metadata.pop(trace_id, None)
            if root:
                root.end()

    def reset(self) -> None:
        # Never shut the SDK client down here: it is a process-wide singleton.
        self._trace_metadata.clear()
        self._open_observations.clear()

    def create_dataset(
        self,
        name: str,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if not self.enabled or not self.client:
            return False
        try:
            self.client.create_dataset(
                name=name,
                description=description or "",
                metadata=metadata or {},
            )
            return True
        except Exception:
            logger.debug("Failed to create Langfuse dataset", exc_info=True)
            return False

    def create_dataset_item(
        self,
        dataset_name: str,
        input: Any,
        expected_output: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        client = self.client
        if not self.enabled or client is None:
            return None
        item = self._resent(
            "create dataset item",
            lambda: client.create_dataset_item(
                dataset_name=dataset_name,
                input=input,
                expected_output=expected_output,
                metadata=metadata or {},
            ),
        )
        return getattr(item, "id", None)

    def _resent(self, what: str, send: Callable[[], _T]) -> _T | None:
        if self.rate_limited:
            return None
        budget = SendBudget(None, attempts=_WRITE_ATTEMPTS)
        while True:
            try:
                return send()
            except Exception as exc:
                outcome = failed_send(exc)
                throttled = outcome.failure is ErrorCategory.PROVIDER_THROTTLED
                asked = parse_retry_after(outcome.headers) if throttled else None
                # A long throttle pauses every later write (`rate_limited`) instead of holding the run.
                if asked is not None and asked > _LONGEST_HELD_S:
                    self._rate_limit_until = time.time() + asked
                    logger.warning(
                        "Langfuse is throttling (resets in %.0fs); its writes are skipped until "
                        "then.",
                        asked,
                    )
                    return None
                wait = budget.resend_wait() if throttled or outcome.resendable else None
                if wait is None:
                    logger.warning("Langfuse %s failed: %s", what, outcome.detail[:300])
                    return None
                time.sleep(asked or wait)

    def get_dataset(self, name: str) -> object | None:
        if not self.enabled or not self.client:
            return None
        try:
            dataset: object = self.client.get_dataset(name=name)
            return dataset
        except Exception:
            logger.debug("Failed to get Langfuse dataset", exc_info=True)
            return None

    def update_dataset_item(
        self,
        item_id: str,
        expected_output: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if not self.enabled or not self.client:
            return False
        try:
            kwargs: dict[str, Any] = {"id": item_id}
            if expected_output is not None:
                kwargs["expected_output"] = expected_output
            if metadata is not None:
                kwargs["metadata"] = metadata
            self.client.create_dataset_item(**kwargs)
            return True
        except Exception:
            logger.debug("Failed to update Langfuse dataset item", exc_info=True)
            return False

    @property
    def rate_limited(self) -> bool:
        return time.time() < self._rate_limit_until

    def link_item_to_run(
        self,
        dataset_item_id: str,
        trace_id: str,
        observation_id: str | None = None,
        run_name: str = "",
        run_metadata: dict[str, Any] | None = None,
    ) -> bool:
        if not self.enabled or not self.client:
            return False
        body: dict[str, Any] = {
            "datasetItemId": dataset_item_id,
            "traceId": trace_id,
            "runName": run_name,
            "metadata": run_metadata or {},
        }
        if observation_id:
            body["observationId"] = observation_id
        url = f"{settings.LANGFUSE_HOST}/api/public/dataset-run-items"
        auth = (settings.LANGFUSE_PUBLIC_KEY, settings.LANGFUSE_SECRET_KEY)

        def post() -> bool:
            resp = httpx.post(url, auth=auth, json=body, timeout=30, verify=tls_context())
            resp.raise_for_status()
            return True

        return self._resent("link item to run", post) or False

    def flush(self) -> None:
        if not (self.enabled and self.client):
            return
        # SDK flush blocks on an uninterruptible queue.join(): a daemon thread drops spans, not Ctrl+C.
        done = threading.Event()
        client = self.client

        def _flush() -> None:
            try:
                with graceful("Failed to flush Langfuse events"):
                    client.flush()
            finally:
                done.set()

        threading.Thread(target=_flush, name="langfuse-flush", daemon=True).start()
        if not done.wait(timeout=FLUSH_TIMEOUT_SEC):
            logger.warning(
                "Langfuse flush exceeded %.0fs — dropping pending spans to "
                "keep the shutdown path responsive",
                FLUSH_TIMEOUT_SEC,
            )


__all__ = ["LangfuseLogger", "langfuse_trace_url"]
