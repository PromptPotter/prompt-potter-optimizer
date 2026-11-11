# Contributing

PromptPotter is built and maintained by one person, and outside changes are welcome.
This page only routes: the rules live in the documents it links. Everyone here follows the [code of conduct](CODE_OF_CONDUCT.md).

## Before you write code

- **A bug or a feature idea:** open an [issue](https://github.com/PromptPotter/prompt-potter-optimizer/issues) first. For anything larger than a fix, agreeing on the shape there saves a rewrite later.
- **Where things live:** the [developer guide](docs/README.md#developer--seams-no-single-file-shows) indexes the pages on how the engine is wired, and [architecture](docs/architecture.md) is the map every change is placed on.
- **How code is written here:** [conventions](docs/developer/conventions.md).

## Set up and check

```bash
git clone https://github.com/PromptPotter/prompt-potter-optimizer && cd prompt-potter-optimizer
python scripts/gate.py          # every check CI runs; the first run builds .venv from uv.lock
git config core.hooksPath .githooks
python scripts/gate.py --changed   # while editing
```

Every optimizer runs end to end at zero spend, so a change can be tried without a provider key: see [`offline-run.md`](docs/developer/offline-run.md).

## Sending a pull request

Fill in the [pull request template](.github/pull_request_template.md): a change that adds a new concept places it on architecture §0 and says what channel it rides, where its facts land and what derives its state; a change that adds none writes `N/A`. `scripts/gate.py` must pass.

## License

Contributions are accepted under the repository's [license](LICENSE).
