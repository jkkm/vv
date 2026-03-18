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

Iterates all git repositories under `basedir` (excluding those in `exclude`)
and runs `git pull <remote>` on each clean tree. Dirty trees are skipped with
a warning. If a pull fails, an interactive shell is spawned in that tree so
the problem can be investigated; the pull is retried when the shell exits.

Results are appended to `~/.vv.log`.

### `vv dirty`

Lists all repositories under `basedir` that have uncommitted changes.

### `vv exclude <path>`

Adds a directory to the `exclude` list in `~/.vv.conf`. Accepts a bare name
or a path; the target must be a direct subdirectory of `basedir`.

## License

ISC — see source for full text.
