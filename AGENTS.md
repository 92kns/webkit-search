- Single-file Python 3.11+ tool (`webkit-search.py`), run with `uv run webkit-search.py`
- Zero dependencies — stdlib only
- Keep it simple: no build system, no packaging
- Routing (see `run_search`, `run_sym`): searchfox first for simple queries on main
  (`searchfox_plan()` decides; JSON API `GET {SEARCHFOX}/wubkat/search?q=&path=&case=&regexp=`,
  1-based `lno`, one `bounds` span per line, no context lines, long lines truncated; `path` is
  substring or `^`-anchored, not regex). Fallback to Sourcegraph on error or zero results.
- searchfox-cli is an optional dependency, only for `define`/`calls-to`/`calls-from`/`field-layout`
  (`run_searchfox_cli`, passes args as a list, no shell)
- Sourcegraph API (sourcegraph.com)
  - Search: `GET /.api/search/stream` (SSE; `cm=t` chunk matches, `cl=N` context lines;
    chunk/line numbers are 0-based, symbol `line` is 1-based)
  - cat: GitHub `raw.githubusercontent.com/{owner/name}/{ref|HEAD}/{path}` first (fresh; never send auth),
    fallback Sourcegraph `GET /{repo}@{rev}/-/raw/{path}` (needs a non-default User-Agent, else 403)
  - `select:` filters apply after `count:`, so `sym --kind` fetches a wide candidate set
  - Staleness: each match has `repoLastFetched`; `_staleness()` adds a note when it's over 24h old
  - ls: GraphQL `repository.commit.tree.entries`

## Dev commands

- Format: `uvx ruff@0.14.0 format webkit-search.py`
- Lint: `uvx ruff@0.14.0 check webkit-search.py`
- Typecheck: `uvx ty check webkit-search.py`
