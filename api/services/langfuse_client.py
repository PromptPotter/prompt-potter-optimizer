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
import uuid
from typing import Any

logger = logging.getLogger(__name__)


class LangfuseLogger:
    """Wrapper for Langfuse SDK with project isolation.

    Project isolation is automatic via API keys:
    - Each Langfuse project has unique LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY
    - Set these in .env to route data to the correct project

    Gracefully disables logging if credentials are missing.
    """

    _instance: "LangfuseLogger | None" = None

    def __init__(self):
        """Initialize Langfuse client from settings."""
        from api.config.settings import settings

        self.enabled = bool(
            settings.LANGFUSE_ENABLED
            and settings.LANGFUSE_SECRET_KEY
            and settings.LANGFUSE_PUBLIC_KEY
        )
        self.client = None
        # Maps trace_id → root SDK span object (not a plain dict)
        self._trace_metadata: dict[str, Any] = {}

        if self.enabled:
            try:
                os.environ["LANGFUSE_PUBLIC_KEY"] = settings.LANGFUSE_PUBLIC_KEY
                os.environ["LANGFUSE_SECRET_KEY"] = settings.LANGFUSE_SECRET_KEY
                os.environ["LANGFUSE_HOST"] = settings.LANGFUSE_HOST

                from langfuse import Langfuse
                self.client = Langfuse()
            except ImportError:
                logger.warning("langfuse package not installed — observability disabled")
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
            root = self.client.start_span(
                trace_context={"trace_id": trace_id},
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

    def create_generation(
        self,
        trace_id: str,
        name: str,
        model: str,
        input: Any,
        output: Any,
        usage: dict[str, int] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """Log an LLM generation nested under the root span.

        Returns observation_id or None if logging is disabled.
        """
        if not self.enabled or not self.client or not trace_id:
            return None

        try:
            usage_details = None
            if usage:
                usage_details = {
                    "input": usage.get("prompt_tokens", 0),
                    "output": usage.get("completion_tokens", 0),
                    "total": usage.get("total_tokens", 0),
                }

            root = self._trace_metadata.get(trace_id)
            if root:
                gen = root.start_generation(
                    name=name,
                    model=model,
                    input=input,
                    output=output,
                    usage_details=usage_details,
                    metadata=metadata or {},
                )
                gen.end()
                return getattr(gen, "id", uuid.uuid4().hex[:12])
            return None
        except Exception:
            logger.warning("Failed to log Langfuse generation", exc_info=True)
            return None

    def create_span(
        self,
        trace_id: str,
        name: str,
        input: Any,
        output: Any,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """Log a non-LLM span nested under the root span.

        Returns observation_id or None if logging is disabled.
        """
        if not self.enabled or not self.client or not trace_id:
            return None

        try:
            root = self._trace_metadata.get(trace_id)
            if root:
                child = root.start_span(
                    name=name,
                    input=input,
                    output=output,
                    metadata=metadata or {},
                )
                child.end()
                return getattr(child, "id", uuid.uuid4().hex[:12])
            return None
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

        try:
            root = self._trace_metadata.get(trace_id)
            if root:
                root.end()
        except Exception:
            logger.warning("Failed to end Langfuse trace", exc_info=True)

    def flush(self) -> None:
        """Ensure all pending events are sent to Langfuse."""
        if self.enabled and self.client:
            try:
                self.client.flush()
            except Exception:
                logger.warning("Failed to flush Langfuse events", exc_info=True)

    def shutdown(self) -> None:
        """Gracefully shutdown the Langfuse client."""
        if self.enabled and self.client:
            try:
                self.client.shutdown()
            except Exception:
                logger.warning("Failed to shutdown Langfuse", exc_info=True)
