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
import stat
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    print("error: PyYAML is required (pip install pyyaml)", file=sys.stderr)
    sys.exit(1)

CONFIG_FILE = Path.home() / ".vv.conf"
DEFAULT_LOG_FILE = Path.home() / ".vv.log"
DEFAULT_BASEDIR = Path.home() / "src"
DEFAULT_JOBS = 4
DEFAULT_FETCH_TIMEOUT = 60  # seconds

_TOP_LEVEL_KEYS = {"basedirs", "fetch_timeout", "include", "jobs", "logfile"}
_BASEDIR_KEYS = {"exclude", "trees"}
_TREE_KEYS = {"remotes", "submodules", "type", "updatecmd"}


@dataclass(frozen=True)
class VcsDriver:
    name: str
    marker: str               # subdirectory/file that identifies this VCS
    dirty_cmd: tuple[str, ...]  # produces output iff repo is dirty
    update_cmd: tuple[str, ...]  # default update command


VCS_DRIVERS: list[VcsDriver] = [
    VcsDriver("git", ".git",  ("git", "status", "--untracked-files=no", "--porcelain"), ("git", "merge", "--ff-only")),
    VcsDriver("hg",  ".hg",   ("hg",  "status", "-mard"),                               ("hg",  "pull", "-u")),
    VcsDriver("svn", ".svn",  ("svn", "status", "-q"),                                  ("svn", "update")),
    VcsDriver("cvs", "CVS",   ("cvs", "-n", "-q", "update"),                             ("cvs", "update")),
]
_VCS_BY_NAME: dict[str, VcsDriver] = {d.name: d for d in VCS_DRIVERS}


class DirtyCheckError(RuntimeError):
    """Raised when a repository's dirty state cannot be determined."""


class ConfigError(ValueError):
    """Raised when ~/.vv.conf cannot be loaded or validated."""


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        with CONFIG_FILE.open() as f:
            config = yaml.safe_load(f)
    except OSError as exc:
        raise ConfigError(f"cannot read {CONFIG_FILE}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {CONFIG_FILE}: {exc}") from exc
    if config is None:
        return {}
    if not isinstance(config, dict):
        raise ConfigError("configuration root must be a mapping")
    return config


def _config_type_error(key: str, expected: str) -> ConfigError:
    return ConfigError(f"{key} must be {expected}")


def _validate_keys(value: dict, allowed: set[str], key: str) -> None:
    unknown = sorted(str(item) for item in value if item not in allowed)
    if unknown:
        raise ConfigError(f"{key} has unknown key: {unknown[0]}")


def _validate_tree_config(value: object, key: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise _config_type_error(key, "a mapping or null")
    _validate_keys(value, _TREE_KEYS, key)

    vcs_type = value.get("type")
    if vcs_type is not None:
        if not isinstance(vcs_type, str):
            raise _config_type_error(f"{key}.type", "a string")
        if vcs_type not in _VCS_BY_NAME:
            supported = ", ".join(sorted(_VCS_BY_NAME))
            raise ConfigError(f"{key}.type must be one of: {supported}")

    remotes = value.get("remotes")
    if remotes is not None:
        if not isinstance(remotes, list) or not all(isinstance(remote, str) for remote in remotes):
            raise _config_type_error(f"{key}.remotes", "a list of strings")

    updatecmd = value.get("updatecmd")
    if updatecmd is not None and not isinstance(updatecmd, str):
        raise _config_type_error(f"{key}.updatecmd", "a string")

    submodules = value.get("submodules")
    if submodules is not None and not isinstance(submodules, bool):
        raise _config_type_error(f"{key}.submodules", "a boolean")


def validate_config(config: dict) -> None:
    """Raise ConfigError if config does not match the supported schema."""
    if not isinstance(config, dict):
        raise ConfigError("configuration root must be a mapping")

    old_keys = []
    if "basedir" in config:
        old_keys.append("basedir")
    if "exclude" in config and isinstance(config["exclude"], list):
        old_keys.append("exclude (top-level list)")
    if "trees" in config and "basedirs" not in config:
        old_keys.append("trees (top-level)")
    if "include" in config and isinstance(config["include"], list):
        old_keys.append("include (list)")
    if old_keys:
        raise ConfigError(
            "~/.vv.conf uses the old config format.\n"
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
            "  See README.md for the full config reference."
        )

    _validate_keys(config, _TOP_LEVEL_KEYS, "configuration")

    for key in ("jobs", "fetch_timeout"):
        value = config.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
            raise _config_type_error(key, "a positive integer")

    logfile = config.get("logfile")
    if logfile is not None and not isinstance(logfile, str):
        raise _config_type_error("logfile", "a string")

    basedirs = config.get("basedirs")
    if basedirs is not None:
        if not isinstance(basedirs, dict):
            raise _config_type_error("basedirs", "a mapping")
        for basedir, section in basedirs.items():
            if not isinstance(basedir, str):
                raise _config_type_error("basedirs keys", "strings")
            section_key = f"basedirs.{basedir}"
            if section is None:
                continue
            if not isinstance(section, dict):
                raise _config_type_error(section_key, "a mapping or null")
            _validate_keys(section, _BASEDIR_KEYS, section_key)

            exclude = section.get("exclude")
            if exclude is not None and (
                not isinstance(exclude, list) or not all(isinstance(name, str) for name in exclude)
            ):
                raise _config_type_error(f"{section_key}.exclude", "a list of strings")

            trees = section.get("trees")
            if trees is not None:
                if not isinstance(trees, dict):
                    raise _config_type_error(f"{section_key}.trees", "a mapping")
                for name, tree_config in trees.items():
                    if not isinstance(name, str):
                        raise _config_type_error(f"{section_key}.trees keys", "strings")
                    _validate_tree_config(tree_config, f"{section_key}.trees.{name}")

    includes = config.get("include")
    if includes is not None:
        if not isinstance(includes, dict):
            raise _config_type_error("include", "a mapping")
        for path, tree_config in includes.items():
            if not isinstance(path, str):
                raise _config_type_error("include keys", "strings")
            _validate_tree_config(tree_config, f"include.{path}")


def save_config(config: dict) -> None:
    """Atomically replace the config, preserving its mode when it exists."""
    try:
        mode = stat.S_IMODE(CONFIG_FILE.stat().st_mode) if CONFIG_FILE.exists() else 0o600
        fd, raw_temp_path = tempfile.mkstemp(
            dir=CONFIG_FILE.parent,
            prefix=f".{CONFIG_FILE.name}.",
        )
    except OSError as exc:
        raise ConfigError(f"cannot prepare {CONFIG_FILE} for writing: {exc}") from exc

    temp_path = Path(raw_temp_path)
    try:
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), mode)
            yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, CONFIG_FILE)
    except Exception as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ConfigError(f"cannot write {CONFIG_FILE}: {exc}") from exc


def get_jobs(config: dict) -> int:
    return int(config.get("jobs") or DEFAULT_JOBS)


def get_fetch_timeout(config: dict) -> int:
    return int(config.get("fetch_timeout") or DEFAULT_FETCH_TIMEOUT)


def get_log_file(config: dict) -> Path:
    raw = config.get("logfile")
    return Path(raw).expanduser() if raw else DEFAULT_LOG_FILE


def get_all_remotes(path: Path) -> list[str]:
    result = subprocess.run(
        ["git", "remote"],
        cwd=path,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    return result.stdout.splitlines()


def git_fetch(path: Path, remote: str, timeout: int) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git", "fetch", "--verbose", remote],
            cwd=path,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"fetch timed out after {timeout}s"
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def get_basedirs(config: dict) -> dict[Path, dict]:
    raw = config.get("basedirs") or {}
    if not raw:
        return {DEFAULT_BASEDIR.resolve(): {}}
    return {Path(k).expanduser().resolve(): (v or {}) for k, v in raw.items()}


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


def is_dirty(path: Path, driver: VcsDriver, ignore_submodules: bool = False) -> bool:
    cmd = list(driver.dirty_cmd)
    if ignore_submodules and driver.name == "git":
        cmd.append("--ignore-submodules=all")
    result = subprocess.run(cmd, cwd=path, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"exit status {result.returncode}"
        raise DirtyCheckError(f"{driver.name} dirty check failed: {detail}")
    return bool(result.stdout.strip())


@dataclass
class Repo:
    path: Path
    driver: VcsDriver
    label: str
    tree_cfg: dict


@dataclass
class DiscoveryResult:
    repos: list[Repo]
    errors: list[str]


def get_include(config: dict) -> dict[Path, dict]:
    raw = config.get("include") or {}
    return {Path(k).expanduser().resolve(): (v or {}) for k, v in raw.items()}


def get_tree_cfg(basedir_section: dict | None, path: Path) -> dict:
    """Look up per-tree config for a repo.

    For basedir repos, basedir_section is the basedir's config dict.
    For include repos, pass None (config is inline in the include dict).
    """
    if basedir_section is None:
        return {}
    trees = basedir_section.get("trees") or {}
    return trees.get(path.name) or {}


def get_repos(config: dict) -> DiscoveryResult:
    """Discover managed repositories and errors in explicitly configured sources."""
    repos: list[Repo] = []
    errors: list[str] = []
    basedir_paths: set[Path] = set()
    has_configured_basedirs = bool(config.get("basedirs"))

    for basedir, section in sorted(get_basedirs(config).items()):
        exclude = set(section.get("exclude") or [])
        if not basedir.is_dir():
            if has_configured_basedirs:
                errors.append(f"basedir does not exist: {basedir}")
            continue
        try:
            children = sorted(basedir.iterdir())
        except OSError as exc:
            errors.append(f"cannot scan basedir {basedir}: {exc}")
            continue
        for p in children:
            if p.is_dir() and p.name not in exclude:
                tree_cfg = get_tree_cfg(section, p)
                driver = get_vcs_driver(tree_cfg, p)
                if driver is not None:
                    canonical_path = p.resolve()
                    if canonical_path not in basedir_paths:
                        repos.append(Repo(canonical_path, driver, p.name, tree_cfg))
                        basedir_paths.add(canonical_path)

    for p, tree_cfg in sorted(get_include(config).items(), key=lambda x: x[0].name):
        if p in basedir_paths:
            continue
        if not p.is_dir():
            errors.append(f"included repository does not exist: {p}")
            continue
        driver = get_vcs_driver(tree_cfg, p)
        if driver is None:
            errors.append(f"no supported VCS found at included repository: {p}")
            continue
        repos.append(Repo(p, driver, str(p), tree_cfg))

    return DiscoveryResult(repos, errors)


def report_discovery_errors(errors: list[str]) -> None:
    for error in errors:
        print(f"error: {error}", file=sys.stderr)


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
        if Path(k).expanduser().resolve() == owner:
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
    discovery = get_repos(config)
    report_discovery_errors(discovery.errors)

    for repo in discovery.repos:
        print(f"{repo.label} ({repo.driver.name})")

    return 1 if discovery.errors else 0


def cmd_dirty(_args: argparse.Namespace) -> int:
    config = load_config()
    validate_config(config)
    discovery = get_repos(config)
    report_discovery_errors(discovery.errors)

    exit_code = 0
    for repo in discovery.repos:
        use_submodules = (
            repo.driver.name == "git"
            and repo.tree_cfg.get("submodules", (repo.path / ".gitmodules").exists())
        )
        try:
            if is_dirty(repo.path, repo.driver, ignore_submodules=use_submodules):
                print(repo.label)
        except (OSError, DirtyCheckError) as exc:
            print(f"{repo.label}: {exc}", file=sys.stderr)
            exit_code = 1

    return 1 if discovery.errors else exit_code


def run_update(path: Path, driver: VcsDriver, updatecmd: str | None = None, timeout: int = DEFAULT_FETCH_TIMEOUT) -> tuple[bool, str]:
    try:
        if updatecmd:
            result = subprocess.run(
                updatecmd, shell=True, cwd=path, capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=timeout,
            )
        else:
            result = subprocess.run(
                list(driver.update_cmd), cwd=path, capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=timeout,
            )
    except subprocess.TimeoutExpired:
        return False, f"update timed out after {timeout}s"
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def run_submodule_update(path: Path, timeout: int = DEFAULT_FETCH_TIMEOUT) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git", "submodule", "update", "--init", "--recursive"],
            cwd=path,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"submodule update timed out after {timeout}s"
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def write_log(log_file: Path, entries: list[tuple[str, str, str | None]]) -> None:
    """Write the most recent update run to the log file.

    Each entry is (tree_name, status, detail) where detail may be None.
    Status values: 'ok', 'dirty', 'failed'.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log_file.open("w") as f:
        f.write(f"[{timestamp}]\n")
        for name, status, detail in entries:
            f.write(f"  {name}: {status}\n")
            if detail:
                for line in detail.splitlines():
                    f.write(f"    {line}\n")
        f.write("\n")


def spawn_shell(path: Path) -> None:
    shell = os.environ.get("SHELL", "/bin/sh")
    print(f"  spawning {shell} in {path} — exit to retry update")
    subprocess.run([shell], cwd=path)


def interactive_recovery_enabled(args: argparse.Namespace) -> bool:
    """Return whether failed updates may open interactive recovery shells."""
    return (
        not getattr(args, "no_interactive", False)
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    )


def _update_worker(repo: Repo, timeout: int) -> tuple[str, str | None, str | None]:
    """Thread worker: fetch all remotes then fast-forward merge.

    Returns (status, display_output, log_detail) where display_output is
    shown to the user and log_detail is the full output written to the log.
    """
    use_submodules = (
        repo.driver.name == "git"
        and repo.tree_cfg.get("submodules", (repo.path / ".gitmodules").exists())
    )

    if is_dirty(repo.path, repo.driver, ignore_submodules=use_submodules):
        return "dirty", None, None

    fetch_outputs = []
    errors = []
    if repo.driver.name == "git":
        fetch_remotes = repo.tree_cfg.get("remotes") or get_all_remotes(repo.path)
        for remote in fetch_remotes:
            ok, out = git_fetch(repo.path, remote, timeout)
            if not ok and out:
                errors.append(f"fetch {remote}: {out}")
            elif out:
                fetch_outputs.append(f"fetch {remote}: {out}")

    if errors:
        detail = "\n".join(fetch_outputs + errors)
        return "failed", "\n".join(errors), detail

    ok, update_out = run_update(repo.path, repo.driver, repo.tree_cfg.get("updatecmd"), timeout)

    if ok and use_submodules:
        sub_ok, sub_out = run_submodule_update(repo.path, timeout)
        if not sub_ok:
            ok = False
        sub_parts = [sub_out] if sub_out else []
    else:
        sub_parts = []

    status = "ok" if ok else "failed"
    display_parts = errors + ([update_out] if update_out else []) + sub_parts
    display = "\n".join(display_parts) or None
    log_parts = fetch_outputs + display_parts
    log_detail = "\n".join(log_parts) or None
    return status, display, log_detail


def cmd_update(args: argparse.Namespace) -> int:
    config = load_config()
    validate_config(config)
    jobs = get_jobs(config)
    timeout = get_fetch_timeout(config)

    discovery = get_repos(config)
    report_discovery_errors(discovery.errors)
    repos = discovery.repos
    if not repos:
        print("no repositories found")
        return 1 if discovery.errors else 0

    log_entries: list[tuple[str, str, str | None]] = []
    failures: list[tuple[Repo, str | None]] = []
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = {executor.submit(_update_worker, repo, timeout): repo for repo in repos}
        for future in as_completed(futures):
            repo = futures[future]
            try:
                status, display, log_detail = future.result()
            except Exception as exc:
                status = "failed"
                display = f"unexpected error: {exc}"
                log_detail = display
            if status == "dirty":
                print(f"{repo.label}: skipped (dirty)")
                log_entries.append((repo.label, "dirty", None))
            elif status == "ok":
                print(f"{repo.label}: {display or 'ok'}")
                log_entries.append((repo.label, "ok", log_detail))
            else:
                print(f"{repo.label}: update failed\n  {display}", file=sys.stderr)
                failures.append((repo, log_detail))

    exit_code = 1 if discovery.errors else 0
    if failures and not interactive_recovery_enabled(args):
        if getattr(args, "no_interactive", False):
            reason = "--no-interactive was specified"
        else:
            reason = "input and output are not attached to a terminal"
        print(f"interactive recovery skipped: {reason}", file=sys.stderr)
        for repo, log_detail in failures:
            log_entries.append((repo.label, "failed", log_detail))
        exit_code = 1
        failures = []

    for repo, _initial_log_detail in failures:
        spawn_shell(repo.path)
        try:
            status, display, log_detail = _update_worker(repo, timeout)
        except Exception as exc:
            status = "failed"
            display = f"unexpected error: {exc}"
            log_detail = display
        if status == "ok":
            print(f"{repo.label}: {display or 'ok'}")
            log_entries.append((repo.label, "ok", log_detail))
        elif status == "dirty":
            print(f"{repo.label}: retry skipped (dirty)", file=sys.stderr)
            log_entries.append((repo.label, "dirty", None))
            exit_code = 1
        else:
            print(f"{repo.label}: update failed\n  {display}", file=sys.stderr)
            log_entries.append((repo.label, "failed", log_detail))
            exit_code = 1

    if log_entries:
        write_log(get_log_file(config), log_entries)

    return exit_code


def main() -> int:
    os.environ["GIT_TERMINAL_PROMPT"] = "0"

    parser = argparse.ArgumentParser(
        prog="vv",
        description="Keep source trees up to date with upstream",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("list", help="List managed repositories")
    p_update = sub.add_parser("update", help="Update all clean repositories (default)")
    p_update.add_argument(
        "--no-interactive",
        action="store_true",
        help="Do not open recovery shells for failed updates",
    )
    sub.add_parser("dirty", help="List repositories with uncommitted changes")

    p_include = sub.add_parser("include", help="Explicitly include a repository")
    p_include.add_argument("path", help="Path to the repository")

    p_exclude = sub.add_parser("exclude", help="Add a basedir subdirectory to the exclude list")
    p_exclude.add_argument("path", help="Directory name or path to exclude")

    args = parser.parse_args()
    if args.command is None:
        args.command = "update"

    dispatch = {
        "list": cmd_list,
        "update": cmd_update,
        "dirty": cmd_dirty,
        "include": cmd_include,
        "exclude": cmd_exclude,
    }
    try:
        return dispatch[args.command](args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
