# vv

A simple tool to keep a collection of source repositories up to date with upstream. Supports git, Mercurial (hg), Subversion (svn), and CVS, with automatic detection from directory markers.

But mostly a sandbox for Claude code.

## Requirements

- Python 3.11+
- [PyYAML](https://pypi.org/project/PyYAML/)

## Configuration

`vv` reads `~/.vv.conf` on startup. If the file is absent, defaults are used.
An example config is provided in `vv.conf`.

Configuration is validated before a command runs. Invalid YAML, unknown keys,
unsupported VCS types, and incorrectly typed values are reported as configuration
errors with the relevant key path.

The `include` and `exclude` commands update the configuration atomically.
Existing file permissions are preserved; a newly created configuration is mode
`0600`.

```yaml
jobs: 4

basedirs:
  ~/src:
    exclude: [vv]
    trees:
      linux:
        remotes: [upstream, origin]
  ~/work:
    trees:
      project-x:
        updatecmd: "make update"

include:
  ~/other/repo:
    type: git
    updatecmd: "custom cmd"
  ~/simple/repo:
```

### Top-level keys

| Key | Default | Description |
|-----|---------|-------------|
| `basedirs` | `~/src` | Dict of directory paths to scan for repos (see below) |
| `jobs` | `4` | Number of parallel pulls |
| `logfile` | `~/.vv.log` | Path to the update log file |
| `fetch_timeout` | `60` | Seconds before a fetch or update is killed and reported as failed |
| `include` | *(none)* | Dict of repo paths to manage outside any basedir (see below) |

### Per-basedir keys

Each key under `basedirs` is a directory path whose immediate subdirectories
are managed. The value is a dict with optional keys:

| Key | Description |
|-----|-------------|
| `exclude` | List of subdirectory names to skip |
| `trees` | Dict of per-tree overrides, keyed by basename (see below) |

### Per-tree overrides

Tree config can appear under `basedirs.<path>.trees.<name>` for basedir repos,
or inline as the value under `include.<path>` for standalone repos.

| Key | Description |
|-----|-------------|
| `type` | VCS type (`git`, `hg`, `svn`, `cvs`); autodetected from directory markers (`.git`, `.hg`, `.svn`, `CVS`) when absent |
| `remotes` | List of remotes to fetch; defaults to all from `git remote` (git only) |
| `updatecmd` | Shell command to run instead of the default update command for the VCS |
| `submodules` | `true`/`false` to override submodule update behavior; by default `git submodule update --init --recursive` is run after a successful merge when `.gitmodules` exists (git only) |

### Include

`include` is a dict of paths to standalone repos not under any basedir. Each
key is an absolute path; the value is a tree config dict (same keys as
per-tree overrides), or `null`/empty for defaults.

## Commands

### `vv update`

For each repository, runs the appropriate update command for its VCS. For git
repos, all remotes are fetched first (or the per-tree `remotes` list if set),
then a fast-forward merge is performed; this fetch-then-merge approach is
git-only. Other VCS types use their native update command (`hg pull -u`,
`svn update`, `cvs update`), or the per-tree `updatecmd` shell command if set.
Dirty trees are skipped with a warning. Results are printed by the main thread
as each update completes, avoiding interleaved output. If any updates fail, an
interactive shell is spawned in each affected tree (after all parallel updates
finish) so the problem can be investigated; the update is retried when the
shell exits.

Results are appended to `~/.vv.log`.

### `vv list`

Lists all managed repositories from all basedirs plus includes.

### `vv dirty`

Lists repositories with uncommitted changes. Checks all repos under all
basedirs plus any in the `include` dict. Works with all supported VCS types.

### `vv include <path>`

Adds a repository to the `include` dict in `~/.vv.conf`. The path may be
anywhere on the filesystem, not just under a basedir. Accepts any supported
VCS type.

### `vv exclude <path>`

Adds a directory to the `exclude` list for its parent basedir in `~/.vv.conf`.
Accepts a bare name or a path; the target must be a direct subdirectory of a
configured basedir.

## License

ISC — see source for full text.
