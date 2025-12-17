"""
Base class for workflow nodes using Pydantic generics.

Pattern inspired by AgentNodeBase[TInput, TOutput] from query-preprocessing-workflow.
Each node has strongly-typed input/output models and a consistent execution interface.
"""
from abc import ABC, abstractmethod
from typing import TypeVar, Generic, Type, Dict, Any, Optional
from pydantic import BaseModel, Field
from datetime import datetime
import time


TInput = TypeVar("TInput", bound=BaseModel)
TOutput = TypeVar("TOutput", bound=BaseModel)


class NodeMetrics(BaseModel):
    """Metrics captured during node execution."""

    node_id: str = Field(..., description="Unique identifier for this node instance")
    node_type: str = Field(..., description="Node class name")
    start_time: str = Field(..., description="ISO timestamp of execution start")
    end_time: str = Field(..., description="ISO timestamp of execution end")
    duration_ms: float = Field(..., description="Execution duration in milliseconds")
    input_tokens: Optional[int] = Field(None, description="Input tokens (for LLM nodes)")
    output_tokens: Optional[int] = Field(None, description="Output tokens (for LLM nodes)")
    model: Optional[str] = Field(None, description="Model used (for LLM nodes)")
    error: Optional[str] = Field(None, description="Error message if execution failed")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class NodeBase(ABC, Generic[TInput, TOutput]):
    """
    Abstract base class for workflow nodes.

    Implements the Template Method pattern with Pydantic generics:
    - Subclasses define input/output models and execution logic
    - Base class handles validation, metrics, and error handling

    Usage:
        class MyNode(NodeBase[MyInput, MyOutput]):
            @classmethod
            def get_input_model(cls) -> Type[MyInput]:
                return MyInput

            @classmethod
            def get_output_model(cls) -> Type[MyOutput]:
                return MyOutput

            async def _execute(self, input_data: MyInput) -> MyOutput:
                # Your logic here
                return MyOutput(...)
    """

    def __init__(self, node_id: str, config: Optional[Dict[str, Any]] = None):
        """
        Initialize a node instance.

        Args:
            node_id: Unique identifier for this node instance in the workflow
            config: Node-specific configuration (e.g., model, temperature, etc.)
        """
        self.node_id = node_id
        self.config = config or {}
        self._last_metrics: Optional[NodeMetrics] = None

    @classmethod
    @abstractmethod
    def get_input_model(cls) -> Type[TInput]:
        """Return the Pydantic model class for input validation."""
        pass

    @classmethod
    @abstractmethod
    def get_output_model(cls) -> Type[TOutput]:
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

    async def process(self, input_data: TInput | Dict[str, Any]) -> TOutput:
        """
        Main entry point for node execution.

        Handles:
        - Input validation via Pydantic model
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
        start_time = datetime.utcnow().isoformat() + "Z"
        error_msg = None

        try:
            # Validate input
            input_model = self.get_input_model()
            if isinstance(input_data, dict):
                validated_input = input_model.model_validate(input_data)
            else:
                validated_input = input_model.model_validate(input_data.model_dump())

            # Execute node logic
            result = await self._execute(validated_input)

            # Validate output
            output_model = self.get_output_model()
            if isinstance(result, dict):
                validated_output = output_model.model_validate(result)
            else:
                validated_output = output_model.model_validate(result.model_dump())

            return validated_output

        except Exception as e:
            error_msg = str(e)
            raise

        finally:
            end_time = datetime.utcnow().isoformat() + "Z"
            duration_ms = (time.time() - start) * 1000

            self._last_metrics = NodeMetrics(
                node_id=self.node_id,
                node_type=self.__class__.__name__,
                start_time=start_time,
                end_time=end_time,
                duration_ms=duration_ms,
                error=error_msg,
                metadata=self.config.copy()
            )

    def get_last_metrics(self) -> Optional[NodeMetrics]:
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
    def get_node_info(cls) -> Dict[str, Any]:
        """
        Return metadata about this node type for documentation/discovery.
        """
        return {
            "type": cls.get_node_type(),
            "input_model": cls.get_input_model().__name__ if hasattr(cls, 'get_input_model') else None,
            "output_model": cls.get_output_model().__name__ if hasattr(cls, 'get_output_model') else None,
            "doc": cls.__doc__
        }
