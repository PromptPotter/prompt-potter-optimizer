from __future__ import annotations

import html
import json
from typing import TYPE_CHECKING

from promptpotter.application.views.render.phase import render_completion
from promptpotter.application.views.render.primitives import BOLD, RESET, YELLOW
from promptpotter.domain.phases import StopOutcome, stop_reason_outcome

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.results import CycleResult

__all__ = ["render_completion_html", "report_completion"]


def render_completion_html(result: CycleResult) -> str:
    if not result.result_prompt_fields:
        return ""
    prompt_json = html.escape(
        json.dumps(dict(result.result_prompt_fields), indent=2, ensure_ascii=False, default=str)
    )
    pp_json = html.escape(
        json.dumps(
            dict(result.result_pipeline_params or {}), indent=2, ensure_ascii=False, default=str
        )
    )
    return (
        "<div style='font-family:monospace'>"
        "<h3 style='margin:8px 0 4px'>Final Winner</h3>"
        "<details><summary>Prompt fields</summary>"
        f"<pre>{prompt_json}</pre></details>"
        "<details><summary>Pipeline params</summary>"
        f"<pre>{pp_json}</pre></details></div>"
    )


def _try_display_html(html_body: str) -> bool:
    if not html_body:
        return False
    try:
        from IPython import get_ipython
        from IPython.display import HTML, display
    except ImportError:
        return False
    if get_ipython() is None:  # type: ignore[no-untyped-call, unused-ignore]
        return False
    display(HTML(html_body))  # type: ignore[no-untyped-call, unused-ignore]
    return True


def report_completion(result: CycleResult, *, session: Session) -> None:
    # Pause only: a cycle refused or crashed at run init also holds no round, and its box says why.
    if not result.rounds and stop_reason_outcome(result.stop_reason) is StopOutcome.PAUSED:
        print(
            f"\n{YELLOW}{BOLD}[PAUSED]{RESET} Cycle ended before any rounds completed — "
            "resume with `python -m promptpotter resume`."
        )
        return
    print(
        render_completion(
            result,
            pipeline_schema=session.pipeline_schema,
            dataset_name=session.dataset_name,
            campaign_dir=(
                session.store.campaigns.campaign_root_dir(session.campaign_id)
                if session.campaign_id
                else None
            ),
        )
    )
    _try_display_html(render_completion_html(result))
