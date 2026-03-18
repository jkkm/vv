# vv

A simple tool to keep a collection of git repositories up to date with upstream.

## Requirements

- Python 3.11+
- [PyYAML](https://pypi.org/project/PyYAML/)

## Configuration

`vv` reads `~/.vv.conf` on startup. If the file is absent, defaults are used.
An example config is provided in `vv.conf`.

| Key | Default | Description |
|-----|---------|-------------|
| `basedir` | `~/src` | Directory whose immediate subdirectories are managed |
| `remote` | `origin` | Remote name to pull from |
| `jobs` | `4` | Number of parallel pulls |
| `include` | *(none)* | List of repo paths to manage outside `basedir` |
| `exclude` | *(none)* | List of `basedir` subdirectory names to skip |
| `trees` | *(none)* | Per-tree overrides (see below) |

### Per-tree overrides

Any top-level key can be overridden for a specific tree under `trees`:

```yaml
trees:
  linux:
    remote: upstream
    remotes:
      - upstream
      - origin
```

| Key (per-tree) | Description |
|---|---|
| `remotes` | List of remotes to fetch; defaults to all from `git remote` |

## Commands

### `vv update`

For each repository, first fetches all configured remotes in parallel (`jobs`
workers), then pulls the tracking branch. Fetch remotes are taken from the
per-tree `remotes` list if set, otherwise all remotes returned by `git remote`
are fetched. The pull remote is resolved from the per-tree `remote` key, the
branch's tracking remote, the top-level `remote` key, or `origin` (in that
order). Dirty trees are skipped with a warning. Results are printed by the main
thread as each update completes, avoiding interleaved output. If any pulls fail,
an interactive shell is spawned in each affected tree (after all parallel updates
finish) so the problem can be investigated; the pull is retried when the shell
exits.

Results are appended to `~/.vv.log`.

### `vv dirty`

Lists all repositories under `basedir` that have uncommitted changes.

### `vv include <path>`

Adds a repository to the `include` list in `~/.vv.conf`. The path may be
anywhere on the filesystem, not just under `basedir`.

### `vv exclude <path>`

Adds a directory to the `exclude` list in `~/.vv.conf`. Accepts a bare name
or a path; the target must be a direct subdirectory of `basedir`.

## License

ISC — see source for full text.
