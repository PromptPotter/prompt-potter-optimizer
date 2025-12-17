"""
Langfuse SDK wrapper for workflow observability.

Provides singleton access to Langfuse client with automatic project isolation
via API keys. Logs traces, observations (generations/spans), and scores.

Compatible with Langfuse SDK v3 (OpenTelemetry-based).

Usage:
    langfuse = LangfuseLogger.get_instance()
    trace_id = langfuse.create_trace("workflow_name", inputs)
    langfuse.create_generation(trace_id, "step_name", "gpt-4", input, output)
    langfuse.create_score(trace_id, "accuracy", 0.95)
    langfuse.flush()
"""

from typing import Optional, Dict, Any
from datetime import datetime
import uuid
import os


class LangfuseLogger:
    """
    Wrapper for Langfuse SDK v3 with project isolation.

    Project isolation is automatic via API keys:
    - Each Langfuse project has unique LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY
    - Set these in .env to route data to the correct project
    - Multiple applications on the same server won't mix data

    Gracefully disables logging if credentials are missing.
    """

    _instance: Optional["LangfuseLogger"] = None

    def __init__(self):
        """Initialize Langfuse client from settings."""
        from api.config.settings import settings

        self.enabled = (
            settings.LANGFUSE_ENABLED
            and settings.LANGFUSE_SECRET_KEY
            and settings.LANGFUSE_PUBLIC_KEY
        )
        self.client = None
        self._traces: Dict[str, Any] = {}  # Store trace references

        if self.enabled:
            try:
                # Set environment variables for Langfuse SDK v3
                os.environ["LANGFUSE_PUBLIC_KEY"] = settings.LANGFUSE_PUBLIC_KEY
                os.environ["LANGFUSE_SECRET_KEY"] = settings.LANGFUSE_SECRET_KEY
                os.environ["LANGFUSE_HOST"] = settings.LANGFUSE_HOST

                from langfuse import Langfuse

                self.client = Langfuse()
            except ImportError:
                print("Warning: langfuse package not installed. Observability disabled.")
                self.enabled = False
            except Exception as e:
                print(f"Warning: Failed to initialize Langfuse: {e}")
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
        input: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        tags: Optional[list] = None,
    ) -> Optional[str]:
        """
        Create a new trace for a workflow execution (Langfuse v3 API).

        Args:
            name: Workflow name/identifier
            input: Workflow input data
            metadata: Additional metadata (workflow_label, etc.)
            user_id: Optional user identifier
            session_id: Optional session identifier
            tags: Optional list of tags

        Returns:
            trace_id or None if logging is disabled
        """
        if not self.enabled or not self.client:
            return None

        try:
            # v3 API: create_trace_id() generates a trace ID
            trace_id = self.client.create_trace_id()

            # Store trace metadata for later use
            self._traces[trace_id] = {
                "name": name,
                "input": input,
                "metadata": metadata or {},
                "user_id": user_id,
                "session_id": session_id,
                "tags": tags or [],
            }
            return trace_id
        except Exception as e:
            print(f"Warning: Failed to create Langfuse trace: {e}")
            return None

    def create_generation(
        self,
        trace_id: str,
        name: str,
        model: str,
        input: Any,
        output: Any,
        usage: Optional[Dict[str, int]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Log an LLM generation (Langfuse v3 API).

        Args:
            trace_id: Parent trace ID
            name: Step/node name
            model: Model identifier (e.g., "gpt-4")
            input: Generation input (prompt, messages, etc.)
            output: Generation output (response content)
            usage: Token usage dict with prompt_tokens, completion_tokens, total_tokens
            metadata: Additional metadata

        Returns:
            observation_id or None if logging is disabled
        """
        if not self.enabled or not self.client or not trace_id:
            return None

        try:
            # Convert usage format for Langfuse v3 (usage_details)
            usage_details = None
            if usage:
                usage_details = {
                    "input": usage.get("prompt_tokens", 0),
                    "output": usage.get("completion_tokens", 0),
                    "total": usage.get("total_tokens", 0),
                }

            # v3 API: trace_context is a dict with trace_id
            trace_context = {"trace_id": trace_id}

            generation = self.client.start_generation(
                trace_context=trace_context,
                name=name,
                model=model,
                input=input,
                output=output,
                usage_details=usage_details,
                metadata=metadata or {},
            )
            # End the generation immediately since we have output
            if hasattr(generation, 'end'):
                generation.end()
            return getattr(generation, 'id', str(uuid.uuid4().hex[:12]))
        except Exception as e:
            print(f"Warning: Failed to log Langfuse generation: {e}")
            return None

    def create_span(
        self,
        trace_id: str,
        name: str,
        input: Any,
        output: Any,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Log a non-LLM node execution (Langfuse v3 API).

        Args:
            trace_id: Parent trace ID
            name: Step/node name
            input: Span input data
            output: Span output data
            metadata: Additional metadata

        Returns:
            observation_id or None if logging is disabled
        """
        if not self.enabled or not self.client or not trace_id:
            return None

        try:
            # v3 API: trace_context is a dict with trace_id
            trace_context = {"trace_id": trace_id}

            span = self.client.start_span(
                trace_context=trace_context,
                name=name,
                input=input,
                output=output,
                metadata=metadata or {},
            )
            # End the span immediately since we have output
            if hasattr(span, 'end'):
                span.end()
            return getattr(span, 'id', str(uuid.uuid4().hex[:12]))
        except Exception as e:
            print(f"Warning: Failed to log Langfuse span: {e}")
            return None

    def create_score(
        self,
        trace_id: str,
        name: str,
        value: float,
        data_type: str = "NUMERIC",
        comment: Optional[str] = None,
    ) -> bool:
        """
        Log an evaluation score for a trace.

        Args:
            trace_id: Trace to score
            name: Score name (e.g., "accuracy", "exact_match")
            value: Score value (0.0 to 1.0 for NUMERIC)
            data_type: "NUMERIC" (default), "BOOLEAN", or "CATEGORICAL"
            comment: Optional explanation

        Returns:
            True if successful, False otherwise
        """
        if not self.enabled or not self.client or not trace_id:
            return False

        try:
            self.client.score(
                trace_id=trace_id,
                name=name,
                value=value,
                data_type=data_type,
                comment=comment,
            )
            return True
        except Exception as e:
            print(f"Warning: Failed to log Langfuse score: {e}")
            return False

    def update_trace(
        self,
        trace_id: str,
        output: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Update a trace with final output or additional metadata.

        Args:
            trace_id: Trace to update
            output: Final workflow output
            metadata: Additional metadata to merge

        Returns:
            True if successful, False otherwise
        """
        if not self.enabled or not self.client or not trace_id:
            return False

        try:
            # Get stored trace reference or create new one
            trace = self._traces.get(trace_id)
            if trace and hasattr(trace, 'update'):
                update_kwargs = {}
                if output is not None:
                    update_kwargs["output"] = output
                if metadata is not None:
                    update_kwargs["metadata"] = metadata
                if update_kwargs:
                    trace.update(**update_kwargs)
            return True
        except Exception as e:
            print(f"Warning: Failed to update Langfuse trace: {e}")
            return False

    def flush(self) -> None:
        """
        Ensure all pending events are sent to Langfuse.

        Call this at the end of a request or workflow execution.
        """
        if self.enabled and self.client:
            try:
                self.client.flush()
            except Exception as e:
                print(f"Warning: Failed to flush Langfuse events: {e}")

    def shutdown(self) -> None:
        """
        Gracefully shutdown the Langfuse client.

        Call this on application shutdown.
        """
        if self.enabled and self.client:
            try:
                self.client.shutdown()
            except Exception as e:
                print(f"Warning: Failed to shutdown Langfuse: {e}")
