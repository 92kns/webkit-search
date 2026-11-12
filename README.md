# webkit-search

An agent friendly CLI for [WebKit source code](https://github.com/WebKit/WebKit).

A single-file Python script with zero dependencies. Talks to the public
[Sourcegraph](https://sourcegraph.com) API, which indexes `github.com/WebKit/WebKit`.

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

## Limitations

- Anonymous use is rate-limited; set `SRC_ACCESS_TOKEN` if you hit 429s.
- `type:commit` / `type:diff` on sourcegraph.com only reach recent history; use
  `git log` or GitHub for older commits.
- No cross-references ("find usages" is text search, not semantic like Code Search's `usage:`).
