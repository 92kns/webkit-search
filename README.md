# webkit-search

An agent friendly CLI for [WebKit source code](https://github.com/WebKit/WebKit).

A single-file Python script with zero dependencies. Search, symbols, find, and ls use the
public [Sourcegraph](https://sourcegraph.com) API, which indexes `github.com/WebKit/WebKit`.
`cat` reads straight from GitHub (always current) and falls back to Sourcegraph.

## Install

```
ln -s "$PWD/webkit-search.py" ~/.local/bin/webkit-search   # or anywhere on PATH
```

Requires Python 3.11+ or [`uv`](https://docs.astral.sh/uv/).

## Usage

```
webkit-search <query>                 # search source code
webkit-search sym <name> [--kind k]   # find symbol definitions
webkit-search find <name>             # find files by path
webkit-search cat <path> [-n]         # print file contents
webkit-search ls [dir]                # list a directory
webkit-search syntax                  # query syntax reference
```

### Search

```
webkit-search RenderBox::computeLogicalWidth
webkit-search 'lang:c++ file:^Source/WebCore/css/ CSSValueID' -C 2
webkit-search '/RefPtr<Render\w+>/ -file:Tests/' -L 10
webkit-search 'type:symbol select:symbol.class RenderFlex' --json
webkit-search 'file:wtf/Vector\.h$ reserveCapacity' --ref safari-7620-branch
```

`-L <n>` limits results (default 30). `-C <n>` adds context lines. `--json [fields]` outputs
JSON (`path,line,snippet,context,matchRanges,url`). `--ref` searches a branch/tag/commit
(slower — non-default branches are searched unindexed).

Output is grouped and colored on a TTY, and `path:line: text` (grep-style) when piped.

Syntax is Sourcegraph's keyword syntax — **`file:` takes a regex, not a glob**. Run `webkit-search syntax` for the reference.

### Symbols, files, contents

```
webkit-search sym RenderFlexibleBox --kind class
webkit-search find RenderBox.h
webkit-search cat Source/WebCore/rendering/RenderBox.h -n
webkit-search cat Source/WTF/wtf/Vector.h --ref safari-7620-branch
webkit-search ls Source/WebCore/layout
```

## Configuration

| Env var              | Default                     |                                   |
|----------------------|-----------------------------|-----------------------------------|
| `SRC_ACCESS_TOKEN`   | (none)                      | Sourcegraph token; raises rate limits |
| `SRC_ENDPOINT`       | `https://sourcegraph.com`   | Another Sourcegraph instance      |
| `WEBKIT_SEARCH_REPO` | `github.com/WebKit/WebKit`  | Any repo that instance indexes    |

## Freshness and line numbers

Sourcegraph refreshes its copy of WebKit irregularly, sometimes a few days behind `main`. When
the index is over a day old, results include a one-line `note:` on stderr. Search line numbers
match the commit in each result's URL (also in `--json` output). Plain `cat` shows the latest
`main`, so if line numbers matter, use `cat <path> --ref <commit-from-url>`.

## Safety

`cat` only fetches from `raw.githubusercontent.com/WebKit/WebKit/` (or the configured
Sourcegraph instance). No credentials are sent to GitHub. Paths containing `..` and refs
starting with `-` are rejected. Files over 5 MB, binary files, and Git LFS pointers are
refused with a message instead of being printed.

## Limitations

- Anonymous use is rate-limited; set `SRC_ACCESS_TOKEN` if you hit 429s.
- `type:commit` / `type:diff` on sourcegraph.com only reach recent history; use
  `git log` or GitHub for older commits.
- No cross-references ("find usages" is text search, not semantic like Code Search's `usage:`).
