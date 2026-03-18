# vv

A simple tool to keep a collection of source repositories up to date with upstream. Supports git, Mercurial (hg), Subversion (svn), and CVS, with automatic detection from directory markers.

## Requirements

- Python 3.11+
- [PyYAML](https://pypi.org/project/PyYAML/)

## Configuration

`vv` reads `~/.vv.conf` on startup. If the file is absent, defaults are used.
An example config is provided in `vv.conf`.

| Key | Default | Description |
|-----|---------|-------------|
| `basedir` | `~/src` | Directory whose immediate subdirectories are managed |
| `jobs` | `4` | Number of parallel pulls |
| `include` | *(none)* | List of repo paths to manage outside `basedir` |
| `exclude` | *(none)* | List of `basedir` subdirectory names to skip |
| `trees` | *(none)* | Per-tree overrides (see below) |

### Per-tree overrides

Any top-level key can be overridden for a specific tree under `trees`. Keys are
matched by basename, or by resolved filesystem path when the key starts with `/`
or `~`. Path-based keys take priority over name-based keys, and are useful for
targeting included repos that share a basename with a `basedir` repo.

```yaml
trees:
  ~/linux:          # matches the included ~/linux repo by path
    remotes: [linus]
    updatecmd: "git fetch linus && git merge linus/master"
  linux:            # matches ~/src/linux (basedir repo) by name
    updatecmd: "git pull --rebase origin"
```

| Key (per-tree) | Description |
|---|---|
| `type` | VCS type (`git`, `hg`, `svn`, `cvs`); autodetected from directory markers (`.git`, `.hg`, `.svn`, `CVS`) when absent |
| `remotes` | List of remotes to fetch before pulling; defaults to all from `git remote` (git only) |
| `updatecmd` | Shell command to run instead of the default update command for the VCS |

## Commands

### `vv update`

For each repository, runs the appropriate update command for its VCS. For git
repos, all remotes are fetched first (or the per-tree `remotes` list if set);
this pre-fetch step is git-only. The update command defaults to the VCS
default (`git pull`, `hg pull -u`, `svn update`, `cvs update`), or the
per-tree `updatecmd` shell command if set. Dirty trees are skipped with a
warning. Results are printed by the main thread as each update completes,
avoiding interleaved output. If any pulls fail, an interactive shell is spawned
in each affected tree (after all parallel updates finish) so the problem can be
investigated; the pull is retried when the shell exits.

Results are appended to `~/.vv.log`.

### `vv dirty`

Lists repositories with uncommitted changes. Checks all repos under `basedir`
plus any in the `include` list. Works with all supported VCS types.

### `vv include <path>`

Adds a repository to the `include` list in `~/.vv.conf`. The path may be
anywhere on the filesystem, not just under `basedir`. Accepts any supported
VCS type.

### `vv exclude <path>`

Adds a directory to the `exclude` list in `~/.vv.conf`. Accepts a bare name
or a path; the target must be a direct subdirectory of `basedir`.

## License

ISC — see source for full text.
