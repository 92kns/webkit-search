- Single-file Python 3.11+ tool (`webkit-search.py`), run with `uv run webkit-search.py`
- Zero dependencies — stdlib only
- Keep it simple: no build system, no packaging
- Backend is the public Sourcegraph API (sourcegraph.com)
  - Search: `GET /.api/search/stream` (SSE; `cm=t` chunk matches, `cl=N` context lines;
    chunk/line numbers are 0-based, symbol `line` is 1-based)
  - cat: `GET /{repo}@{rev}/-/raw/{path}` (needs a non-default User-Agent, else 403)
  - ls: GraphQL `repository.commit.tree.entries`

## Dev commands

- Format: `uvx ruff@0.14.0 format webkit-search.py`
- Lint: `uvx ruff@0.14.0 check webkit-search.py`
- Typecheck: `uvx ty check webkit-search.py`
