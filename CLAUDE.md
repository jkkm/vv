# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Overview

vv is a multi-VCS source tree updater. Single-file Python (`vv.py`), one dependency (PyYAML). Requires Python 3.11+.

Keeps a collection of source repos up to date with upstream. Scans multiple basedirs for repos, autodetects VCS (git, hg, svn, cvs), and manages repos found there plus explicitly included paths.

## Commands

- `vv list` — list managed repositories
- `vv update` — parallel fetch+merge, skip dirty trees, interactive shell on failure
- `vv dirty` — list repos with uncommitted changes
- `vv include <path>` — add a repo from anywhere on the filesystem
- `vv exclude <path>` — skip a basedir subdirectory

## Config

`~/.vv.conf` (YAML). Top-level keys: `basedirs` (dict of paths, each with optional `exclude` and `trees`), `jobs`, `include` (dict of path → tree config). Per-tree keys: `type`, `remotes`, `updatecmd`.

## Architecture

Everything lives in `vv.py`:

- **`VcsDriver`** — frozen dataclass defining a VCS type: name, directory marker (e.g. `.git`), dirty-check command, and default update command. The four built-in drivers are in `VCS_DRIVERS`; lookup by name via `_VCS_BY_NAME`.
- **`Repo`** — dataclass holding a repo's path, its `VcsDriver`, a display label, and per-tree config from the YAML file.
- **Config flow** — `load_config()` reads `~/.vv.conf` with `yaml.safe_load`. `get_repos()` walks each basedir's immediate subdirectories plus `include` entries, calls `detect_vcs()` (checks for marker dirs) or uses explicit `type`, and builds `Repo` objects.
- **Threading model** — `cmd_update` uses `ThreadPoolExecutor` with a `SimpleQueue`. Workers run `_update_worker` (fetch + ff-merge) and post results to the queue. The main thread reads results sequentially to avoid interleaved output, then spawns interactive shells for failures.

## Development

```sh
python3 vv.py <command>    # run directly
python3 -m py_compile vv.py  # syntax check
```

No test suite. Validate changes manually against a real basedir.

## Workflow

- Break changes into logical git commits (one concern per commit).
- Keep `README.md` up to date when changing program behavior (commands, config, flags, etc.).
