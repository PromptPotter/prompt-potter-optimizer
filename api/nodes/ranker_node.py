"""
Ranker node.

Ranks candidates using LLM-based scoring against a query.
"""
from typing import Type, Dict, Any, List, Optional
from pydantic import BaseModel, Field

from .base import NodeBase


class RankerInput(BaseModel):
    """Input model for ranker node."""

    query: str = Field(..., description="Query to rank candidates against")
    candidates: List[str] = Field(
        ...,
        min_length=1,
        description="List of candidate strings to rank"
    )
    context: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional context (e.g., entity profile, search results)"
    )


class RankedCandidate(BaseModel):
    """Single ranked candidate with score and reasoning."""

    candidate: str = Field(..., description="The candidate string")
    score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Relevance score (0.0 = no match, 1.0 = perfect match)"
    )
    reasoning: Optional[str] = Field(
        None,
        description="Explanation for the score"
    )


class RankerOutput(BaseModel):
    """Output model for ranker node."""

    ranked_candidates: List[RankedCandidate] = Field(
        default_factory=list,
        description="Candidates sorted by score (highest first)"
    )
    top_candidate: str = Field(..., description="Best matching candidate")
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score of top candidate"
    )


class RankerNode(NodeBase[RankerInput, RankerOutput]):
    """
    Rank candidates by relevance to a query using LLM.

    Uses an LLM to score each candidate's relevance to the query,
    optionally using additional context (e.g., entity profiles).

    Config options:
        model: LLM model to use (default: from settings)
        temperature: Sampling temperature (default: 0.0 for determinism)
        max_candidates: Maximum candidates to rank (default: 20)
        ranking_prompt: Custom ranking prompt template (optional)
    """

    DEFAULT_RANKING_PROMPT = """You are a relevance ranking expert. Score each candidate's relevance to the query.

Query: {query}

Candidates:
{candidates}

{context_section}

Return a JSON object with this exact structure:
{{
  "ranked_candidates": [
    {{"candidate": "exact candidate string", "score": 0.0-1.0, "reasoning": "brief explanation"}}
  ]
}}

Scoring guidelines:
- 1.0: Perfect match (exact or semantically identical)
- 0.8-0.9: Very strong match (minor differences only)
- 0.6-0.7: Good match (same concept, different phrasing)
- 0.4-0.5: Partial match (related but not equivalent)
- 0.1-0.3: Weak match (loosely related)
- 0.0: No match (unrelated)

Return candidates sorted from highest to lowest score."""

    @classmethod
    def get_input_model(cls) -> Type[RankerInput]:
        return RankerInput

    @classmethod
    def get_output_model(cls) -> Type[RankerOutput]:
        return RankerOutput

    def format_user_prompt(self, input_data: RankerInput) -> str:
        """Format input for logging."""
        candidates_str = "\n".join(f"- {c}" for c in input_data.candidates[:5])
        return f"Query: {input_data.query}\nCandidates ({len(input_data.candidates)}):\n{candidates_str}"

    async def _execute(self, input_data: RankerInput) -> RankerOutput:
        from api.services.llm_client import get_llm_client
        import json

        # Get config
        model = self.config.get("model")
        temperature = self.config.get("temperature", 0.0)
        max_candidates = self.config.get("max_candidates", 20)
        custom_prompt = self.config.get("ranking_prompt")

        # Limit candidates
        candidates = input_data.candidates[:max_candidates]

        # Build prompt
        candidates_str = "\n".join(f"- {c}" for c in candidates)

        context_section = ""
        if input_data.context:
            context_section = f"Additional context:\n{json.dumps(input_data.context, indent=2)}"

        prompt_template = custom_prompt or self.DEFAULT_RANKING_PROMPT
        prompt = prompt_template.format(
            query=input_data.query,
            candidates=candidates_str,
            context_section=context_section
        )

        # Call LLM
        client = get_llm_client()
        response = await client.chat(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=temperature,
            max_tokens=2000,
            output_format="json"
        )

        # Update metrics
        if self._last_metrics:
            self._last_metrics.input_tokens = response.usage.get("prompt_tokens")
            self._last_metrics.output_tokens = response.usage.get("completion_tokens")
            self._last_metrics.model = response.model

        # Parse response
        ranked_candidates = []
        if response.parsed and "ranked_candidates" in response.parsed:
            for item in response.parsed["ranked_candidates"]:
                ranked_candidates.append(RankedCandidate(
                    candidate=item.get("candidate", ""),
                    score=float(item.get("score", 0.0)),
                    reasoning=item.get("reasoning")
                ))

        # Sort by score (highest first)
        ranked_candidates.sort(key=lambda x: x.score, reverse=True)

        # Get top candidate
        if ranked_candidates:
            top = ranked_candidates[0]
        else:
            # Fallback if parsing failed
            top = RankedCandidate(
                candidate=candidates[0] if candidates else "",
                score=0.0,
                reasoning="Failed to parse LLM response"
            )

        return RankerOutput(
            ranked_candidates=ranked_candidates,
            top_candidate=top.candidate,
            confidence=top.score
        )
