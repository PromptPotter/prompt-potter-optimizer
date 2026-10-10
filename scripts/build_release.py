"""Build the release wheel: stage the two derived asset trees, then ``uv build``.

``webapp/out`` and ``datasets/`` are build artifacts ``pyproject.toml`` declares as package-data:
unstaged, its globs match nothing QUIETLY and the wheel serves an API with no dashboard or dataset.
Datasets are selected by ``git ls-files``, so ``.gitignore`` stays the one cache/definition split.
A missing ``webapp/out`` is a hard error: build the webapp first, or say ``--no-webapp``.
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
_STAGED_BANNER = "<!-- generated from datasets/ by scripts/build_release.py — do not edit -->"


def _clear(dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)


def stage_webapp() -> int:
    """The `.js` keeps its `sourceMappingURL` comments, one silent 404 in DevTools: never "fixed" by shipping the maps."""
    _clear(_WEBAPP_DST)
    shutil.copytree(_WEBAPP_SRC, _WEBAPP_DST, ignore=shutil.ignore_patterns("*.map"))
    return sum(1 for p in _WEBAPP_DST.rglob("*") if p.is_file())


def stage_datasets() -> int:
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
        # A staged `CLAUDE.md` is found by Grep with links that resolve nowhere; a `.py` under `assets/` the complexity ledger refuses.
        if src.name == "CLAUDE.md" or src.suffix == ".py":
            continue
        dst = _DATASETS_DST / Path(rel).relative_to("datasets")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        # NOT applied to `task_description.md`: that file reaches the model, where a banner is prompt text.
        if dst.name == "dataset.md":
            dst.write_text(
                f"{_STAGED_BANNER}\n\n{src.read_text(encoding='utf-8')}", encoding="utf-8"
            )
    (_DATASETS_DST / "README.md").write_text(
        f"{_STAGED_BANNER}\n\n# Staged benchmark definitions\n\n"
        "Generated copies of the tracked files under `datasets/`, written by\n"
        "`scripts/build_release.py::stage_datasets` so the wheel ships them. **Edit the source, "
        "never this tree** — the next release build overwrites it whole.\n",
        encoding="utf-8",
    )
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
            "       Build it first:  cd webapp && npm ci && npm run build\n"
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
    """setuptools never removes a vanished source from `build/lib` or the egg-info manifest, and publish globs `dist/`."""
    for path in (_REPO / "build", _REPO / "dist", *_REPO.glob("*.egg-info")):
        _clear(path)


# High-signal on purpose: dataset prose and minified JS match any bare prefix, so each carries its charset and length.
_SECRET_NAMES = (".env", ".pem", ".key", ".p12", ".pfx", "id_rsa", "id_ed25519", ".keystore")
_SECRET_PATTERNS: tuple[re.Pattern[bytes], ...] = (
    re.compile(rb"-----BEGIN [A-Z ]+-----"),
    re.compile(rb"sk-or-v1-[a-f0-9]{16,}"),
    re.compile(rb"sk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(rb"sk-proj-[A-Za-z0-9_-]{20,}"),
    re.compile(rb"gsk_[A-Za-z0-9]{20,}"),
    re.compile(rb"ghp_[A-Za-z0-9]{36}"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"xoxb-[0-9]{9,}-[0-9A-Za-z-]{10,}"),
    re.compile(rb"(OPENROUTER|ANTHROPIC|GROQ|OPENAI)_API_KEY=[^\s\"']{8,}"),
)


def _verify_wheel(*, expect_webapp: bool) -> int:
    """A wheel that fails is DELETED, not just reported: `dist/` is what the publish commands glob."""
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
    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()

    # One probe per package-data glob: a tree-level probe passes on a glob that ships one of its three files.
    required = {
        "assets/benchmarks": "promptpotter/assets/benchmarks/",
        "assets/optimizers/*/*.yaml": "promptpotter/assets/optimizers/potter/pipeline.yaml",
        "assets/optimizers/*/*.json": "promptpotter/assets/optimizers/potter/resolved_schemas.json",
        "assets/checkin/*.yaml": "promptpotter/assets/checkin/pipeline.yaml",
        "assets/checkin/*.json": "promptpotter/assets/checkin/resolved_schemas.json",
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
    # email-tagging's caches are hand-authored demo samples, the one deliberate exception (see .gitignore).
    leaked = [n for n in names if n.endswith("cache.json") and "email-tagging" not in n]
    if leaked:
        return f"{wheel.name} carries HuggingFace caches: {leaked}", 0

    # `.py` is exempt only outside `assets/`: `assets/benchmarks` ships whatever `git ls-files datasets/` returns.
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
