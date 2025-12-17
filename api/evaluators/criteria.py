"""
Criteria evaluator (LLM-as-judge).

Uses an LLM to semantically evaluate whether the actual output
meets the criteria defined by the expected output.
"""
from typing import Any, Dict, Optional

from .base import EvaluatorBase, EvaluationOutput, EvalResult


class CriteriaEvaluator(EvaluatorBase):
    """
    LLM-based semantic evaluator.

    Uses an LLM to judge whether the actual output matches
    the expected output semantically, not just lexically.

    Config options:
        model: LLM model to use (default: from settings)
        temperature: Sampling temperature (default: 0.0)
        criteria_prompt: Custom evaluation prompt (optional)
        threshold: Score threshold for pass (default: 0.7)
    """

    DEFAULT_CRITERIA_PROMPT = """You are an evaluation expert. Compare the expected and actual outputs and determine if they are semantically equivalent.

Expected output:
{expected}

Actual output:
{actual}

Evaluate whether the actual output conveys the same meaning, information, or intent as the expected output.

Return a JSON object with this exact structure:
{{
  "match": true or false,
  "score": 0.0 to 1.0,
  "reasoning": "explanation of your evaluation"
}}

Scoring guidelines:
- 1.0: Semantically identical (same meaning, same information)
- 0.8-0.9: Very close match (minor phrasing differences)
- 0.6-0.7: Partial match (captures main points but missing details)
- 0.4-0.5: Weak match (some overlap but significant differences)
- 0.0-0.3: No match (different meaning or information)"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.model = self.config.get("model")
        self.temperature = self.config.get("temperature", 0.0)
        self.threshold = self.config.get("threshold", 0.7)
        self.criteria_prompt = self.config.get("criteria_prompt", self.DEFAULT_CRITERIA_PROMPT)

    def evaluate(self, expected: Any, actual: Any) -> EvaluationOutput:
        """
        Use LLM to evaluate semantic match.

        Note: This is a synchronous wrapper. For async usage,
        use evaluate_async directly.
        """
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Already in async context - create new loop
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    result = pool.submit(
                        asyncio.run, self.evaluate_async(expected, actual)
                    ).result()
                return result
            else:
                return loop.run_until_complete(self.evaluate_async(expected, actual))
        except RuntimeError:
            return asyncio.run(self.evaluate_async(expected, actual))

    async def evaluate_async(self, expected: Any, actual: Any) -> EvaluationOutput:
        """
        Asynchronously evaluate using LLM.

        Args:
            expected: Expected value
            actual: Actual value

        Returns:
            EvaluationOutput with LLM-based evaluation
        """
        from api.services.llm_client import get_llm_client
        import json

        # Handle None cases
        if expected is None and actual is None:
            return EvaluationOutput(
                result=EvalResult.PASS,
                score=1.0,
                expected=expected,
                actual=actual,
                reason="Both values are None"
            )

        # Convert to strings for LLM
        expected_str = json.dumps(expected, indent=2) if isinstance(expected, (dict, list)) else str(expected)
        actual_str = json.dumps(actual, indent=2) if isinstance(actual, (dict, list)) else str(actual)

        # Build prompt
        prompt = self.criteria_prompt.format(
            expected=expected_str,
            actual=actual_str
        )

        try:
            client = get_llm_client()
            response = await client.chat(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                temperature=self.temperature,
                max_tokens=500,
                output_format="json"
            )

            # Parse response
            if response.parsed:
                match = response.parsed.get("match", False)
                score = float(response.parsed.get("score", 0.0))
                reasoning = response.parsed.get("reasoning", "")

                # Apply threshold
                result = EvalResult.PASS if score >= self.threshold else EvalResult.FAIL

                return EvaluationOutput(
                    result=result,
                    score=score,
                    expected=expected,
                    actual=actual,
                    reason=reasoning,
                    metadata={
                        "llm_match": match,
                        "threshold": self.threshold,
                        "model": response.model
                    }
                )
            else:
                # Failed to parse JSON
                return EvaluationOutput(
                    result=EvalResult.ERROR,
                    score=0.0,
                    expected=expected,
                    actual=actual,
                    reason=f"Failed to parse LLM response: {response.content[:200]}"
                )

        except Exception as e:
            return EvaluationOutput(
                result=EvalResult.ERROR,
                score=0.0,
                expected=expected,
                actual=actual,
                reason=f"LLM evaluation failed: {str(e)}"
            )
