# LongSeal task templates

The layers a `harbor_tasks` source lays into each task directory, in the order its `template:` lists
them (`application/datasets/harbor_panel.py`). Every file is formatted over the row: `{question}`,
`{documents}` (the haystack rendered whole), and, in a file named `{n}`, one copy per document with
`{n}` its two-digit number and `{document}` its page. This README sits above the layers and is never
laid. A task's files are what its image hashes, so an edit here re-pins every task built from them.

- **`base/environment/Dockerfile` installs nothing that fetches**: where no network policy holds, the
  toolless image is the only seal, and a fetch tool would hand the agent a way around the haystack.
- **`base/solution/solve.sh` is no oracle**: the gold is held in `panel.yaml`, outside the container.
- **`task.toml` sets no `network_mode`**: its egress sidecar needs `CONFIG_NFT_FIB_INET`, which
  Docker Desktop lacks. The two-step file names `multi_step_reward_strategy` rather than inheriting
  it, and runs each step's verifier in a `separate` environment, so gold document numbers never sit
  in the agent's container through the answer step.
- **`test.sh` grades presence, never correctness** (`env_reward` is instrument health, not the score;
  a gold in `tests/` is readable by a step), and echoes because `connectors/harbor.py` tails it into
  the optimizer's view and warns on an empty tail. The answer lands at `/logs/artifacts/answer.txt`,
  which `connectors/harbor.py::ANSWER_FILENAME` names; the selection at `/logs/artifacts/retrieved.txt`.
