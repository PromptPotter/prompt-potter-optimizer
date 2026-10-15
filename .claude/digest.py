"""Hand a read to a cheap model, so the files never enter the session's context.

    python .claude/digest.py --extract --question "What does X report?" --paths a.html   # the lines themselves
    python .claude/digest.py --question "What does X do?" --paths a.py b.py              # a digest in bullets
    python .claude/digest.py --dry-run --question "..." --paths a.py                     # what would be sent
    python .claude/digest.py --note "enough | re-read anyway because ..."                # verdict on the last call
    python .claude/digest.py --ledger                                                    # every call so far
    python .claude/digest.py --bench [--system-file candidate.txt]                       # score the extract prompt

Two modes. `--extract` asks the worker only WHERE the answer is and then prints those lines from
disk, so nothing the session reads was written by the worker. Without it the worker answers in its
own words, citing `path:line`; that is a pointer, never a reading.

Markup is stripped line by line before sending, which keeps every line number equal to the file's.
One model, one shot, nothing kept by the worker. Every call appends a row to
`.claude/notes/digest-ledger.jsonl`; `--note` writes the verdict onto the newest row, which is the
experience this experiment exists to collect. A row also keeps the worker's answer and a copy of each
source under `.claude/notes/digest-sources/`, so it is a trace of the compaction task: source,
question, compaction, verdict. The last stdout line (`[digest] ...`) is the receipt
the session repeats in its reply.

The whole tool is one system prompt and one model. `--bench` scores a system prompt on the cases in
`.claude/digest-bench.jsonl` (did the quoted lines contain the answer, and how much came back), so
a candidate prompt is one `--system-file` away from a number — which is all an optimizer needs.
That number is span recall, the cheap screen; the measurement it stands in for is
`datasets/compaction/README.md` § Scoring.

The key is `DIGEST_OPENROUTER_API_KEY` (environment, else the repo's `.env`) — its own key, so this
spend never lands on a campaign's.
"""

import argparse
import hashlib
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
LEDGER = REPO / ".claude" / "notes" / "digest-ledger.jsonl"
BENCH = REPO / ".claude" / "digest-bench.jsonl"
SOURCES = REPO / ".claude" / "notes" / "digest-sources"
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


def kept_sources(files):
    """Each source as the worker saw it, stored once by content hash: a row stays a sample after the file is gone."""
    SOURCES.mkdir(parents=True, exist_ok=True)
    names = {}
    for name, lines in files.items():
        body = "\n".join(lines)
        names[name] = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
        target = SOURCES / f"{names[name]}.txt"
        if not target.exists():
            target.write_text(body, encoding="utf-8")
    return names


def rows():
    if not LEDGER.exists():
        return []
    return [
        json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def write(all_rows):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in all_rows), encoding="utf-8"
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


def bench(instructions):
    """Recall of each case's gold strings in the quoted lines; a case with none scores on returning nothing."""
    scores, returned, cost, started = [], [], 0.0, time.time()
    for line in BENCH.read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        files = load(case["paths"])
        reply = ask(instructions, case["question"], corpus(files))
        cost += reply.get("usage", {}).get("cost") or 0
        try:
            shown = quoted((reply["choices"][0]["message"].get("content") or "").strip(), files)
        except SystemExit:
            shown = ""
        quotes = [row for row in shown.splitlines() if "\t" in row]
        body = "".join("".join(row.split()) for row in quotes)
        gold = case["must_contain"]
        score = (
            sum("".join(g.split()) in body for g in gold) / len(gold) if gold else float(not body)
        )
        scores.append(score)
        returned.append(sum(len(row) for row in quotes) // 4)
        print(f"{score:4.2f}  {returned[-1]:>5} tok  {case['question'][:80]}")
    print(
        f"\n[bench] {MODEL}: recall {sum(scores) / len(scores):.2f} over {len(scores)} cases, "
        f"{sum(returned) // len(returned)} tokens returned on average, ${cost:.4f}, {round(time.time() - started)}s"
    )


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--question")
    parser.add_argument("--paths", nargs="+", default=[])
    parser.add_argument("--extract", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--note")
    parser.add_argument("--ledger", action="store_true")
    parser.add_argument("--bench", action="store_true")
    parser.add_argument("--system-file")
    args = parser.parse_args()

    override = Path(args.system_file).read_text(encoding="utf-8") if args.system_file else None
    if args.bench:
        bench(override or EXTRACT)
        return
    if args.ledger:
        for r in rows():
            print(
                f"{r['at']}  {r.get('mode', 'digest'):<7} {r['in_tokens']:>7} -> {r['out_tokens']:>5} tok  "
                f"${r['cost_usd'] or 0:.4f}  {r['seconds']:>3}s  {r['verdict'] or '(no verdict)'}  | {r['question'][:70]}"
            )
        return
    if args.note:
        all_rows = rows()
        if not all_rows:
            sys.exit("no digest call on the ledger to attach a verdict to")
        all_rows[-1]["verdict"] = args.note
        write(all_rows)
        print(f"verdict recorded on the {all_rows[-1]['at']} call")
        return
    if not args.question or not args.paths:
        parser.error("--question and --paths are both required")

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
    reply = ask(override or (EXTRACT if args.extract else DIGEST), args.question, text)
    seconds = round(time.time() - started)
    usage = reply.get("usage", {})
    choice = reply["choices"][0]
    answer = (choice["message"].get("content") or "").strip()
    if not answer:
        sys.exit(
            f"worker returned no answer (finish_reason={choice.get('finish_reason')}, usage={usage})"
        )
    shown = quoted(answer, files) if args.extract else answer
    row = {
        "at": time.strftime("%Y-%m-%d %H:%M"),
        "model": MODEL,
        "mode": mode,
        "question": args.question,
        "sources": kept_sources(files),
        "worker_answer": answer,
        "in_tokens": usage.get("prompt_tokens", 0),
        "out_tokens": usage.get("completion_tokens", 0),
        "shown_tokens": len(shown) // 4,
        "cost_usd": usage.get("cost"),
        "seconds": seconds,
        "verdict": None,
    }
    write([*rows(), row])
    cost = f"${row['cost_usd']:.4f}" if row["cost_usd"] is not None else "cost not reported"
    cut = (
        ", CUT OFF at the output cap: narrow the question"
        if choice.get("finish_reason") == "length"
        else ""
    )
    kind = "lines quoted from disk" if args.extract else "the worker's own words, unverified"
    print(shown)
    print(
        f"\n[digest] {mode}: {MODEL} read {len(files)} file(s), {row['in_tokens']} tokens in -> "
        f"~{row['shown_tokens']} returned ({kind}), {cost}, {seconds}s{cut}"
    )


if __name__ == "__main__":
    main()
