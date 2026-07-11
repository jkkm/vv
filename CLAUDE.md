# Repository Guidance

This guidance applies to every coding assistant working in this repository.

## Project

`vv` is a Python 3.11+ command-line tool for updating collections of Git,
Mercurial, Subversion, and CVS repositories. The implementation is currently in
`vv.py`; `test_vv.py` contains the standard-library `unittest` suite. PyYAML is
the only runtime dependency.

Configuration is read from `~/.vv.conf`. Avoid reading, writing, or updating the
user's real repositories and configuration during development or testing. Use
temporary directories, isolated `HOME` values, mocks, and local repositories.

## Required Checks

Run these before committing code changes:

```sh
python3 -m unittest -v
python3 -m py_compile vv.py test_vv.py
git diff --check
```

Also validate behavior in a fresh CLI process when changing argument parsing,
configuration loading, environment handling, or exit codes. Tests must not need
network access or optional VCS programs.

## Change Rules

- Preserve unrelated user changes already present in the worktree.
- Keep changes small, reversible, and limited to one concern per commit.
- Add regression tests for bug fixes and tests for success and failure paths.
- Fail safely when repository state, configuration, or subprocess results are
  uncertain. Never interpret an operational error as a clean tree or success.
- Keep `README.md` and `vv.conf` synchronized with user-visible behavior and
  configuration changes.
- Prefer the standard library and keep new runtime dependencies exceptional.
- Keep worker output coordinated through the main thread so concurrent command
  output does not interleave.
- Treat configured `updatecmd` values as trusted shell commands; do not expand
  shell execution to values that are not explicitly documented as shell input.

## Git Rules

- Stage only files belonging to the current change.
- Create or rewrite commits only when the user has requested or approved it.
- Use an imperative commit subject and wrap commit-message body lines near 80
  columns.
- Include the coding assistant's model identifier in a `Co-Authored-By` trailer
  when the assistant materially contributes to a commit.
- Inspect the staged diff and rerun the required checks before committing.
