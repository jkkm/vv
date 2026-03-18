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
from dataclasses import dataclass
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
DEFAULT_JOBS = 4


@dataclass(frozen=True)
class VcsDriver:
    name: str
    marker: str               # subdirectory/file that identifies this VCS
    dirty_cmd: tuple[str, ...]  # produces output iff repo is dirty
    update_cmd: tuple[str, ...]  # default update command (no pullcmd set)


VCS_DRIVERS: list[VcsDriver] = [
    VcsDriver("git", ".git",  ("git", "status", "--untracked-files=no", "--porcelain"), ("git", "pull")),
    VcsDriver("hg",  ".hg",   ("hg",  "status", "-mard"),                               ("hg",  "pull", "-u")),
    VcsDriver("svn", ".svn",  ("svn", "status", "-q"),                                  ("svn", "update")),
    VcsDriver("cvs", "CVS",   ("cvs", "-n", "-q", "update"),                             ("cvs", "update")),
]
_VCS_BY_NAME: dict[str, VcsDriver] = {d.name: d for d in VCS_DRIVERS}


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


def git_fetch(path: Path, remote: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["git", "fetch", remote],
        cwd=path,
        capture_output=True,
        text=True,
    )
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def get_basedir(config: dict) -> Path:
    raw = config.get("basedir")
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_BASEDIR


def detect_vcs(path: Path) -> VcsDriver | None:
    for driver in VCS_DRIVERS:
        if (path / driver.marker).exists():
            return driver
    return None


def get_vcs_driver(config: dict, path: Path) -> VcsDriver | None:
    vcs_name = get_tree_cfg(config, path).get("type")
    if vcs_name:
        return _VCS_BY_NAME.get(vcs_name)
    return detect_vcs(path)


def is_dirty(path: Path, driver: VcsDriver) -> bool:
    result = subprocess.run(
        list(driver.dirty_cmd), cwd=path, capture_output=True, text=True
    )
    return bool(result.stdout.strip())


def get_exclude(config: dict) -> set[str]:
    raw = config.get("exclude") or []
    return set(raw)


def get_include(config: dict) -> list[Path]:
    raw = config.get("include") or []
    return [Path(p).expanduser() for p in raw]


def repo_label(path: Path, basedir: Path) -> str:
    """Short name for basedir repos, full path for included repos."""
    if path.parent == basedir:
        return path.name
    return str(path)


def get_repos(config: dict) -> tuple[list[Path], list[Path]]:
    """Return (basedir_repos, included_repos), filtered and sorted."""
    basedir = get_basedir(config)
    exclude = get_exclude(config)
    basedir_repos = sorted(
        p for p in basedir.iterdir()
        if p.is_dir() and p.name not in exclude and get_vcs_driver(config, p) is not None
    )
    included_repos = sorted(
        (
            p for p in get_include(config)
            if p not in basedir_repos and p.is_dir() and get_vcs_driver(config, p) is not None
        ),
        key=lambda p: p.name,
    )
    return basedir_repos, included_repos


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
    if detect_vcs(path) is None:
        print(f"error: no supported VCS found at: {path}", file=sys.stderr)
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

    if not basedir.is_dir():
        print(f"error: basedir does not exist: {basedir}", file=sys.stderr)
        return 1

    basedir_repos, included_repos = get_repos(config)
    for path in basedir_repos:
        driver = get_vcs_driver(config, path)
        if is_dirty(path, driver):
            print(path.name)

    for path in included_repos:
        driver = get_vcs_driver(config, path)
        if is_dirty(path, driver):
            print(path)

    return 0


def run_pull(path: Path, driver: VcsDriver, pullcmd: str | None = None) -> tuple[bool, str]:
    if pullcmd:
        result = subprocess.run(
            pullcmd, shell=True, cwd=path, capture_output=True, text=True
        )
    else:
        result = subprocess.run(
            list(driver.update_cmd), cwd=path, capture_output=True, text=True
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
    tree_cfg = get_tree_cfg(config, path)
    driver = get_vcs_driver(config, path)
    if is_dirty(path, driver):
        return "dirty", None

    errors = []
    if driver.name == "git":
        fetch_remotes = tree_cfg.get("remotes") or get_all_remotes(path)
        for remote in fetch_remotes:
            ok, out = git_fetch(path, remote)
            if not ok and out:
                errors.append(f"fetch {remote}: {out}")

    ok, pull_out = run_pull(path, driver, tree_cfg.get("pullcmd"))

    parts = errors + ([pull_out] if pull_out else [])
    return ("ok" if ok else "failed"), ("\n".join(parts) or None)


def cmd_update(_args: argparse.Namespace) -> int:
    config = load_config()
    basedir = get_basedir(config)
    jobs = get_jobs(config)

    if not basedir.is_dir():
        print(f"error: basedir does not exist: {basedir}", file=sys.stderr)
        return 1

    basedir_repos, included_repos = get_repos(config)
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
            label = repo_label(path, basedir)
            if status == "dirty":
                print(f"{label}: skipped (dirty)")
                log_entries.append((label, "dirty", None))
            elif status == "ok":
                print(f"{label}: {output or 'ok'}")
                log_entries.append((label, "ok", output))
            else:
                print(f"{label}: pull failed\n  {output}", file=sys.stderr)
                failures.append(path)

    # Handle failures interactively once all pulls are done
    exit_code = 0
    for path in failures:
        label = repo_label(path, basedir)
        spawn_shell(path)
        driver = get_vcs_driver(config, path)
        if driver is None:
            log_entries.append((label, "failed", "VCS marker gone"))
            exit_code = 1
            continue
        pullcmd = get_tree_cfg(config, path).get("pullcmd")
        success, output = run_pull(path, driver, pullcmd)
        if success:
            print(f"{label}: {output or 'ok'}")
            log_entries.append((label, "ok", output or None))
        else:
            print(f"{label}: pull failed\n  {output}", file=sys.stderr)
            log_entries.append((label, "failed", output or None))
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
    p_include.add_argument("path", help="Path to the repository")

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
