#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""CLI for searching WebKit source code (via the public Sourcegraph API)."""

from __future__ import annotations

import argparse
import contextlib
import itertools
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ENDPOINT = os.environ.get("SRC_ENDPOINT", "https://sourcegraph.com").rstrip("/")
REPO = os.environ.get("WEBKIT_SEARCH_REPO", "github.com/WebKit/WebKit")
TOKEN = os.environ.get("SRC_ACCESS_TOKEN", "")
USER_AGENT = "webkit-search/0.1"
TIMEOUT = 60.0


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    h = {"User-Agent": USER_AGENT}
    if TOKEN:
        h["Authorization"] = f"token {TOKEN}"
    if extra:
        h.update(extra)
    return h


def _open(req: urllib.request.Request) -> Any:
    try:
        return urllib.request.urlopen(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RuntimeError(
                "Rate limited by Sourcegraph; set SRC_ACCESS_TOKEN to raise limits"
            ) from exc
        raise


def repo_filter(ref: str | None) -> str:
    pat = "^" + re.escape(REPO) + "$"
    return f"repo:{pat}@{ref}" if ref else f"repo:{pat}"


def scoped_query(query: str, ref: str | None, count: int | None) -> str:
    parts = []
    if not re.search(r"(^|\s)-?(repo|r):", query):
        parts.append(repo_filter(ref))
    parts.append(query)
    # Sourcegraph's count: counts highlights, not results; over-fetch and trim.
    if count is not None and not re.search(r"(^|\s)count:", query):
        parts.append(f"count:{count * 3}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class MatchRange:
    start: int
    end: int


@dataclass
class SnippetLine:
    line_text: str
    line_number: int  # 1-based
    ranges: list[MatchRange] = field(default_factory=list)


@dataclass
class Snippet:
    lines: list[SnippetLine] = field(default_factory=list)


@dataclass
class FileResult:
    path: str
    commit: str = ""
    snippets: list[Snippet] = field(default_factory=list)


@dataclass
class Symbol:
    name: str
    kind: str
    container: str
    path: str
    line: int
    commit: str = ""


@dataclass
class StreamResult:
    matches: list[dict[str, Any]] = field(default_factory=list)
    match_count: int = 0
    alerts: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Sourcegraph streaming search client
# ---------------------------------------------------------------------------


def stream_search(query: str, *, display: int, context_lines: int) -> StreamResult:
    params = urllib.parse.urlencode(
        {
            "q": query,
            "v": "V3",
            "t": "keyword",
            "display": str(display),
            "cm": "t",
            "cl": str(context_lines),
        }
    )
    req = urllib.request.Request(
        f"{ENDPOINT}/.api/search/stream?{params}",
        headers=_headers({"Accept": "text/event-stream"}),
    )
    out = StreamResult()
    event = ""
    with _open(req) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip() or "null")
                if event == "matches":
                    out.matches.extend(data)
                elif event == "progress" and isinstance(data, dict):
                    out.match_count = data.get("matchCount", out.match_count)
                elif event == "alert" and isinstance(data, dict):
                    msg = data.get("title", "")
                    if data.get("description"):
                        msg += f": {data['description']}"
                    out.alerts.append(msg)
                elif event == "error":
                    msg = data.get("message") if isinstance(data, dict) else data
                    raise RuntimeError(f"Search error: {msg}")
                elif event == "done":
                    break
    return out


def parse_content(match: dict[str, Any]) -> FileResult:
    fr = FileResult(path=match["path"], commit=match.get("commit", ""))
    for chunk in match.get("chunkMatches") or []:
        content: str = chunk.get("content", "")
        texts = content.split("\n")
        if content.endswith("\n"):
            texts.pop()
        start0 = chunk["contentStart"]["line"]
        lines = [
            SnippetLine(line_text=t, line_number=start0 + i + 1)
            for i, t in enumerate(texts)
        ]
        for r in chunk.get("ranges", []):
            s, e = r["start"], r["end"]
            for ln0 in range(s["line"], e["line"] + 1):
                idx = ln0 - start0
                if not 0 <= idx < len(lines):
                    continue
                text = lines[idx].line_text
                cs = s["column"] if ln0 == s["line"] else 0
                ce = e["column"] if ln0 == e["line"] else len(text)
                if ce > cs:
                    lines[idx].ranges.append(MatchRange(cs, ce))
        fr.snippets.append(Snippet(lines=lines))
    return fr


def parse_symbols(matches: list[dict[str, Any]]) -> list[Symbol]:
    out: list[Symbol] = []
    for m in matches:
        if m.get("type") != "symbol":
            continue
        for s in m.get("symbols", []):
            out.append(
                Symbol(
                    name=s.get("name", ""),
                    kind=s.get("kind", "").lower(),
                    container=s.get("containerName", ""),
                    path=m["path"],
                    line=int(s.get("line", 0)),
                    commit=m.get("commit", ""),
                )
            )
    return out


# ---------------------------------------------------------------------------
# File / tree clients
# ---------------------------------------------------------------------------


def fetch_file_contents(path: str, ref: str | None = None) -> list[str]:
    rev = f"@{urllib.parse.quote(ref, safe='')}" if ref else ""
    url = f"{ENDPOINT}/{REPO}{rev}/-/raw/{urllib.parse.quote(path.lstrip('/'))}"
    req = urllib.request.Request(url, headers=_headers())
    try:
        with _open(req) as resp:
            ctype = resp.headers.get("Content-Type", "")
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"File not found: {path} at {ref or 'HEAD'}") from exc
    if "text/html" in ctype and body.lstrip().lower().startswith("<!doctype"):
        raise RuntimeError(f"File not found: {path} at {ref or 'HEAD'}")
    if body.endswith("\n"):
        body = body[:-1]
    return body.split("\n")


def graphql(query: str, variables: dict[str, Any]) -> Any:
    req = urllib.request.Request(
        f"{ENDPOINT}/.api/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers=_headers({"Content-Type": "application/json"}),
        method="POST",
    )
    with _open(req) as resp:
        raw = json.loads(resp.read().decode())
    if raw.get("errors"):
        raise RuntimeError("; ".join(e.get("message", "") for e in raw["errors"]))
    return raw["data"]


TREE_QUERY = """
query($repo: String!, $rev: String!, $path: String!) {
  repository(name: $repo) {
    commit(rev: $rev) {
      tree(path: $path) { entries(first: 10000) { name isDirectory } }
    }
  }
}
"""


def fetch_tree(path: str, ref: str | None = None) -> list[tuple[str, bool]]:
    data = graphql(
        TREE_QUERY, {"repo": REPO, "rev": ref or "HEAD", "path": path.strip("/")}
    )
    commit = (data.get("repository") or {}).get("commit")
    if not commit:
        raise RuntimeError(f"Unknown ref: {ref}")
    tree = commit.get("tree")
    if not tree:
        raise RuntimeError(f"Directory not found: {path}")
    return [(e["name"], e["isDirectory"]) for e in tree["entries"]]


# ---------------------------------------------------------------------------
# Terminal output
# ---------------------------------------------------------------------------

STDOUT_IS_TTY = sys.stdout.isatty()
USE_COLOR = STDOUT_IS_TTY and not os.environ.get("NO_COLOR")
SGR = {"bold": 1, "dim": 2, "red": 31, "yellow": 33, "cyan": 36}


def style(text: str, *attrs: str) -> str:
    """Wrap text in ANSI SGR codes when color output is enabled."""
    codes = ";".join(str(SGR[a]) for a in attrs if a in SGR)
    return f"\x1b[{codes}m{text}\x1b[0m" if USE_COLOR and codes else text


@contextlib.contextmanager
def progress(label: str) -> Iterator[None]:
    """Show an animated status line on stderr while the body runs."""
    if not sys.stderr.isatty():
        yield
        return
    done = threading.Event()

    def animate() -> None:
        for frame in itertools.cycle("◐◓◑◒"):
            sys.stderr.write(f"\r{frame} {label}")
            sys.stderr.flush()
            if done.wait(0.1):
                break
        sys.stderr.write("\r\x1b[K")
        sys.stderr.flush()

    worker = threading.Thread(target=animate, daemon=True)
    worker.start()
    try:
        yield
    finally:
        done.set()
        worker.join()


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def line_url(path: str, line: int, commit: str = "") -> str:
    name = REPO.split("/", 1)[1] if REPO.startswith("github.com/") else None
    if name:
        return f"https://github.com/{name}/blob/{commit or 'main'}/{path}#L{line}"
    rev = f"@{commit}" if commit else ""
    return f"{ENDPOINT}/{REPO}{rev}/-/blob/{path}?L{line}"


def highlight_line(line: SnippetLine) -> str:
    if not USE_COLOR or not line.ranges:
        return line.line_text
    result, pos = "", 0
    for r in sorted(line.ranges, key=lambda r: r.start):
        if r.start < pos:
            continue
        result += line.line_text[pos : r.start]
        result += style(line.line_text[r.start : r.end], "bold", "red")
        pos = r.end
    return result + line.line_text[pos:]


def iter_lines(files: list[FileResult], limit: int):
    """Yield (file, snippet_index, line) for match and context lines, capped."""
    count = 0
    for f in files:
        for si, sn in enumerate(f.snippets):
            if not any(ln.ranges for ln in sn.lines):
                continue
            for ln in sn.lines:
                if ln.ranges:
                    if count >= limit:
                        return
                    count += 1
                yield f, si, ln


def format_grouped(files: list[FileResult], context: bool, limit: int) -> str:
    out: list[str] = []
    cur: FileResult | None = None
    prev_si, prev_line = -1, -1
    for f, si, ln in iter_lines(files, limit):
        if not ln.ranges and not context:
            continue
        if f is not cur:
            if cur is not None:
                out.append("")
            out.append(style(f.path, "bold", "cyan"))
            cur, prev_si, prev_line = f, si, -1
        if si != prev_si and prev_line >= 0 and ln.line_number > prev_line + 1:
            out.append(style("    ...", "dim"))
        prev_si, prev_line = si, ln.line_number
        num = style(str(ln.line_number).rjust(5), "dim")
        if ln.ranges:
            out.append(f"{num}:  {highlight_line(ln)}")
        else:
            out.append(style(f"{num}   {ln.line_text}", "dim"))
    return "\n".join(out)


def format_flat(files: list[FileResult], context: bool, limit: int) -> str:
    out: list[str] = []
    prev: tuple[int, int] | None = None
    for f, si, ln in iter_lines(files, limit):
        if not ln.ranges and not context:
            continue
        key = (id(f), si)
        if context and prev is not None and key != prev:
            out.append("--")
        prev = key
        sep = ":" if ln.ranges else "-"
        out.append(f"{f.path}:{ln.line_number}{sep} {ln.line_text}")
    return "\n".join(out)


def format_json(files: list[FileResult], fields: list[str], limit: int) -> str:
    entries: list[dict[str, Any]] = []
    wants_context = "context" in fields
    seen: set[tuple[int, int]] = set()
    for f, si, ln in iter_lines(files, limit):
        if not ln.ranges:
            continue
        if wants_context:
            if (id(f), si) in seen:
                continue
            seen.add((id(f), si))
        entry: dict[str, Any] = {}
        for fld in fields:
            if fld == "path":
                entry["path"] = f.path
            elif fld == "line":
                entry["line"] = ln.line_number
            elif fld == "snippet":
                entry["snippet"] = ln.line_text
            elif fld == "context":
                entry["context"] = [x.line_text for x in f.snippets[si].lines]
            elif fld == "matchRanges":
                entry["matchRanges"] = [
                    {"start": r.start, "end": r.end} for r in ln.ranges
                ]
            elif fld == "url":
                entry["url"] = line_url(f.path, ln.line_number, f.commit)
        entries.append(entry)
    return json.dumps(entries, indent=2)


def count_matches(files: list[FileResult]) -> int:
    return sum(1 for f in files for s in f.snippets for ln in s.lines if ln.ranges)


# ---------------------------------------------------------------------------
# Syntax reference
# ---------------------------------------------------------------------------

SYNTAX_REFERENCE = """\
WebKit Code Search — Query Syntax Reference (Sourcegraph keyword syntax)

Queries are automatically scoped to the WebKit repo (unless you pass repo:).

PATTERNS
  foo bar               Both terms must match (AND is implicit)
  foo OR bar            Either term may match
  NOT foo / -foo        Exclude matches
  "exact phrase"        Match an exact phrase (\\" to escape quotes)
  /regex/               Regular expression (RE2 syntax)
  (a OR b) c            Group expressions with parentheses

FILTERS
  file:<regex>          Filter by path regex   (file:\\.cpp$  file:^Source/WebCore/css/)
  -file:<regex>         Exclude paths          (-file:LayoutTests/ -file:/Tests?/)
  lang:<language>       Filter by language     (c++, objective-c, swift, javascript,
                                                python, perl, ruby, cmake, idl, ...)
  case:yes              Case-sensitive search (default: insensitive)
  type:symbol           Search symbol definitions (or use the `sym` subcommand)
  type:path             Match file paths only (or use the `find` subcommand)
  type:commit           Search commit messages   (try with author: before: after:)
  type:diff             Search diffs             (try with author: before: after:)
  select:symbol.class   With type:symbol, restrict kind (class, function, method,
                        struct, enum, variable, field, namespace, ...)
  count:<n>|all         Max results (set automatically from -L)
  timeout:<duration>    Raise search timeout (e.g. timeout:30s)

NOTE: file: takes a regex, not a glob — `file:*_test.cc` is invalid; use
`file:Test\\.cpp$` instead.

EXAMPLES
  RenderBox::computeLogicalWidth                   Simple search
  'case:yes lang:c++ LayoutUnit'                   Case-sensitive C++ search
  'file:^Source/WebCore/css/ CSSValueID'           Restrict to a directory
  'lang:c++ -file:Tests/ /WTF::(Ref|RefPtr)<Node>/'  Regex, excluding tests
  'type:symbol select:symbol.class RenderFlex'     Class definitions
  'type:commit author:Antti flexbox after:"1 month ago"'  Recent commits
"""


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _print_alerts(alerts: list[str]) -> None:
    for a in alerts:
        sys.stderr.write(style(f"note: {a}", "yellow") + "\n")


def run_search(
    query: str,
    *,
    limit: int,
    context_lines: int,
    json_fields: str | None,
    ref: str | None,
) -> None:
    with progress("Searching..."):
        res = stream_search(
            scoped_query(query, ref, limit),
            display=limit * 3,
            context_lines=context_lines,
        )

    # Non-content results (commit/diff/symbol/path) — print simply.
    others = [m for m in res.matches if m.get("type") != "content"]
    if others and not any(m.get("type") == "content" for m in res.matches):
        run_other(others[:limit], json_out=json_fields is not None)
        _print_alerts(res.alerts)
        return

    files = [parse_content(m) for m in res.matches if m.get("type") == "content"]
    if not files:
        if json_fields is not None:
            sys.stdout.write("[]\n")
        _print_alerts(res.alerts)
        sys.stderr.write("No results found\n")
        return

    if json_fields is not None:
        fields = [f.strip() for f in json_fields.split(",") if f.strip()]
        fields = fields or ["path", "line", "snippet", "url"]
        sys.stdout.write(format_json(files, fields, limit) + "\n")
    elif STDOUT_IS_TTY:
        shown = min(count_matches(files), limit)
        total = res.match_count
        if total > shown:
            sys.stderr.write(f"Showing {shown} of {total}+ results\n\n")
        else:
            sys.stderr.write(f"Showing {shown} results\n\n")
        sys.stdout.write(format_grouped(files, context_lines > 0, limit) + "\n")
    else:
        sys.stdout.write(format_flat(files, context_lines > 0, limit) + "\n")
    _print_alerts(res.alerts)


def run_other(matches: list[dict[str, Any]], *, json_out: bool) -> None:
    if json_out:
        sys.stdout.write(json.dumps(matches, indent=2) + "\n")
        return
    for m in matches:
        t = m.get("type")
        if t in ("commit", "diff"):
            oid = m.get("oid", "")[:12]
            date = m.get("authorDate", "")[:10]
            subject = (m.get("message") or "").split("\n", 1)[0]
            sys.stdout.write(
                f"{style(oid, 'yellow')} {date} {m.get('authorName', '')}: {subject}\n"
            )
            if t == "diff" and m.get("content"):
                sys.stdout.write(m["content"].rstrip("\n") + "\n\n")
        elif t == "symbol":
            for s in parse_symbols([m]):
                _print_symbol(s)
        elif t == "path":
            sys.stdout.write(m["path"] + "\n")


def _print_symbol(s: Symbol) -> None:
    container = f"{s.container}::" if s.container and s.container != s.name else ""
    loc = style(f"{s.path}:{s.line}", "cyan")
    sys.stdout.write(f"{loc}  {style(s.kind, 'dim')} {container}{s.name}\n")


def run_sym(query: str, *, limit: int, kind: str | None, json_out: bool) -> None:
    q = f"type:symbol {query}"
    if kind:
        q += f" select:symbol.{kind}"
    with progress("Searching symbols..."):
        res = stream_search(
            scoped_query(q, None, limit), display=limit * 3, context_lines=0
        )
    syms = parse_symbols(res.matches)[:limit]
    if not syms:
        if json_out:
            sys.stdout.write("[]\n")
        _print_alerts(res.alerts)
        sys.stderr.write("No results found\n")
        return
    if json_out:
        rows = [
            {
                "name": s.name,
                "kind": s.kind,
                "container": s.container,
                "path": s.path,
                "line": s.line,
                "url": line_url(s.path, s.line, s.commit),
            }
            for s in syms
        ]
        sys.stdout.write(json.dumps(rows, indent=2) + "\n")
        return
    for s in syms:
        _print_symbol(s)


def run_find(query: str, *, limit: int) -> None:
    with progress("Finding..."):
        res = stream_search(
            scoped_query(f"type:path {query}", None, limit),
            display=limit * 3,
            context_lines=0,
        )
    paths = [m["path"] for m in res.matches if "path" in m][:limit]
    if not paths:
        _print_alerts(res.alerts)
        sys.stderr.write("No results found\n")
        return
    for p in paths:
        sys.stdout.write(p + "\n")


def run_cat(path: str, *, ref: str | None, number_lines: bool) -> None:
    with progress("Fetching..."):
        lines = fetch_file_contents(path, ref=ref)
    for i, line in enumerate(lines, 1):
        sys.stdout.write(f"{i:6}\t{line}\n" if number_lines else line + "\n")


def run_ls(path: str, *, ref: str | None) -> None:
    with progress("Listing..."):
        entries = fetch_tree(path, ref=ref)
    for name, is_dir in entries:
        sys.stdout.write((style(name + "/", "bold", "cyan") if is_dir else name) + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

USAGE = """\
Usage: webkit-search [command] <query> [flags]

Commands:
  search <query>   Search WebKit source code
  sym <name>       Find symbol definitions (classes, functions, ...)
  find <name>      Find files by path
  cat <path>       Print file contents
  ls [dir]         List directory contents
  syntax           Print query syntax reference
  <query>          Implicit search (no subcommand needed)

Flags (search/sym/find):
  -L, --limit <n>         Max results (default: 30; find: 10)
  -C, --context <n>       Context lines around matches (default: 0)
      --json [fields]     JSON output (fields: path,line,snippet,context,
                          matchRanges,url)
      --kind <kind>       sym only: class, function, method, struct, enum, ...

Flags (search/cat/ls):
      --ref <ref>         Git branch, tag, or commit (default: main)
  -n                      cat only: number output lines

Flags (all):
      --no-color          Disable colored output
  -h, --help              Show this help

Environment:
  SRC_ACCESS_TOKEN        Sourcegraph token (optional; raises rate limits)
  SRC_ENDPOINT            Sourcegraph instance (default: https://sourcegraph.com)
  WEBKIT_SEARCH_REPO      Repo to search (default: github.com/WebKit/WebKit)

Examples:
  webkit-search RenderBox::computeLogicalWidth
  webkit-search 'lang:c++ file:^Source/WebCore/css/ CSSValueID' -C 2
  webkit-search 'type:commit flexbox after:"2 weeks ago"'
  webkit-search sym RenderFlexibleBox --kind class
  webkit-search find RenderBox.h
  webkit-search cat Source/WebCore/rendering/RenderBox.h -n
  webkit-search cat Source/WTF/wtf/Vector.h --ref safari-7620-branch
  webkit-search ls Source/WebCore/layout
"""


def main() -> None:
    parser = argparse.ArgumentParser(prog="webkit-search", add_help=False)
    parser.add_argument("args", nargs="*")
    parser.add_argument("-L", "--limit", type=int, default=None)
    parser.add_argument("-C", "--context", type=int, default=0)
    parser.add_argument("--json", nargs="?", const="", default=None, dest="json_fields")
    parser.add_argument("--ref", default=None)
    parser.add_argument("--kind", default=None)
    parser.add_argument("-n", "--number", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    opts = parser.parse_args()

    if opts.help:
        sys.stderr.write(USAGE)
        return
    if opts.no_color:
        global USE_COLOR
        USE_COLOR = False

    pos: list[str] = opts.args
    sub = pos[0] if pos else ""
    rest = " ".join(pos[1:])

    if sub == "syntax":
        sys.stdout.write(SYNTAX_REFERENCE)
    elif sub == "cat":
        if not rest:
            sys.exit("Usage: webkit-search cat <path> [--ref <ref>] [-n]")
        run_cat(rest, ref=opts.ref, number_lines=opts.number)
    elif sub == "ls":
        run_ls(rest, ref=opts.ref)
    elif sub == "find":
        if not rest:
            sys.exit("Usage: webkit-search find <name>")
        run_find(rest, limit=opts.limit or 10)
    elif sub == "sym":
        if not rest:
            sys.exit("Usage: webkit-search sym <name> [--kind <kind>]")
        run_sym(
            rest,
            limit=opts.limit or 30,
            kind=opts.kind,
            json_out=opts.json_fields is not None,
        )
    else:
        query = rest if sub == "search" else " ".join(pos)
        if not query:
            sys.stderr.write(USAGE)
            sys.exit(1)
        run_search(
            query,
            limit=opts.limit or 30,
            context_lines=opts.context,
            json_fields=opts.json_fields,
            ref=opts.ref,
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        sys.stderr.write(f"Error: {exc}\n")
        sys.exit(1)
