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

import sys
import subprocess
import argparse
from pathlib import Path

try:
    import yaml
except ImportError:
    print("error: PyYAML is required (pip install pyyaml)", file=sys.stderr)
    sys.exit(1)

CONFIG_FILE = Path.home() / ".vv.conf"
DEFAULT_BASEDIR = Path.home() / "src"


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    with CONFIG_FILE.open() as f:
        return yaml.safe_load(f) or {}


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


def cmd_update(_args: argparse.Namespace) -> int:
    config = load_config()
    basedir = get_basedir(config)
    exclude = get_exclude(config)

    if not basedir.is_dir():
        print(f"error: basedir does not exist: {basedir}", file=sys.stderr)
        return 1

    subdirs = sorted(p for p in basedir.iterdir() if p.is_dir())
    if not subdirs:
        print(f"no subdirectories found in {basedir}")
        return 0

    exit_code = 0
    for path in subdirs:
        if path.name in exclude:
            continue
        if not is_git_repo(path):
            continue
        if is_dirty(path):
            print(f"{path.name}: skipped (dirty)")
            continue
        result = subprocess.run(
            ["git", "pull", "origin"],
            cwd=path,
            capture_output=True,
            text=True,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode == 0:
            print(f"{path.name}: {output or 'ok'}")
        else:
            print(f"{path.name}: pull failed\n  {output}", file=sys.stderr)
            exit_code = 1

    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="vv",
        description="Keep source trees up to date with upstream",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("update", help="Pull all clean repositories under basedir")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return 1

    return cmd_update(args)


if __name__ == "__main__":
    sys.exit(main())
