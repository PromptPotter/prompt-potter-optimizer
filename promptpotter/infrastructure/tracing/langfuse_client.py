"""Langfuse SDK wrapper — explicit-args client with project isolation via API keys.

One root span per trace, child spans nested via ``start_observation``.
Per-call observability failures log at DEBUG (a lost span is expected
during network blips); setup + post-retry failures log at WARNING.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import httpx

from promptpotter.config.settings import settings
from promptpotter.shared.errors import graceful

logger = logging.getLogger(__name__)


class LangfuseLogger:
    """Langfuse SDK wrapper. Disabled if credentials are missing."""

    def __init__(self) -> None:

        self.enabled = bool(
            settings.LANGFUSE_ENABLED
            and settings.LANGFUSE_SECRET_KEY
            and settings.LANGFUSE_PUBLIC_KEY
        )
        self.client = None
        self._trace_metadata: dict[str, Any] = {}  # trace_id → root SDK observation
        self._open_observations: dict[str, Any] = {}  # observation_id → open SDK observation
        self._rate_limit_until: float = 0.0  # unix ts when quota resets

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

    def create_trace_id(self) -> str | None:
        """Bare trace ID without a root observation — keeps pipeline traces
        from collapsing into a root chain in the Langfuse graph view."""
        if not self.enabled or not self.client:
            return None
        try:
            return self.client.create_trace_id()
        except Exception:
            logger.debug("Failed to create Langfuse trace ID", exc_info=True)
            return None

    def create_trace(
        self,
        name: str,
        input: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        tags: list[str] | None = None,
    ) -> str | None:
        """Create trace with a root span; pushes metadata via ``update_trace``
        so the cloud UI shows full info instead of an auto-stub."""
        if not self.enabled or not self.client:
            return None

        try:
            trace_id = self.client.create_trace_id()
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

    def _resolve_parent(self, trace_id: str, parent_observation_id: str | None) -> Any | None:
        """Find an SDK observation to nest under: explicit open obs → root → None."""
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
        """Start a long-running observation; pair with ``end_observation``."""
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
        """Log a closed observation nested under root or a parent obs."""
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
        """Push trace-level output/metadata via the root span's ``update_trace``."""
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

        with graceful("Failed to end Langfuse trace"):
            root = self._trace_metadata.get(trace_id)
            if root:
                root.end()

    # -- Dataset API ---------------------------------------------------------

    def create_dataset(
        self,
        name: str,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Idempotent — no error if the dataset exists."""
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
        _max_retries: int = 3,
    ) -> str | None:
        """Retries with exponential backoff on 429."""
        if not self.enabled or not self.client:
            return None
        for attempt in range(_max_retries):
            try:
                item = self.client.create_dataset_item(
                    dataset_name=dataset_name,
                    input=input,
                    expected_output=expected_output,
                    metadata=metadata or {},
                )
                return getattr(item, "id", None)
            except Exception as exc:
                if "429" in str(exc) and attempt < _max_retries - 1:
                    delay = 2**attempt
                    logger.debug("Langfuse 429 rate limit, retry in %ds", delay)
                    time.sleep(delay)
                    continue
                logger.warning("Failed to create Langfuse dataset item", exc_info=True)
                return None
        return None

    def get_dataset(self, name: str) -> object | None:
        if not self.enabled or not self.client:
            return None
        try:
            return self.client.get_dataset(name=name)
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
        *,
        max_retries: int = 3,
    ) -> bool:
        """Link trace/observation to a dataset item via REST (the SDK only
        exposes a context-manager approach unsuitable for backfill).

        429 with Retry-After > 300s sets ``rate_limited`` and returns False
        so callers can stop early instead of retrying for hours.
        """
        if not self.enabled or not self.client:
            return False
        if self.rate_limited:
            return False
        try:
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

            for attempt in range(max_retries):
                resp = httpx.post(url, auth=auth, json=body, timeout=30)

                if resp.status_code in (200, 201):
                    return True

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "0"))
                    remaining = int(resp.headers.get("X-RateLimit-Remaining", "-1"))

                    # Daily quota exhausted (Retry-After > 5 min) — stop trying
                    if retry_after > 300 or remaining == 0:
                        self._rate_limit_until = time.time() + retry_after
                        logger.warning(
                            "Langfuse daily rate limit hit (resets in %ds). "
                            "Skipping remaining link_item_to_run calls.",
                            retry_after,
                        )
                        return False

                    # Short-term rate limit — back off and retry
                    wait = min(2**attempt, retry_after or 2**attempt)
                    logger.debug("Rate limited, waiting %ds (attempt %d)", wait, attempt + 1)
                    time.sleep(wait)
                    continue

                # Other HTTP error
                logger.warning(
                    "Link item to run HTTP %s: %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return False

            logger.warning("link_item_to_run exhausted %d retries", max_retries)
            return False
        except Exception:
            logger.warning("Failed to link dataset item to run", exc_info=True)
            return False

    def flush(self) -> None:
        if self.enabled and self.client:
            with graceful("Failed to flush Langfuse events"):
                self.client.flush()


__all__ = ["LangfuseLogger"]
