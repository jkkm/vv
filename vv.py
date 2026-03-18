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


def get_remote(config: dict, tree_name: str | None = None) -> str:
    if tree_name:
        tree_cfg = (config.get("trees") or {}).get(tree_name) or {}
        if tree_cfg.get("remote"):
            return tree_cfg["remote"]
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


def _pull_worker(path: Path, remote: str) -> tuple[str, str | None]:
    """Thread worker: returns (status, output). Does not print anything."""
    if is_dirty(path):
        return "dirty", None
    success, output = git_pull(path, remote)
    return ("ok" if success else "failed"), (output or None)


def cmd_update(_args: argparse.Namespace) -> int:
    config = load_config()
    basedir = get_basedir(config)
    exclude = get_exclude(config)
    jobs = get_jobs(config)

    if not basedir.is_dir():
        print(f"error: basedir does not exist: {basedir}", file=sys.stderr)
        return 1

    repos = sorted(
        p for p in basedir.iterdir()
        if p.is_dir() and p.name not in exclude and is_git_repo(p)
    )
    if not repos:
        print(f"no repositories found in {basedir}")
        return 0

    # Workers push results into a queue; main thread prints them as they arrive
    result_queue: queue.SimpleQueue = queue.SimpleQueue()

    def worker(path: Path, remote: str) -> None:
        status, output = _pull_worker(path, remote)
        result_queue.put((path, status, output))

    log_entries: list[tuple[str, str, str | None]] = []
    failures: list[Path] = []
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        for path in repos:
            executor.submit(worker, path, get_remote(config, path.name))
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
        remote = get_remote(config, path.name)
        success, output = git_pull(path, remote)
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
    sub.add_parser("update", help="Pull all clean repositories under basedir")
    sub.add_parser("dirty", help="List repositories with uncommitted changes")

    p_exclude = sub.add_parser("exclude", help="Add a directory to the exclude list")
    p_exclude.add_argument("path", help="Directory name or path to exclude")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return 1

    dispatch = {
        "update": cmd_update,
        "dirty": cmd_dirty,
        "exclude": cmd_exclude,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
