# Contributing

PromptPotter is built and maintained by one person, and outside changes are welcome.
This page only routes: the rules live in the documents it links. Everyone here follows the [code of conduct](CODE_OF_CONDUCT.md).

## Before you write code

- **A bug or a feature idea:** open an [issue](https://github.com/PromptPotter/prompt-potter-optimizer/issues) first. For anything larger than a fix, agreeing on the shape there saves a rewrite later.
- **Where things live:** the [developer guide](docs/developer/README.md) draws the engine's four seams on one page, and [architecture](docs/architecture.md) is the map every change is placed on.
- **How code is written here:** [conventions](docs/developer/conventions.md).

## Set up and check

```bash
git clone https://github.com/PromptPotter/prompt-potter-optimizer && cd prompt-potter-optimizer
pip install -e ".[all,dev]"
python scripts/gate.py          # every check CI runs
```

Every optimizer runs end to end at zero spend, so a change can be tried without a provider key: see [`offline-run.md`](docs/developer/offline-run.md).

## Sending a pull request

The [pull request template](.github/pull_request_template.md) carries the pre-flight gate. A change that adds a new concept answers all eight questions; a change that adds none says so and skips them. `scripts/gate.py` must pass.

## License

Contributions are accepted under the repository's [license](LICENSE).
