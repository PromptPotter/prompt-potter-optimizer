"""Hand a read to a cheap model, so the files never enter the session's context.

    python .claude/digest.py --extract --question "What does X report?" --paths a.html   # the lines themselves
    python .claude/digest.py --question "What does X do?" --paths a.py b.py              # a digest in bullets
    python .claude/digest.py --dry-run --question "..." --paths a.py                     # what would be sent

Two modes. `--extract` asks the worker only WHERE the answer is and then prints those lines from
disk, so nothing the session reads was written by the worker. Without it the worker answers in its
own words, citing `path:line`; that is a pointer, never a reading.

Markup is stripped line by line before sending, which keeps every line number equal to the file's.
The last stdout line (`[digest] ...`) is the receipt the session repeats in its reply.

The key is `DIGEST_OPENROUTER_API_KEY` (environment, else the repo's `.env`) — its own key, so this
spend never lands on a campaign's.
"""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

MODEL = "qwen/qwen3.8-flash"
KEY_NAME = "DIGEST_OPENROUTER_API_KEY"
REPO = Path(__file__).resolve().parent.parent
MAX_OUTPUT_TOKENS = 2000
MAX_EXTRACT_LINES = 120
MAX_LINE_CHARS = 1500
WITHHELD = (".env", "settings.json", "settings.local.json", ".mcp.json", "credentials")
MARKUP = {".html", ".htm"}
DIGEST = (
    "You are a precise analyst. Read the provided files and answer the question concisely. "
    "Output structured bullets only. No greetings, no prose, no preambles. Lead every bullet with "
    "the exact name, type, or `path:line` it rests on; each file is given with line numbers, so "
    "cite them. Use nested bullets for details. Skip anything the caller did not ask for. If the "
    "files do not answer the question, say so in one bullet."
)
EXTRACT = (
    "You locate text; you never answer. Each file is given with line numbers. Return ONLY a JSON "
    'object {"ranges": [{"path": "<path as given>", "start": <line>, "end": <line>, "why": "<five '
    'words>"}]} naming the smallest line ranges that together contain everything needed to answer '
    "the question. At most 12 ranges, each at most 25 lines. If the files do not contain the answer, "
    'return {"ranges": []}.'
)


def key():
    value = os.environ.get(KEY_NAME)
    if value:
        return value.strip()
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            name, _, rest = line.partition("=")
            if name.strip() == KEY_NAME and rest.strip():
                return rest.strip().strip("\"'")
    sys.exit(f"{KEY_NAME} is not set in the environment or in {env}")


def readable(path):
    """The file's lines, markup stripped in place so line N here is line N on disk."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if path.suffix.lower() not in MARKUP:
        return lines
    out, skipping = [], False
    for line in lines:
        if skipping:
            skipping = not re.search(r"</(script|style)>", line, re.I)
            out.append("")
            continue
        line = re.sub(r"<(script|style)\b.*?</\1>", " ", line, flags=re.I)
        skipping = bool(re.search(r"<(script|style)\b", line, re.I))
        text = "" if skipping else html.unescape(re.sub(r"<[^>]*>", " ", line))
        out.append(re.sub(r"\s+", " ", text).strip())
    return out


def load(paths):
    files = {}
    for raw in paths:
        path = Path(raw)
        if path.name.startswith(".env") or path.name in WITHHELD:
            sys.exit(f"refused: {raw} can hold secrets and is never sent to the worker")
        files[path.as_posix()] = readable(path)
    return files


def corpus(files):
    return "\n\n".join(
        f'<file path="{name}">\n'
        + "\n".join(f"{n}\t{line}" for n, line in enumerate(lines, 1) if line.strip())
        + "\n</file>"
        for name, lines in files.items()
    )


def ask(instructions, question, text):
    body = {
        "model": MODEL,
        "temperature": 0.2,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "reasoning": {"enabled": False},
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": f"{text}\n\n<question>\n{question}\n</question>"},
        ],
    }
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key()}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        sys.exit(
            f"worker call failed: HTTP {error.code} {error.read().decode('utf-8', 'replace')[:400]}"
        )


def quoted(answer, files):
    """The worker's ranges, printed from disk. Whatever it wrote besides line numbers is dropped."""
    found = re.search(r"[\[{].*[\]}]", answer, re.S)
    try:
        ranges = json.loads(found.group(0)) if found else None
    except json.JSONDecodeError:
        ranges = None
    if isinstance(ranges, dict):
        ranges = ranges.get("ranges")
    if not isinstance(ranges, list):
        sys.exit(f"worker did not return ranges: {answer[:300]}")
    if not ranges:
        return "The worker found nothing in these files that answers the question."
    out, budget = [], MAX_EXTRACT_LINES
    for item in ranges:
        lines = files.get(str(item.get("path")))
        if lines is None or budget <= 0:
            continue
        start = max(1, int(item.get("start", 1)))
        end = min(len(lines), int(item.get("end", start)), start + budget - 1)
        if end < start:
            continue
        out.append(f"--- {item['path']}:{start}-{end}  ({str(item.get('why', ''))[:60]})")
        out += [
            f"{n}\t{lines[n - 1][:MAX_LINE_CHARS]}"
            for n in range(start, end + 1)
            if lines[n - 1].strip()
        ]
        budget -= end - start + 1
    return "\n".join(out) if out else "The worker named no range that exists in these files."


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--question", required=True)
    parser.add_argument("--paths", nargs="+", required=True)
    parser.add_argument("--extract", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    files = load(args.paths)
    text = corpus(files)
    if args.dry_run:
        raw = sum(Path(p).stat().st_size for p in args.paths)
        print(
            f"would send {len(files)} file(s), ~{len(text) // 4} tokens (~{raw // 4} on disk), to {MODEL}"
        )
        return

    mode = "extract" if args.extract else "digest"
    started = time.time()
    reply = ask(EXTRACT if args.extract else DIGEST, args.question, text)
    seconds = round(time.time() - started)
    usage = reply.get("usage", {})
    choice = reply["choices"][0]
    answer = (choice["message"].get("content") or "").strip()
    if not answer:
        sys.exit(
            f"worker returned no answer (finish_reason={choice.get('finish_reason')}, usage={usage})"
        )
    shown = quoted(answer, files) if args.extract else answer
    cost = f"${usage['cost']:.4f}" if usage.get("cost") is not None else "cost not reported"
    cut = (
        ", CUT OFF at the output cap: narrow the question"
        if choice.get("finish_reason") == "length"
        else ""
    )
    kind = "lines quoted from disk" if args.extract else "the worker's own words, unverified"
    print(shown)
    print(
        f"\n[digest] {mode}: {MODEL} read {len(files)} file(s), {usage.get('prompt_tokens', 0)} tokens in -> "
        f"~{len(shown) // 4} returned ({kind}), {cost}, {seconds}s{cut}"
    )


if __name__ == "__main__":
    main()
