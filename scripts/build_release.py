"""Build the release wheel: stage the two derived asset trees, then ``uv build``.

Most of what the package reads at runtime is committed inside ``promptpotter/`` and
ships because ``pyproject.toml`` names it. Two things cannot work that way, because
they are **build artifacts**, not sources:

* **the dashboard** — ``webapp/out``, ~1.7 MB of Next.js export that exists only
  after ``npm run build`` in ``webapp/``;
* **the benchmark datasets** — ``datasets/``, where each dataset's ``cache.json``
  is gitignored and regenerable (~6.8 MB of HuggingFace rows) while the ~270 KB of
  definitions beside it is exactly what a fresh install needs to have something to
  run.

``pyproject.toml`` declares both under ``package-data``. This script is what makes
those declarations true. Skip it and the globs match nothing **quietly**: the mount
in ``main.py`` guards on ``.exists()``, so the wheel serves an API with no dashboard
and resolves no dataset, and neither failure says anything out loud.

Two rules the staging follows, both to avoid a second copy of a rule that already
exists somewhere else:

* **datasets are selected by ``git ls-files``**, not by a hand-written exclude list.
  The cache/definition split is already encoded in ``.gitignore`` (``datasets/*/
  cache.json``, minus the hand-authored ``email-tagging`` demo rows); re-stating it
  here would give it a second home to drift from.
* **a missing ``webapp/out`` is a hard error.** It is the one input this script
  cannot derive, and shipping without it is precisely the silent degradation above.
  Build the webapp first, or say ``--no-webapp`` and mean it.

Staging targets are gitignored and cleared on every run — and so are ``build/``,
``dist/`` and the egg-info, because clearing the staging tree alone does not make this
script idempotent (``_clear_build_state`` says why, and it was measured, not assumed).
A stale file from a previous release cannot ride along.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

_REPO = Path(__file__).resolve().parents[1]
_ASSETS = _REPO / "promptpotter" / "assets"
_WEBAPP_SRC = _REPO / "webapp" / "out"
_WEBAPP_DST = _ASSETS / "webapp"
_DATASETS_DST = _ASSETS / "benchmarks"


def _clear(dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)


def stage_webapp() -> int:
    """Copy the exported dashboard into the package. Returns the file count."""
    _clear(_WEBAPP_DST)
    shutil.copytree(_WEBAPP_SRC, _WEBAPP_DST)
    return sum(1 for p in _WEBAPP_DST.rglob("*") if p.is_file())


def stage_datasets() -> int:
    """Copy the *tracked* dataset files into the package. Returns the file count.

    Tracked-only is the whole point: it ships the definitions (``campaign.yaml``,
    ``pipeline.yaml``, ``prompts/``, ``task_description.md``, the sweep payloads)
    and leaves the regenerable HuggingFace caches behind. A benchmark whose cache
    is absent is already a handled case — ``application/datasets/loaders.py::
    resolve_dataset_items`` fetches and re-persists it on first use, the same path
    a fresh clone takes.
    """
    listing = subprocess.run(
        ["git", "ls-files", "-z", "datasets/"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    rel_paths = [p for p in listing.stdout.split("\0") if p]
    _clear(_DATASETS_DST)
    for rel in rel_paths:
        src = _REPO / rel
        if not src.is_file():  # tracked but deleted in the working tree
            continue
        # `datasets/CLAUDE.md` is a CONTRACT for whoever edits this repo, not dataset content.
        # Copied in, it becomes a second on-disk `CLAUDE.md` stating the same rules — and one
        # that no git-based tool can see, because the staged tree is gitignored, while every
        # tool an agent actually uses (Glob/Grep/Read) finds it immediately. Its links are
        # repo-relative, so from `promptpotter/assets/benchmarks/` all ten resolve into
        # directories that do not exist. An installed user gains nothing; a reader gains a
        # duplicate of the one thing this repo most often gets wrong (one fact, many copies).
        if src.name == "CLAUDE.md":
            continue
        dst = _DATASETS_DST / Path(rel).relative_to("datasets")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return sum(1 for p in _DATASETS_DST.rglob("*") if p.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-webapp",
        action="store_true",
        help="ship without the dashboard (headless install); otherwise a missing "
        "webapp/out is an error",
    )
    parser.add_argument(
        "--stage-only",
        action="store_true",
        help="stage the asset trees but do not invoke `uv build`",
    )
    args = parser.parse_args()

    if args.no_webapp:
        _clear(_WEBAPP_DST)
        print("webapp    : SKIPPED (--no-webapp) — the wheel will serve the API only")
    elif not _WEBAPP_SRC.is_dir():
        print(
            f"error: {_WEBAPP_SRC.relative_to(_REPO)} does not exist.\n"
            "       Build it first:  cd webapp && npm ci && npm run build:deploy\n"
            "       Or pass --no-webapp to ship an API-only wheel deliberately.",
            file=sys.stderr,
        )
        return 1
    else:
        print(f"webapp    : {stage_webapp()} files -> {_WEBAPP_DST.relative_to(_REPO)}")

    print(f"benchmarks: {stage_datasets()} files -> {_DATASETS_DST.relative_to(_REPO)}")

    if args.stage_only:
        return 0
    _clear_build_state()
    code = subprocess.run(["uv", "build", "--wheel"], cwd=_REPO, check=False).returncode
    return code or _verify_wheel(expect_webapp=not args.no_webapp)


def _clear_build_state() -> None:
    """The clean room ``pyproject.toml`` describes, applied on every run.

    Clearing the staging targets is NOT enough to keep a deleted file out of the wheel,
    which is the opposite of what this script's docstring used to promise. setuptools
    copies sources into ``build/lib`` incrementally and never removes what vanished, and
    ``*.egg-info/SOURCES.txt`` caches the file manifest across builds — so a file dropped
    from ``webapp/out`` or untracked from ``datasets/`` keeps shipping until someone
    deletes those two by hand. Reproduced: a planted ``leaked.env`` was staged, caught,
    removed from the source, and shipped again on the very next build. That is the exact
    remedy ``_wheel_problem`` prints ("remove the file and rebuild"), so leaving it out
    made the scanner's own instruction wrong.

    ``dist/`` goes too, so the verifier reads the wheel THIS run produced. Without it the
    check picks the newest file in a directory nothing prunes, and a wheel rejected by an
    earlier run sits there until a later, passing run vouches for the directory it is
    still in — while ``uv publish`` / ``twine upload dist/*`` glob the directory, not the
    wheel we approved.
    """
    for path in (_REPO / "build", _REPO / "dist", *_REPO.glob("*.egg-info")):
        _clear(path)


# Credential shapes, checked against the built wheel. Deliberately high-signal: the
# benchmark datasets are English prose about secrets and keys, so a bare "secret" or
# "sk-" would cry wolf every build and be switched off within a week.
#
# Every prefix carries the charset and length that follow it, for the same reason. A bare
# ``AKIA`` is four bytes; scanned across 1.7 MB of minified Next.js it is a coin toss, and
# the build it fails offers no override — the message can only tell the operator to go and
# edit this list, which is how a check gets deleted instead of narrowed. Anchored, a hit is
# a credential. ``promptpotter/config/log_redaction.py`` keeps its own patterns for log
# lines; that one redacts and this one REFUSES, so they stay apart deliberately.
_SECRET_NAMES = (".env", ".pem", ".key", ".p12", ".pfx", "id_rsa", "id_ed25519", ".keystore")
_SECRET_PATTERNS: tuple[re.Pattern[bytes], ...] = (
    re.compile(rb"-----BEGIN [A-Z ]+-----"),  # any PEM private key / certificate block
    re.compile(rb"sk-or-v1-[a-f0-9]{16,}"),  # OpenRouter
    re.compile(rb"sk-ant-[A-Za-z0-9_-]{20,}"),  # Anthropic
    re.compile(rb"sk-proj-[A-Za-z0-9_-]{20,}"),  # OpenAI project keys
    re.compile(rb"gsk_[A-Za-z0-9]{20,}"),  # Groq
    re.compile(rb"ghp_[A-Za-z0-9]{36}"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),  # AWS access key id
    re.compile(rb"xoxb-[0-9]{9,}-[0-9A-Za-z-]{10,}"),  # Slack bot token
    # A .env line that actually carries a value; an empty `KEY=` in a sample file is not a leak.
    re.compile(rb"(OPENROUTER|ANTHROPIC|GROQ|OPENAI)_API_KEY=[^\s\"']{8,}"),
)


def _verify_wheel(*, expect_webapp: bool) -> int:
    """Re-open the wheel we just built and check the artifact, not the intent.

    **Did the trees we staged arrive?** Staging puts files on disk; whether they SHIP is
    decided separately, by ``pyproject.toml``'s ``package-data`` globs. That list is
    load-bearing (delete it in a clean room and the wheel drops to one non-.py file), so
    a typo in it silently drops a tree.

    **Is anything in there that must never be published?** Two of those globs are
    wildcards over trees this script stages — ``assets/benchmarks`` takes whatever
    ``git ls-files datasets/`` returns, ``assets/webapp`` whatever ``npm run build``
    emitted, and a Next.js build inlines ``NEXT_PUBLIC_*`` values into its bundle. Both
    are wide open by construction, and publishing to an index is irreversible. So the
    payload is scanned before the wheel is handed over, by name and by content.

    **A wheel that fails any of this is deleted, not reported.** ``dist/`` is what the
    publish commands glob, so leaving a rejected artifact there and returning 1 hands over
    exactly the thing the check exists to withhold — the operator has to notice the exit
    code, and nothing downstream does.
    """
    wheels = list((_REPO / "dist").glob("*.whl"))
    if len(wheels) != 1:
        found = ", ".join(sorted(w.name for w in wheels)) or "nothing"
        print(
            f"error: expected the one wheel this run built in dist/, found {found}. "
            "uv build reported success, so something else is writing there.",
            file=sys.stderr,
        )
        return 1
    wheel = wheels[0]
    problem, scanned = _wheel_problem(wheel, expect_webapp=expect_webapp)
    if problem is not None:
        wheel.unlink()
        print(f"error: {problem}\n\n{wheel.name} was DELETED; dist/ is empty.", file=sys.stderr)
        return 1

    print(
        f"verified  : {wheel.name} carries every required tree; "
        f"{scanned} payload files scanned, no credentials, no stray caches"
    )
    return 0


def _wheel_problem(wheel: Path, *, expect_webapp: bool) -> tuple[str | None, int]:
    """What disqualifies *wheel* from being published, and how many files were scanned.

    Returns the message rather than printing it, so the one caller can delete the artifact
    and report in the same breath — three earlier ``return 1`` sites each left it on disk.
    """
    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()

    # One probe per package-data glob, each naming a file that glob is the only way to ship.
    # A tree-level probe would pass on a glob that ships one of its three files.
    required = {
        "assets/benchmarks": "promptpotter/assets/benchmarks/",
        "assets/optimizer/*.yaml": "promptpotter/assets/optimizer/pipeline.yaml",
        "assets/optimizer/*.json": "promptpotter/assets/optimizer/resolved_schemas.json",
        "assets/optimizer/sets/*.yaml": "promptpotter/assets/optimizer/sets/self_optimizing.yaml",
    }
    if expect_webapp:
        required["assets/webapp"] = "promptpotter/assets/webapp/index.html"

    missing = [
        label for label, probe in required.items() if not any(n.startswith(probe) for n in names)
    ]
    if missing:
        return (
            f"{wheel.name} is missing {', '.join(missing)}. The files were staged, so "
            "this is a package-data glob in pyproject.toml that matches nothing.",
            0,
        )
    # Caches are 6.8 MB of regenerable rows and must never ride along; email-tagging's are
    # hand-authored demo samples and are the one deliberate exception (see .gitignore).
    leaked = [n for n in names if n.endswith("cache.json") and "email-tagging" not in n]
    if leaked:
        return f"{wheel.name} carries HuggingFace caches: {leaked}", 0

    # Our own sources are reviewed and version-controlled; the staged trees are not, and
    # ``assets/benchmarks`` ships whatever ``git ls-files datasets/`` returns — including a
    # ``.py`` helper the day a dataset grows one. So the exemption is for OUR modules, not
    # for the extension.
    payload = [
        n
        for n in names
        if n.startswith("promptpotter/") and (not n.endswith(".py") or "/assets/" in n)
    ]
    findings: list[str] = []
    with zipfile.ZipFile(wheel) as z:
        for name in payload:
            base = PurePosixPath(name).name
            if any(base == s or base.startswith(s) or base.endswith(s) for s in _SECRET_NAMES):
                findings.append(f"{name}  (credential-shaped filename)")
                continue
            blob = z.read(name)
            for pattern in _SECRET_PATTERNS:
                hit = pattern.search(blob)
                if hit:
                    findings.append(f"{name}  (matches {pattern.pattern.decode()})")
                    break
    if findings:
        return (
            f"{wheel.name} would PUBLISH what looks like a credential:\n  "
            + "\n  ".join(findings)
            + "\nPublishing to an index cannot be undone. Remove the file (or the value) "
            "and rebuild; if it is a false positive, narrow the pattern in build_release.py.",
            len(payload),
        )
    return None, len(payload)


if __name__ == "__main__":
    raise SystemExit(main())
