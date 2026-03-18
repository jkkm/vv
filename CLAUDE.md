# vv

Multi-VCS source tree updater. Single-file Python (`vv.py`), one dependency (PyYAML).

## What it does

Keeps a collection of source repos up to date with upstream. Points at a basedir (default `~/src`), autodetects VCS (git, hg, svn, cvs), and manages repos found there plus explicitly included paths.

## Commands

- `vv list` — list managed repositories
- `vv update` — parallel fetch+pull, skip dirty trees, interactive shell on failure
- `vv dirty` — list repos with uncommitted changes
- `vv include <path>` — add a repo from anywhere on the filesystem
- `vv exclude <path>` — skip a basedir subdirectory

## Config

`~/.vv.conf` (YAML). Keys: `basedir`, `jobs`, `exclude`, `include`, `trees` (per-repo overrides for `type`, `remotes`, `updatecmd`).

## Development

```sh
python3 vv.py <command>    # run directly
python3 -m py_compile vv.py  # syntax check
```

No test suite. Validate changes manually against a real basedir.

## Workflow

- Break changes into logical git commits (one concern per commit).
- Keep `README.md` up to date when changing program behavior (commands, config, flags, etc.).
