#!/usr/bin/env python3
# Copyright (c) 2013, 2014, 2026 Kyle McMartin <jkkm@jkkm.org>
#
# Permission to use, copy, modify, and distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
# WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
# ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
# WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
# ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
# OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
"""vv - keep source trees up to date with upstream"""

import os
import sys
import subprocess
import argparse
import queue
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    print("error: PyYAML is required (pip install pyyaml)", file=sys.stderr)
    sys.exit(1)

CONFIG_FILE = Path.home() / ".vv.conf"
LOG_FILE = Path.home() / ".vv.log"
DEFAULT_BASEDIR = Path.home() / "src"
DEFAULT_REMOTE = "origin"
DEFAULT_JOBS = 4


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    with CONFIG_FILE.open() as f:
        return yaml.safe_load(f) or {}


def save_config(config: dict) -> None:
    with CONFIG_FILE.open("w") as f:
        yaml.dump(config, f, default_flow_style=False)


def get_jobs(config: dict) -> int:
    return int(config.get("jobs") or DEFAULT_JOBS)


def get_all_remotes(path: Path) -> list[str]:
    result = subprocess.run(
        ["git", "remote"],
        cwd=path,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def get_branch_remote(path: Path) -> str | None:
    branch_result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
    )
    branch = branch_result.stdout.strip()
    if not branch or branch == "HEAD":
        return None
    remote_result = subprocess.run(
        ["git", "config", f"branch.{branch}.remote"],
        cwd=path,
        capture_output=True,
        text=True,
    )
    if remote_result.returncode != 0:
        return None
    return remote_result.stdout.strip() or None


def git_fetch(path: Path, remote: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["git", "fetch", remote],
        cwd=path,
        capture_output=True,
        text=True,
    )
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def get_pull_remote(config: dict, path: Path) -> str:
    tree_cfg = get_tree_cfg(config, path)
    if tree_cfg.get("remote"):
        return tree_cfg["remote"]
    branch_remote = get_branch_remote(path)
    if branch_remote:
        return branch_remote
    return config.get("remote") or DEFAULT_REMOTE


def get_basedir(config: dict) -> Path:
    raw = config.get("basedir")
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_BASEDIR


def is_git_repo(path: Path) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=path,
        capture_output=True,
    )
    return result.returncode == 0


def is_dirty(path: Path) -> bool:
    """True if the repo has staged or unstaged changes to tracked files."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=path,
        capture_output=True,
        text=True,
    )
    tracked_changes = [
        line for line in result.stdout.splitlines()
        if not line.startswith("??")
    ]
    return bool(tracked_changes)


def get_exclude(config: dict) -> set[str]:
    raw = config.get("exclude") or []
    return set(raw)


def get_include(config: dict) -> list[Path]:
    raw = config.get("include") or []
    return [Path(p).expanduser() for p in raw]


def get_tree_cfg(config: dict, path: Path) -> dict:
    trees = config.get("trees") or {}
    resolved = path.resolve()
    for key, val in trees.items():
        if key.startswith("/") or key.startswith("~"):
            try:
                if Path(key).expanduser().resolve() == resolved:
                    return val or {}
            except Exception:
                continue
    return trees.get(path.name) or {}


def cmd_include(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser().resolve()
    if not path.exists():
        print(f"error: path does not exist: {path}", file=sys.stderr)
        return 1
    if not is_git_repo(path):
        print(f"error: not a git repository: {path}", file=sys.stderr)
        return 1
    config = load_config()
    include = list(config.get("include") or [])
    if str(path) in include:
        print(f"already included: {path}")
        return 0
    include.append(str(path))
    config["include"] = include
    save_config(config)
    print(f"included: {path}")
    return 0


def cmd_exclude(args: argparse.Namespace) -> int:
    config = load_config()
    basedir = get_basedir(config)

    # Accept either a bare name or a full/relative path; resolve to check parentage
    candidate = Path(args.path).expanduser()
    if not candidate.is_absolute():
        candidate = (basedir / candidate).resolve()
    else:
        candidate = candidate.resolve()

    if candidate.parent != basedir.resolve():
        print(
            f"error: {candidate.name!r} is not a direct subdirectory of basedir ({basedir})",
            file=sys.stderr,
        )
        return 1

    exclude = list(config.get("exclude") or [])
    if candidate.name in exclude:
        print(f"already excluded: {candidate.name}")
        return 0

    exclude.append(candidate.name)
    config["exclude"] = exclude
    save_config(config)
    print(f"excluded: {candidate.name}")
    return 0


def cmd_dirty(_args: argparse.Namespace) -> int:
    config = load_config()
    basedir = get_basedir(config)
    exclude = get_exclude(config)

    if not basedir.is_dir():
        print(f"error: basedir does not exist: {basedir}", file=sys.stderr)
        return 1

    for path in sorted(p for p in basedir.iterdir() if p.is_dir()):
        if path.name in exclude:
            continue
        if not is_git_repo(path):
            continue
        if is_dirty(path):
            print(path.name)

    return 0


def git_pull(path: Path, remote: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["git", "pull", remote],
        cwd=path,
        capture_output=True,
        text=True,
    )
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def write_log(entries: list[tuple[str, str, str | None]]) -> None:
    """Append an update run to the log file.

    Each entry is (tree_name, status, detail) where detail may be None.
    Status values: 'ok', 'dirty', 'failed'.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with LOG_FILE.open("a") as f:
        f.write(f"[{timestamp}]\n")
        for name, status, detail in entries:
            f.write(f"  {name}: {status}\n")
            if detail:
                for line in detail.splitlines():
                    f.write(f"    {line}\n")
        f.write("\n")


def spawn_shell(path: Path) -> None:
    shell = os.environ.get("SHELL", "/bin/sh")
    print(f"  spawning {shell} in {path} — exit to retry pull")
    subprocess.run([shell], cwd=path)


def _update_worker(path: Path, config: dict) -> tuple[str, str | None]:
    """Thread worker: fetch all remotes then pull. Returns (status, output)."""
    if is_dirty(path):
        return "dirty", None

    tree_cfg = get_tree_cfg(config, path)
    fetch_remotes = tree_cfg.get("remotes") or get_all_remotes(path)

    errors = []
    for remote in fetch_remotes:
        ok, out = git_fetch(path, remote)
        if not ok and out:
            errors.append(f"fetch {remote}: {out}")

    pull_remote = get_pull_remote(config, path)
    ok, pull_out = git_pull(path, pull_remote)

    parts = errors + ([pull_out] if pull_out else [])
    return ("ok" if ok else "failed"), ("\n".join(parts) or None)


def cmd_update(_args: argparse.Namespace) -> int:
    config = load_config()
    basedir = get_basedir(config)
    exclude = get_exclude(config)
    jobs = get_jobs(config)

    if not basedir.is_dir():
        print(f"error: basedir does not exist: {basedir}", file=sys.stderr)
        return 1

    basedir_repos = sorted(
        p for p in basedir.iterdir()
        if p.is_dir() and p.name not in exclude and is_git_repo(p)
    )
    included_repos = [p for p in get_include(config) if p not in basedir_repos]
    repos = basedir_repos + included_repos
    if not repos:
        print(f"no repositories found in {basedir}")
        return 0

    # Workers push results into a queue; main thread prints them as they arrive
    result_queue: queue.SimpleQueue = queue.SimpleQueue()

    def worker(path: Path, config: dict) -> None:
        status, output = _update_worker(path, config)
        result_queue.put((path, status, output))

    log_entries: list[tuple[str, str, str | None]] = []
    failures: list[Path] = []
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        for path in repos:
            executor.submit(worker, path, config)
        for _ in repos:
            path, status, output = result_queue.get()
            if status == "dirty":
                print(f"{path.name}: skipped (dirty)")
                log_entries.append((path.name, "dirty", None))
            elif status == "ok":
                print(f"{path.name}: {output or 'ok'}")
                log_entries.append((path.name, "ok", output))
            else:
                print(f"{path.name}: pull failed\n  {output}", file=sys.stderr)
                failures.append(path)

    # Handle failures interactively once all pulls are done
    exit_code = 0
    for path in failures:
        spawn_shell(path)
        pull_remote = get_pull_remote(config, path)
        success, output = git_pull(path, pull_remote)
        if success:
            print(f"{path.name}: {output or 'ok'}")
            log_entries.append((path.name, "ok", output or None))
        else:
            print(f"{path.name}: pull failed\n  {output}", file=sys.stderr)
            log_entries.append((path.name, "failed", output or None))
            exit_code = 1

    if log_entries:
        write_log(log_entries)

    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="vv",
        description="Keep source trees up to date with upstream",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("update", help="Pull all clean repositories")
    sub.add_parser("dirty", help="List repositories with uncommitted changes")

    p_include = sub.add_parser("include", help="Explicitly include a repository")
    p_include.add_argument("path", help="Path to the git repository")

    p_exclude = sub.add_parser("exclude", help="Add a basedir subdirectory to the exclude list")
    p_exclude.add_argument("path", help="Directory name or path to exclude")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return 1

    dispatch = {
        "update": cmd_update,
        "dirty": cmd_dirty,
        "include": cmd_include,
        "exclude": cmd_exclude,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
