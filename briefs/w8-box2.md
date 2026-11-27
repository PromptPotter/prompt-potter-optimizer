# w8-box2: w8-box's three links, and library sends get explicit keys (cloud · Opus)

`briefs/common.md` and `briefs/w8-shared.md` bind you.

START: cherry-pick `origin/arc/w8-box`'s three commits (b365cdb, 2324b68, b483d68) onto
`origin/release/next`, and resolve them on today's tree. The orchestrator held them back at landing,
because link 1 (the checkout `.env` is the one source, and no unit has `EnvironmentFile`) leaves the
process environment without the provider keys. Two sends read their key from that environment:
- `connectors/dspy_module.py:92` builds `dspy.LM(**lm_kwargs)` with no `api_key`, so litellm reads
  the environment;
- `connectors/harbor.py::_installed_run`'s agent sends from its container on the environment's key.
Both are admitted `paid_by=ENV_KEY_PAYER`.

ROOT (DECIDED): provider selection is always explicit (`infrastructure/llm/CLAUDE.md`). Every send
carries its key from the wallet's resolution, the account's own key, else the box's
(`wallet.py::keyed_client`'s rule), so no send reads `os.environ`:
- `dspy.LM` takes `api_key=` (and `api_base` where the provider needs it);
- harbor passes the agent's key into its container's environment explicitly;
- `ENV_KEY_PAYER` goes, if nothing is left that pays on the environment's key.

This changes who pays a DSPy or harbor send (the account's key, where it has one), which
decisions § 0003's one-payer rule asks for. Name it. Re-test: `git grep -n "ENV_KEY_PAYER" --
promptpotter` is empty, and an offline DSPy cell's LM carries the wallet's key.

**A resume's wait is unattributed** is w8-launch3's.

OWNS: `deploy-linux/`, `.claude/skills/potter-box/SKILL.md`, `promptpotter/__init__.py`,
`connectors/dspy_module.py`, `connectors/harbor.py`, `infrastructure/llm/wallet.py`, and their tests.
`application/maintenance/landing.py` is a re-point only (box's two lines).

LEAN: `promptpotter/` shrinks. Equals-main: legs unmoved (the offline world pays nothing). Digests
and behaviour keys unchanged. Box: list every operator step under "Box:" in the commit body
(w8-box's list, plus a check that a DSPy cell sends on the box after the env move).
