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
| `exclude` | *(none)* | List of subdirectory names to skip |
| `trees` | *(none)* | Per-tree overrides (see below) |

### Per-tree overrides

Any top-level key can be overridden for a specific tree under `trees`:

```yaml
trees:
  linux:
    remote: upstream
```

## Commands

### `vv update`

Pulls all git repositories under `basedir` (excluding those in `exclude`) in
parallel (`jobs` workers). Dirty trees are skipped with a warning. All output
is emitted from the main thread in sorted order once the pulls complete. If
any pulls fail, an interactive shell is spawned in each affected tree (after
all parallel pulls finish) so the problem can be investigated; the pull is
retried when the shell exits.

Results are appended to `~/.vv.log`.

### `vv dirty`

Lists all repositories under `basedir` that have uncommitted changes.

### `vv exclude <path>`

Adds a directory to the `exclude` list in `~/.vv.conf`. Accepts a bare name
or a path; the target must be a direct subdirectory of `basedir`.

## License

ISC — see source for full text.
