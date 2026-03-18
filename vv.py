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
    update_cmd: tuple[str, ...]  # default update command


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


def validate_config(config: dict) -> None:
    """Exit with a clear error if the config uses the old single-basedir format."""
    old_keys = []
    if "basedir" in config:
        old_keys.append("basedir")
    if "exclude" in config and isinstance(config["exclude"], list):
        old_keys.append("exclude (top-level list)")
    if "trees" in config and not any(
        isinstance(config.get("basedirs", {}).get(k), dict)
        for k in (config.get("basedirs") or {})
    ):
        if "basedirs" not in config:
            old_keys.append("trees (top-level)")
    if "include" in config and isinstance(config["include"], list):
        old_keys.append("include (list)")
    if not old_keys:
        return
    print(
        "error: ~/.vv.conf uses the old config format.\n"
        f"  Detected old-format keys: {', '.join(old_keys)}\n"
        "\n"
        "  The new format uses 'basedirs' (plural) with per-basedir settings:\n"
        "\n"
        "    basedirs:\n"
        "      ~/src:\n"
        "        exclude: [dirname]\n"
        "        trees:\n"
        "          reponame:\n"
        "            remotes: [upstream, origin]\n"
        "    include:\n"
        "      ~/other/repo:\n"
        "        type: git\n"
        "\n"
        "  See README.md for the full config reference.",
        file=sys.stderr,
    )
    sys.exit(1)


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
        ["git", "fetch", "--verbose", remote],
        cwd=path,
        capture_output=True,
        text=True,
    )
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def get_basedirs(config: dict) -> dict[Path, dict]:
    raw = config.get("basedirs") or {}
    if not raw:
        return {DEFAULT_BASEDIR: {}}
    return {Path(k).expanduser(): (v or {}) for k, v in raw.items()}


def detect_vcs(path: Path) -> VcsDriver | None:
    for driver in VCS_DRIVERS:
        if (path / driver.marker).exists():
            return driver
    return None


def get_vcs_driver(tree_cfg: dict, path: Path) -> VcsDriver | None:
    vcs_name = tree_cfg.get("type")
    if vcs_name:
        return _VCS_BY_NAME.get(vcs_name)
    return detect_vcs(path)


def is_dirty(path: Path, driver: VcsDriver) -> bool:
    result = subprocess.run(
        list(driver.dirty_cmd), cwd=path, capture_output=True, text=True
    )
    return bool(result.stdout.strip())


@dataclass
class Repo:
    path: Path
    driver: VcsDriver
    label: str
    tree_cfg: dict


def get_include(config: dict) -> dict[Path, dict]:
    raw = config.get("include") or {}
    return {Path(k).expanduser(): (v or {}) for k, v in raw.items()}


def require_basedirs(config: dict) -> dict[Path, dict] | None:
    """Return basedirs dict if all exist, or print an error and return None."""
    basedirs = get_basedirs(config)
    if not basedirs:
        print("error: no basedirs configured in ~/.vv.conf", file=sys.stderr)
        return None
    for path in basedirs:
        if not path.is_dir():
            print(f"error: basedir does not exist: {path}", file=sys.stderr)
            return None
    return basedirs


def get_tree_cfg(basedir_section: dict | None, path: Path) -> dict:
    """Look up per-tree config for a repo.

    For basedir repos, basedir_section is the basedir's config dict.
    For include repos, pass None (config is inline in the include dict).
    """
    if basedir_section is None:
        return {}
    trees = basedir_section.get("trees") or {}
    return trees.get(path.name) or {}


def get_repos(config: dict) -> list[Repo]:
    """Return all managed repos from all basedirs plus includes."""
    repos: list[Repo] = []
    basedir_paths: set[Path] = set()

    for basedir, section in sorted(get_basedirs(config).items()):
        exclude = set(section.get("exclude") or [])
        if not basedir.is_dir():
            continue
        for p in sorted(basedir.iterdir()):
            if p.is_dir() and p.name not in exclude:
                tree_cfg = get_tree_cfg(section, p)
                driver = get_vcs_driver(tree_cfg, p)
                if driver is not None:
                    repos.append(Repo(p, driver, p.name, tree_cfg))
                    basedir_paths.add(p)

    for p, tree_cfg in sorted(get_include(config).items(), key=lambda x: x[0].name):
        if p not in basedir_paths and p.is_dir():
            driver = get_vcs_driver(tree_cfg, p)
            if driver is not None:
                repos.append(Repo(p, driver, str(p), tree_cfg))

    return repos


def cmd_include(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser().resolve()
    if not path.exists():
        print(f"error: path does not exist: {path}", file=sys.stderr)
        return 1
    if detect_vcs(path) is None:
        print(f"error: no supported VCS found at: {path}", file=sys.stderr)
        return 1
    config = load_config()
    validate_config(config)
    include = config.get("include") or {}
    if str(path) in include:
        print(f"already included: {path}")
        return 0
    include[str(path)] = None
    config["include"] = include
    save_config(config)
    print(f"included: {path}")
    return 0


def cmd_exclude(args: argparse.Namespace) -> int:
    config = load_config()
    validate_config(config)

    basedirs = get_basedirs(config)
    candidate_path = Path(args.path).expanduser()

    # Find which basedir this path belongs to
    owner = None
    candidate = None
    for basedir in basedirs:
        if candidate_path.is_absolute():
            full = candidate_path.resolve()
        else:
            full = (basedir / candidate_path).resolve()
        if full.parent == basedir.resolve():
            owner = basedir
            candidate = full
            break

    if owner is None:
        print(
            f"error: {args.path!r} is not a direct subdirectory of any configured basedir",
            file=sys.stderr,
        )
        return 1

    # Get the raw config key for this basedir
    raw_basedirs = config.get("basedirs") or {}
    raw_key = None
    for k in raw_basedirs:
        if Path(k).expanduser() == owner:
            raw_key = k
            break

    if raw_key is None:
        print(f"error: basedir {owner} not found in config", file=sys.stderr)
        return 1

    section = raw_basedirs[raw_key] or {}
    exclude = list(section.get("exclude") or [])
    if candidate.name in exclude:
        print(f"already excluded: {candidate.name}")
        return 0

    exclude.append(candidate.name)
    section["exclude"] = exclude
    raw_basedirs[raw_key] = section
    config["basedirs"] = raw_basedirs
    save_config(config)
    print(f"excluded: {candidate.name} (from {owner})")
    return 0


def cmd_list(_args: argparse.Namespace) -> int:
    config = load_config()
    validate_config(config)
    if require_basedirs(config) is None:
        return 1

    for repo in get_repos(config):
        print(f"{repo.label} ({repo.driver.name})")

    return 0


def cmd_dirty(_args: argparse.Namespace) -> int:
    config = load_config()
    validate_config(config)
    if require_basedirs(config) is None:
        return 1

    for repo in get_repos(config):
        if is_dirty(repo.path, repo.driver):
            print(repo.label)

    return 0


def run_update(path: Path, driver: VcsDriver, updatecmd: str | None = None) -> tuple[bool, str]:
    if updatecmd:
        result = subprocess.run(
            updatecmd, shell=True, cwd=path, capture_output=True, text=True
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


def _update_worker(repo: Repo) -> tuple[str, str | None]:
    """Thread worker: fetch all remotes then pull. Returns (status, output)."""
    if is_dirty(repo.path, repo.driver):
        return "dirty", None

    fetch_outputs = []
    errors = []
    if repo.driver.name == "git":
        fetch_remotes = repo.tree_cfg.get("remotes") or get_all_remotes(repo.path)
        for remote in fetch_remotes:
            ok, out = git_fetch(repo.path, remote)
            if not ok and out:
                errors.append(f"fetch {remote}: {out}")
            elif out:
                fetch_outputs.append(f"fetch {remote}: {out}")

    ok, pull_out = run_update(repo.path, repo.driver, repo.tree_cfg.get("updatecmd"))

    parts = fetch_outputs + errors + ([pull_out] if pull_out else [])
    return ("ok" if ok else "failed"), ("\n".join(parts) or None)


def cmd_update(_args: argparse.Namespace) -> int:
    config = load_config()
    validate_config(config)
    if require_basedirs(config) is None:
        return 1
    jobs = get_jobs(config)

    repos = get_repos(config)
    if not repos:
        print("no repositories found")
        return 0

    result_queue: queue.SimpleQueue = queue.SimpleQueue()

    def worker(repo: Repo) -> None:
        status, output = _update_worker(repo)
        result_queue.put((repo, status, output))

    log_entries: list[tuple[str, str, str | None]] = []
    failures: list[Repo] = []
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        for repo in repos:
            executor.submit(worker, repo)
        for _ in repos:
            repo, status, output = result_queue.get()
            if status == "dirty":
                print(f"{repo.label}: skipped (dirty)")
                log_entries.append((repo.label, "dirty", None))
            elif status == "ok":
                print(f"{repo.label}: {output or 'ok'}")
                log_entries.append((repo.label, "ok", output))
            else:
                print(f"{repo.label}: pull failed\n  {output}", file=sys.stderr)
                failures.append(repo)

    exit_code = 0
    for repo in failures:
        spawn_shell(repo.path)
        driver = detect_vcs(repo.path)
        if driver is None:
            log_entries.append((repo.label, "failed", "VCS marker gone"))
            exit_code = 1
            continue
        success, output = run_update(repo.path, driver, repo.tree_cfg.get("updatecmd"))
        if success:
            print(f"{repo.label}: {output or 'ok'}")
            log_entries.append((repo.label, "ok", output or None))
        else:
            print(f"{repo.label}: pull failed\n  {output}", file=sys.stderr)
            log_entries.append((repo.label, "failed", output or None))
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
    sub.add_parser("list", help="List managed repositories")
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
        "list": cmd_list,
        "update": cmd_update,
        "dirty": cmd_dirty,
        "include": cmd_include,
        "exclude": cmd_exclude,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
