"""Artifact manifest — canonical set of session-level campaign files.

Every entry point must produce all files listed here. The parity test
(``tests/test_artifact_parity.py``) asserts their existence after a
1-round optimization run.
"""

CAMPAIGN_SESSION_ARTIFACTS = {
    "campaign_state.json",
    "campaign_output.log",
    "campaign_log.md",
    "session.json",
}
