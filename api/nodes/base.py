"""
Base class for workflow nodes using Pydantic generics.

Pattern inspired by AgentNodeBase[TInput, TOutput] from query-preprocessing-workflow.
Each node has strongly-typed input/output models and a consistent execution interface.

Observability is opt-in: pass ``obs`` + ``trace_id`` at construction to enable
automatic step tracing (file + Langfuse cloud). When omitted, tracing is silently
skipped and the node works identically.
"""
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, TypeVar, Generic
from pydantic import BaseModel, Field
from datetime import datetime, timezone
import logging
import time


if TYPE_CHECKING:
    from api.services.obs.observability_logger import ObsLogger

logger = logging.getLogger(__name__)

TInput = TypeVar("TInput", bound=BaseModel)
TOutput = TypeVar("TOutput", bound=BaseModel)


class NodeMetrics(BaseModel):
    """Metrics captured during node execution."""

    node_id: str = Field(..., description="Unique identifier for this node instance")
    node_type: str = Field(..., description="Node class name")
    start_time: str = Field(..., description="ISO timestamp of execution start")
    end_time: str = Field(..., description="ISO timestamp of execution end")
    duration_ms: float = Field(..., description="Execution duration in milliseconds")
    input_tokens: int | None = Field(None, description="Input tokens (for LLM nodes)")
    output_tokens: int | None = Field(None, description="Output tokens (for LLM nodes)")
    model: str | None = Field(None, description="Model used (for LLM nodes)")
    error: str | None = Field(None, description="Error message if execution failed")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class NodeBase(ABC, Generic[TInput, TOutput]):
    """
    Abstract base class for workflow nodes.

    Implements the Template Method pattern with Pydantic generics:
    - Subclasses define input/output models and execution logic
    - Base class handles validation, metrics, and error handling

    Usage:
        class MyNode(NodeBase[MyInput, MyOutput]):
            @classmethod
            def get_input_model(cls) -> type[MyInput]:
                return MyInput

            @classmethod
            def get_output_model(cls) -> type[MyOutput]:
                return MyOutput

            async def _execute(self, input_data: MyInput) -> MyOutput:
                # Your logic here
                return MyOutput(...)
    """

    def __init__(
        self,
        node_id: str,
        config: dict[str, Any] | None = None,
        *,
        obs: "ObsLogger | None" = None,
        trace_id: str | None = None,
    ):
        """
        Initialize a node instance.

        Args:
            node_id: Unique identifier for this node instance in the workflow
            config: Node-specific configuration (e.g., model, temperature, etc.)
            obs: Optional ObsLogger for step-level tracing (file + Langfuse)
            trace_id: Campaign trace ID for nesting observations
        """
        self.node_id = node_id
        self.config = config or {}
        self.obs = obs
        self.trace_id = trace_id
        self._last_metrics: NodeMetrics | None = None

    def _node_obs_type(self) -> str:
        """Langfuse observation type for this node.

        Override in subclasses: ``"generation"`` for single LLM calls,
        ``"span"`` for composite operations with nested children.
        """
        return "generation"

    def _start_observation(self, input_data: TInput) -> str | None:
        """Start a step observation if obs is configured. Returns obs_id."""
        if not self.obs or not self.trace_id:
            return None
        try:
            return self.obs.log_node_step_start(
                trace_id=self.trace_id,
                node_id=self.node_id,
                node_type=self.__class__.__name__,
                obs_type=self._node_obs_type(),
                input_data=input_data.model_dump(),
                metadata=self.config.copy(),
            )
        except Exception:
            logger.warning("NodeBase._start_observation failed", exc_info=True)
            return None

    def _end_observation(
        self,
        obs_id: str | None,
        output_data: dict | None,
        error: str | None,
    ) -> None:
        """Close a step observation with output and metrics."""
        if not obs_id or not self.obs:
            return
        try:
            self.obs.log_node_step_end(
                obs_id=obs_id,
                trace_id=self.trace_id or "",
                node_id=self.node_id,
                output_data=output_data,
                metrics=self._last_metrics.model_dump() if self._last_metrics else None,
                error=error,
            )
        except Exception:
            logger.warning("NodeBase._end_observation failed", exc_info=True)

    @classmethod
    @abstractmethod
    def get_input_model(cls) -> type[TInput]:
        """Return the Pydantic model class for input validation."""
        pass

    @classmethod
    @abstractmethod
    def get_output_model(cls) -> type[TOutput]:
        """Return the Pydantic model class for output validation."""
        pass

    def format_user_prompt(self, input_data: TInput) -> str:
        """
        Format input data into a user prompt string.

        Override for LLM nodes that need custom prompt formatting.
        Default implementation returns JSON representation.
        """
        return input_data.model_dump_json(indent=2)

    @abstractmethod
    async def _execute(self, input_data: TInput) -> TOutput:
        """
        Internal execution logic - override in subclasses.

        This method contains the actual node logic. It receives validated
        input and should return output matching the output model.

        Args:
            input_data: Validated input data

        Returns:
            Output data (will be validated against output model)
        """
        pass

    async def process(self, input_data: TInput | dict[str, Any]) -> TOutput:
        """
        Main entry point for node execution.

        Handles:
        - Input validation via Pydantic model
        - Step-level observability (file + Langfuse, opt-in)
        - Metrics collection (timing, tokens, etc.)
        - Error handling and reporting
        - Output validation

        Args:
            input_data: Input data (Pydantic model or dict)

        Returns:
            Validated output data

        Raises:
            ValidationError: If input/output doesn't match model
            Exception: Any exception from _execute is re-raised with metrics captured
        """
        start = time.time()
        start_time = datetime.now(timezone.utc).isoformat() + "Z"
        error_msg = None
        obs_id = None
        validated_output = None
        interrupted = False

        try:
            # Validate input
            input_model = self.get_input_model()
            if isinstance(input_data, dict):
                validated_input = input_model.model_validate(input_data)
            else:
                validated_input = input_model.model_validate(input_data.model_dump())

            # Start step observation (opt-in)
            obs_id = self._start_observation(validated_input)

            # Execute node logic
            result = await self._execute(validated_input)

            # Validate output
            output_model = self.get_output_model()
            if isinstance(result, dict):
                validated_output = output_model.model_validate(result)
            else:
                validated_output = output_model.model_validate(result.model_dump())

            return validated_output

        except KeyboardInterrupt:
            interrupted = True
            raise

        except Exception as e:
            error_msg = str(e)
            raise

        finally:
            # On KeyboardInterrupt: skip everything. The Windows asyncio
            # event loop is corrupted — any I/O (obs, Langfuse, even
            # Pydantic construction touching complex config) can hang.
            if not interrupted:
                end_time = datetime.now(timezone.utc).isoformat() + "Z"
                duration_ms = (time.time() - start) * 1000

                safe_meta = {
                    k: v for k, v in self.config.items()
                    if isinstance(v, (str, int, float, bool, list, type(None)))
                }

                self._last_metrics = NodeMetrics(
                    node_id=self.node_id,
                    node_type=self.__class__.__name__,
                    start_time=start_time,
                    end_time=end_time,
                    duration_ms=duration_ms,
                    error=error_msg,
                    metadata=safe_meta,
                )

                self._end_observation(
                    obs_id,
                    validated_output.model_dump() if validated_output else None,
                    error_msg,
                )

    def get_last_metrics(self) -> NodeMetrics | None:
        """Return metrics from the last execution."""
        return self._last_metrics

    def update_metrics(self, **kwargs) -> None:
        """
        Update metrics for the current execution.

        Call this from _execute to add token counts, model info, etc.
        """
        if self._last_metrics:
            for key, value in kwargs.items():
                if hasattr(self._last_metrics, key):
                    setattr(self._last_metrics, key, value)
                else:
                    self._last_metrics.metadata[key] = value

    @classmethod
    def get_node_type(cls) -> str:
        """
        Return the node type identifier for registration.

        Default is the class name. Override for custom type names.
        """
        return cls.__name__

    @classmethod
    def get_node_info(cls) -> dict[str, Any]:
        """
        Return metadata about this node type for documentation/discovery.
        """
        input_name = (
            cls.get_input_model().__name__
            if hasattr(cls, 'get_input_model') else None
        )
        output_name = (
            cls.get_output_model().__name__
            if hasattr(cls, 'get_output_model') else None
        )
        return {
            "type": cls.get_node_type(),
            "input_model": input_name,
            "output_model": output_name,
            "doc": cls.__doc__
        }
