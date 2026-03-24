from .pipeline_schema import (
    ObservationMapping as ObservationMapping,
    PipelineNode as PipelineNode,
    PipelineSchema as PipelineSchema,
    PipelineStep as PipelineStep,  # backward compat alias → PipelineNode
)
from .opt_search_point import (
    FewShotExample as FewShotExample,
    OptSearchPoint as OptSearchPoint,
    PROMPT_STRING_FIELDS as PROMPT_STRING_FIELDS,
)
from .phase_event import PhaseEvent as PhaseEvent
from .search_point import SearchPoint as SearchPoint

# Legacy re-exports during migration — will be removed
from .prompt_state import (
    LAYER_FIELDS as LAYER_FIELDS,
    FieldChange as FieldChange,
    PromptState as PromptState,
    PromptStateDiff as PromptStateDiff,
    diff as diff,
)
