from __future__ import annotations

import html
from typing import TYPE_CHECKING

from promptpotter.application.views.render.export import winner_pointer
from promptpotter.application.views.render.phase import render_completion
from promptpotter.application.views.render.primitives import BOLD, RESET, YELLOW
from promptpotter.domain.phases import StopOutcome, stop_reason_outcome

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.results import CycleResult


def render_completion_html(result: CycleResult, hop: CycleHop) -> str:
    if not result.result_prompt_fields:
        return ""
    return (
        "<div style='font-family:monospace'>"
        "<h3 style='margin:8px 0 4px'>Winner</h3>"
        f"<p>{html.escape(winner_pointer(hop))}</p></div>"
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
    _try_display_html(render_completion_html(result, session.hop))
