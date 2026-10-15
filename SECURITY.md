# Security

**Report a vulnerability privately** through GitHub: the repository's *Security* tab → *Report a vulnerability*. Private vulnerability reporting is switched on, so the report reaches the maintainer and nobody else. Please do not open a public issue for it.

**In scope:** the optimizer, its REST API and webapp, and the deployment under `deploy-linux/`. What matters most is anything that lets one account read another's data or spend past its ceiling, bypass sign-in, or run code on the host.

**Supported:** the latest release on PyPI and `main`.

**What to expect:** an acknowledgement within a week, and a fix or a stated decision before any public disclosure. The access model the code is held to is [`docs/operations/access-model.md`](docs/operations/access-model.md).
