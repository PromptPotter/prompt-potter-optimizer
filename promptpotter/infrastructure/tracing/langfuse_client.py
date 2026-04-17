"""
Langfuse SDK wrapper for workflow observability.

Provides singleton access to Langfuse client with automatic project isolation
via API keys. Logs traces, observations (generations/spans), and scores.

Uses the Langfuse SDK v3 ``start_span()`` / ``start_generation()`` API to build
a proper trace hierarchy: one root span per trace, child spans nested underneath.

Usage:
    langfuse = LangfuseLogger.get_instance()
    trace_id = langfuse.create_trace("workflow_name", inputs)
    langfuse.create_span(trace_id, "step_name", input, output)
    langfuse.create_score(trace_id, "accuracy", 0.95)
    langfuse.end_trace(trace_id)
    langfuse.flush()
"""

import logging
import os
import time
import uuid
from typing import Any

import httpx

from promptpotter.shared.errors import graceful

logger = logging.getLogger(__name__)


class LangfuseLogger:
    """Wrapper for Langfuse SDK with project isolation.

    Project isolation is automatic via API keys:
    - Each Langfuse project has unique LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY
    - Set these in .env to route data to the correct project

    Gracefully disables logging if credentials are missing.
    """

    _instance: "LangfuseLogger | None" = None

    def __init__(self) -> None:
        """Initialize Langfuse client from settings."""
        from promptpotter.config.settings import settings

        self.enabled = bool(
            settings.LANGFUSE_ENABLED
            and settings.LANGFUSE_SECRET_KEY
            and settings.LANGFUSE_PUBLIC_KEY
        )
        self.client = None
        # Maps trace_id → root SDK observation object (not a plain dict)
        self._trace_metadata: dict[str, Any] = {}
        # Maps obs_id → open SDK observation (for long-running spans)
        self._open_observations: dict[str, Any] = {}
        # Rate-limit tracking for REST API calls
        self._rate_limit_until: float = 0.0  # unix timestamp when quota resets

        if self.enabled:
            try:
                os.environ["LANGFUSE_PUBLIC_KEY"] = settings.LANGFUSE_PUBLIC_KEY
                os.environ["LANGFUSE_SECRET_KEY"] = settings.LANGFUSE_SECRET_KEY
                os.environ["LANGFUSE_HOST"] = settings.LANGFUSE_HOST

                from langfuse import Langfuse

                self.client = Langfuse()
            except ImportError:
                logger.warning(
                    "langfuse package not installed — observability disabled. "
                    'Install: pip install -e ".[observability]"'
                )
                self.enabled = False
            except Exception:
                logger.warning("Failed to initialize Langfuse", exc_info=True)
                self.enabled = False

    @classmethod
    def get_instance(cls) -> "LangfuseLogger":
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (useful for testing)."""
        cls._instance = None

    def create_trace_id(self) -> str | None:
        """Create a bare trace ID without a root observation.

        Use this for pipeline traces where step observations carry trace metadata
        via ``trace_params`` on ``create_top_level_observation()``. This avoids
        creating a root chain that collapses steps in the Langfuse graph view.
        """
        if not self.enabled or not self.client:
            return None
        try:
            return self.client.create_trace_id()
        except Exception:
            logger.warning("Failed to create Langfuse trace ID", exc_info=True)
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
        """Create a new trace with a root span and proper metadata.

        Creates a trace_id, starts a root span linked to it, then calls
        ``update_trace()`` on the root span to set trace-level metadata
        (name, session_id, tags, input). This ensures the trace appears in
        Langfuse with full metadata instead of as a bare auto-created stub.

        Returns trace_id or None if logging is disabled.
        """
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
            logger.warning("Failed to create Langfuse trace", exc_info=True)
            return None

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
        """Start a long-running observation. Call end_observation() when done.

        Returns observation_id or None if logging is disabled.
        """
        if not self.enabled or not self.client or not trace_id:
            return None

        try:
            # Find parent: explicit parent obs, or root trace span
            parent = None
            if parent_observation_id:
                parent = self._open_observations.get(parent_observation_id)
            if parent is None:
                parent = self._trace_metadata.get(trace_id)
            if parent is None:
                return None

            child = parent.start_observation(
                as_type=as_type,
                name=name,
                input=input,
                metadata=metadata or {},
            )
            obs_id = getattr(child, "id", uuid.uuid4().hex[:12])
            self._open_observations[obs_id] = child
            return obs_id
        except Exception:
            logger.warning("Failed to start Langfuse span", exc_info=True)
            return None

    def end_observation(
        self,
        obs_id: str,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Close a previously started observation (span/chain/tool)."""
        if not self.enabled or not obs_id:
            return

        with graceful("Failed to end Langfuse observation"):
            child = self._open_observations.pop(obs_id, None)
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
        """Log a non-LLM observation nested under root or a parent observation.

        Args:
            parent_observation_id: Nest under this open observation instead of root.
            as_type: Observation type (``span``, ``tool``, ``chain``, etc.).
            model: Model name (for generation-type steps).
            usage_details: Token usage dict (for generation-type steps).

        Returns observation_id or None if logging is disabled.
        """
        if not self.enabled or not self.client or not trace_id:
            return None

        try:
            parent = None
            if parent_observation_id:
                parent = self._open_observations.get(parent_observation_id)
            if parent is None:
                parent = self._trace_metadata.get(trace_id)
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
            logger.warning("Failed to log Langfuse span", exc_info=True)
            return None

    def create_score(
        self,
        trace_id: str,
        name: str,
        value: float,
        data_type: str = "NUMERIC",
        comment: str | None = None,
    ) -> bool:
        """Log an evaluation score for a trace."""
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
            logger.warning("Failed to log Langfuse score", exc_info=True)
            return False

    def update_trace(
        self,
        trace_id: str,
        output: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Update a trace with final output or additional metadata.

        Uses the root SDK span's ``update_trace()`` method to push changes
        to the server (not just a local dict merge).
        """
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
            logger.warning("Failed to update Langfuse trace", exc_info=True)
            return False

    def end_trace(self, trace_id: str) -> None:
        """End the root span for a trace (marks the trace as complete)."""
        if not self.enabled or not self.client or not trace_id:
            return

        with graceful("Failed to end Langfuse trace"):
            root = self._trace_metadata.get(trace_id)
            if root:
                root.end()

    # ------------------------------------------------------------------
    # Dataset API
    # ------------------------------------------------------------------

    def create_dataset(
        self,
        name: str,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Create a Langfuse dataset (idempotent — no error if it exists)."""
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
            logger.warning("Failed to create Langfuse dataset", exc_info=True)
            return False

    def create_dataset_item(
        self,
        dataset_name: str,
        input: Any,
        expected_output: Any = None,
        metadata: dict[str, Any] | None = None,
        _max_retries: int = 3,
    ) -> str | None:
        """Create a dataset item. Returns item ID or None.

        Retries with exponential backoff on 429 rate-limit errors.
        """
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
        """Fetch a dataset by name. Returns SDK dataset object or None."""
        if not self.enabled or not self.client:
            return None
        try:
            return self.client.get_dataset(name=name)
        except Exception:
            logger.warning("Failed to get Langfuse dataset", exc_info=True)
            return None

    def update_dataset_item(
        self,
        item_id: str,
        expected_output: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Update an existing dataset item (e.g. to set expectedOutput)."""
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
            logger.warning("Failed to update Langfuse dataset item", exc_info=True)
            return False

    @property
    def rate_limited(self) -> bool:
        """True if we're currently blocked by a Langfuse rate limit."""
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
        """Link a trace/observation to a dataset item via a Dataset Run.

        Uses the REST API directly because the Python SDK does not expose
        ``create_dataset_run_item`` (it only offers a context-manager approach
        via ``item.run()`` which is unsuitable for backfill).

        Respects 429 rate limits: retries with exponential backoff for short
        waits, but if ``Retry-After`` exceeds 300s (daily quota exhausted),
        sets ``rate_limited`` and returns False immediately so callers can
        stop early.
        """
        if not self.enabled or not self.client:
            return False
        if self.rate_limited:
            return False
        try:
            from promptpotter.config.settings import settings

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
        """Ensure all pending events are sent to Langfuse."""
        if self.enabled and self.client:
            with graceful("Failed to flush Langfuse events"):
                self.client.flush()
