"""
Web search node (mock implementation).

Performs web searches and returns structured results.
This is a placeholder implementation - add real providers later.
"""
from typing import Type, Dict, Any, List, Optional
from pydantic import BaseModel, Field

from .base import NodeBase


class WebSearchInput(BaseModel):
    """Input model for web search node."""

    query: str = Field(..., description="Search query")
    context: Optional[str] = Field(
        None,
        description="Additional context to append to query"
    )


class WebSearchResult(BaseModel):
    """Single search result."""

    title: str = Field(..., description="Result title")
    url: str = Field(..., description="Result URL")
    snippet: str = Field(..., description="Result snippet/description")
    content: Optional[str] = Field(
        None,
        description="Full page content (if scrape_content=true)"
    )


class WebSearchOutput(BaseModel):
    """Output model for web search node."""

    results: List[WebSearchResult] = Field(
        default_factory=list,
        description="List of search results"
    )
    query: str = Field(..., description="Query that was searched")
    total_results: int = Field(..., description="Number of results returned")
    search_provider: str = Field(..., description="Search provider used")


class WebSearchNode(NodeBase[WebSearchInput, WebSearchOutput]):
    """
    Execute web search (mock implementation).

    This is a placeholder that returns mock results.
    To add real search, implement providers in api/services/web_search.py

    Config options:
        max_results: Maximum results to return (default: 5)
        provider: Search provider (default: "mock")
            - "mock": Returns placeholder results
            - "brave": Brave Search API (future)
            - "searxng": Self-hosted SearxNG (future)
        scrape_content: Whether to fetch full page content (default: false)
    """

    @classmethod
    def get_input_model(cls) -> Type[WebSearchInput]:
        return WebSearchInput

    @classmethod
    def get_output_model(cls) -> Type[WebSearchOutput]:
        return WebSearchOutput

    def format_user_prompt(self, input_data: WebSearchInput) -> str:
        """Format input as a search query string."""
        if input_data.context:
            return f"{input_data.query} {input_data.context}"
        return input_data.query

    async def _execute(self, input_data: WebSearchInput) -> WebSearchOutput:
        max_results = self.config.get("max_results", 5)
        provider = self.config.get("provider", "mock")

        # Build search query with context
        search_query = input_data.query
        if input_data.context:
            search_query = f"{input_data.query} {input_data.context}"

        if provider == "mock":
            results = self._mock_search(search_query, max_results)
        else:
            # Future: add real search providers
            # from api.services.web_search import search_web
            # results = await search_web(search_query, max_results, provider)
            results = self._mock_search(search_query, max_results)

        return WebSearchOutput(
            results=results,
            query=input_data.query,
            total_results=len(results),
            search_provider=provider
        )

    def _mock_search(self, query: str, max_results: int) -> List[WebSearchResult]:
        """
        Generate mock search results for testing.

        Returns placeholder results based on the query.
        """
        mock_results = [
            WebSearchResult(
                title=f"Result {i+1} for: {query}",
                url=f"https://example.com/result-{i+1}",
                snippet=f"This is a mock search result snippet for '{query}'. "
                        f"Replace with real search provider for production use.",
                content=None
            )
            for i in range(min(max_results, 5))
        ]
        return mock_results
