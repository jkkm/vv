import argparse
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import call, patch

import yaml

import vv


class ConfigValidationTests(unittest.TestCase):
    def test_accepts_complete_valid_configuration(self):
        config = {
            "jobs": 8,
            "fetch_timeout": 120,
            "logfile": "~/vv.log",
            "basedirs": {
                "~/src": {
                    "exclude": ["ignored"],
                    "trees": {
                        "git-tree": {
                            "type": "git",
                            "remotes": ["upstream", "origin"],
                            "updatecmd": "make update",
                            "submodules": False,
                        },
                        "defaults": None,
                    },
                },
                "~/work": None,
            },
            "include": {
                "~/standalone": {"type": "hg"},
                "~/defaults": None,
            },
        }

        vv.validate_config(config)

    def test_accepts_empty_configuration(self):
        vv.validate_config({})

    def test_rejects_invalid_values_with_their_config_path(self):
        cases = [
            ([], "configuration root must be a mapping"),
            ({"unknown": True}, "configuration has unknown key: unknown"),
            ({"jobs": 0}, "jobs must be a positive integer"),
            ({"jobs": -1}, "jobs must be a positive integer"),
            ({"jobs": True}, "jobs must be a positive integer"),
            ({"jobs": "four"}, "jobs must be a positive integer"),
            ({"fetch_timeout": 0}, "fetch_timeout must be a positive integer"),
            ({"fetch_timeout": 1.5}, "fetch_timeout must be a positive integer"),
            ({"logfile": 3}, "logfile must be a string"),
            ({"basedirs": []}, "basedirs must be a mapping"),
            ({"basedirs": {1: {}}}, "basedirs keys must be strings"),
            ({"basedirs": {"~/src": []}}, "basedirs.~/src must be a mapping or null"),
            (
                {"basedirs": {"~/src": {"unknown": True}}},
                "basedirs.~/src has unknown key: unknown",
            ),
            (
                {"basedirs": {"~/src": {"exclude": "repo"}}},
                "basedirs.~/src.exclude must be a list of strings",
            ),
            (
                {"basedirs": {"~/src": {"exclude": ["repo", 1]}}},
                "basedirs.~/src.exclude must be a list of strings",
            ),
            (
                {"basedirs": {"~/src": {"trees": []}}},
                "basedirs.~/src.trees must be a mapping",
            ),
            (
                {"basedirs": {"~/src": {"trees": {1: {}}}}},
                "basedirs.~/src.trees keys must be strings",
            ),
            ({"include": []}, "uses the old config format"),
            ({"include": {1: None}}, "include keys must be strings"),
            ({"include": {"~/repo": []}}, "include.~/repo must be a mapping or null"),
            (
                {"include": {"~/repo": {"unknown": True}}},
                "include.~/repo has unknown key: unknown",
            ),
            ({"include": {"~/repo": {"type": 1}}}, "include.~/repo.type must be a string"),
            (
                {"include": {"~/repo": {"type": "fossil"}}},
                "include.~/repo.type must be one of: cvs, git, hg, svn",
            ),
            (
                {"include": {"~/repo": {"remotes": "origin"}}},
                "include.~/repo.remotes must be a list of strings",
            ),
            (
                {"include": {"~/repo": {"remotes": ["origin", 1]}}},
                "include.~/repo.remotes must be a list of strings",
            ),
            (
                {"include": {"~/repo": {"updatecmd": ["make"]}}},
                "include.~/repo.updatecmd must be a string",
            ),
            (
                {"include": {"~/repo": {"submodules": "yes"}}},
                "include.~/repo.submodules must be a boolean",
            ),
        ]

        for config, message in cases:
            with self.subTest(config=config):
                with self.assertRaisesRegex(vv.ConfigError, message.replace(".", r"\.")):
                    vv.validate_config(config)

    def test_rejects_old_configuration_with_migration_message(self):
        cases = [
            {"basedir": "~/src"},
            {"exclude": ["repo"]},
            {"trees": {"repo": {}}},
            {"include": ["~/repo"]},
        ]

        for config in cases:
            with self.subTest(config=config):
                with self.assertRaisesRegex(vv.ConfigError, "uses the old config format"):
                    vv.validate_config(config)


class ConfigLoadingTests(unittest.TestCase):
    def test_empty_file_loads_as_empty_configuration(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "vv.conf"
            path.write_text("")
            with patch("vv.CONFIG_FILE", path):
                self.assertEqual(vv.load_config(), {})

    def test_non_mapping_yaml_is_rejected(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "vv.conf"
            path.write_text("- one\n- two\n")
            with patch("vv.CONFIG_FILE", path):
                with self.assertRaisesRegex(vv.ConfigError, "root must be a mapping"):
                    vv.load_config()

    def test_invalid_yaml_is_reported_as_config_error(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "vv.conf"
            path.write_text("basedirs: [\n")
            with patch("vv.CONFIG_FILE", path):
                with self.assertRaisesRegex(vv.ConfigError, "invalid YAML"):
                    vv.load_config()

    def test_main_prints_clean_config_error_without_traceback(self):
        with (
            tempfile.TemporaryDirectory() as tempdir,
            patch("vv.CONFIG_FILE", Path(tempdir) / "missing.conf"),
            patch("vv.load_config", side_effect=vv.ConfigError("jobs must be a positive integer")),
            patch.object(sys, "argv", ["vv", "list"]),
            redirect_stderr(io.StringIO()) as stderr,
        ):
            exit_code = vv.main()

        self.assertEqual(exit_code, 2)
        self.assertEqual(stderr.getvalue(), "error: jobs must be a positive integer\n")

    def test_cli_reports_invalid_yaml_without_traceback(self):
        with tempfile.TemporaryDirectory() as tempdir:
            home = Path(tempdir)
            (home / ".vv.conf").write_text("jobs: [\n")
            env = {**os.environ, "HOME": tempdir}

            result = subprocess.run(
                [sys.executable, str(Path(vv.__file__)), "list"],
                capture_output=True,
                text=True,
                env=env,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("error: invalid YAML", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class ConfigSavingTests(unittest.TestCase):
    def test_missing_parent_is_reported_without_creating_a_config(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "missing" / ".vv.conf"

            with patch("vv.CONFIG_FILE", path):
                with self.assertRaisesRegex(vv.ConfigError, "cannot prepare"):
                    vv.save_config({"jobs": 4})

            self.assertFalse(path.exists())

    def test_new_config_is_written_safely_with_restrictive_permissions(self):
        config = {"jobs": 2, "include": {"~/repo": None}}

        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / ".vv.conf"
            with patch("vv.CONFIG_FILE", path):
                vv.save_config(config)

            self.assertEqual(yaml.safe_load(path.read_text()), config)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(list(path.parent.glob(f".{path.name}.*")), [])

    def test_existing_config_permissions_are_preserved(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / ".vv.conf"
            path.write_text("jobs: 1\n")
            path.chmod(0o640)

            with patch("vv.CONFIG_FILE", path):
                vv.save_config({"jobs": 4})

            self.assertEqual(yaml.safe_load(path.read_text()), {"jobs": 4})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)

    def test_replace_failure_preserves_original_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / ".vv.conf"
            original = "jobs: 1\n"
            path.write_text(original)

            with (
                patch("vv.CONFIG_FILE", path),
                patch("vv.os.replace", side_effect=PermissionError("replace denied")),
            ):
                with self.assertRaisesRegex(vv.ConfigError, "replace denied"):
                    vv.save_config({"jobs": 4})

            self.assertEqual(path.read_text(), original)
            self.assertEqual(list(path.parent.glob(f".{path.name}.*")), [])

    def test_serialization_failure_preserves_original_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / ".vv.conf"
            original = "jobs: 1\n"
            path.write_text(original)

            with (
                patch("vv.CONFIG_FILE", path),
                patch("vv.yaml.safe_dump", side_effect=yaml.YAMLError("cannot serialize")),
            ):
                with self.assertRaisesRegex(vv.ConfigError, "cannot serialize"):
                    vv.save_config({"jobs": 4})

            self.assertEqual(path.read_text(), original)
            self.assertEqual(list(path.parent.glob(f".{path.name}.*")), [])


class DiscoveryTests(unittest.TestCase):
    @staticmethod
    def make_git_repo(path: Path) -> None:
        path.mkdir()
        (path / ".git").mkdir()

    def test_include_works_without_default_basedir(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo_path = root / "standalone"
            self.make_git_repo(repo_path)

            with patch("vv.DEFAULT_BASEDIR", root / "missing-src"):
                result = vv.get_repos({"include": {str(repo_path): None}})

        self.assertEqual([repo.path for repo in result.repos], [repo_path])
        self.assertEqual(result.errors, [])

    def test_include_only_configuration_works_in_fresh_cli_process(self):
        with tempfile.TemporaryDirectory() as tempdir:
            home = Path(tempdir)
            repo_path = home / "standalone"
            self.make_git_repo(repo_path)
            (home / ".vv.conf").write_text(
                yaml.safe_dump({"include": {str(repo_path): None}})
            )

            result = subprocess.run(
                [sys.executable, str(Path(vv.__file__)), "list"],
                capture_output=True,
                text=True,
                env={**os.environ, "HOME": tempdir},
            )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, f"{repo_path} (git)\n")
        self.assertEqual(result.stderr, "")

    def test_missing_implicit_default_basedir_is_optional(self):
        with tempfile.TemporaryDirectory() as tempdir:
            with patch("vv.DEFAULT_BASEDIR", Path(tempdir) / "missing-src"):
                result = vv.get_repos({})

        self.assertEqual(result.repos, [])
        self.assertEqual(result.errors, [])

    def test_missing_explicit_basedir_does_not_hide_valid_include(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo_path = root / "standalone"
            missing = root / "missing-src"
            self.make_git_repo(repo_path)

            result = vv.get_repos(
                {
                    "basedirs": {str(missing): {}},
                    "include": {str(repo_path): None},
                }
            )

        self.assertEqual([repo.path for repo in result.repos], [repo_path])
        self.assertEqual(result.errors, [f"basedir does not exist: {missing}"])

    def test_missing_and_unrecognized_includes_are_reported(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            basedir = root / "src"
            missing = root / "missing"
            unrecognized = root / "plain-directory"
            basedir.mkdir()
            unrecognized.mkdir()

            result = vv.get_repos(
                {
                    "basedirs": {str(basedir): {}},
                    "include": {str(missing): None, str(unrecognized): None},
                }
            )

        self.assertEqual(result.repos, [])
        self.assertEqual(
            set(result.errors),
            {
                f"included repository does not exist: {missing}",
                f"no supported VCS found at included repository: {unrecognized}",
            },
        )

    def test_canonical_paths_deduplicate_basedir_and_symlinked_include(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            basedir = root / "src"
            basedir.mkdir()
            repo_path = basedir / "repo"
            self.make_git_repo(repo_path)
            alias = root / "repo-alias"
            alias.symlink_to(repo_path, target_is_directory=True)

            result = vv.get_repos(
                {
                    "basedirs": {str(basedir): {}},
                    "include": {str(alias): None},
                }
            )

        self.assertEqual(len(result.repos), 1)
        self.assertEqual(result.repos[0].path, repo_path)
        self.assertEqual(result.errors, [])

    def test_list_reports_errors_after_listing_valid_repositories(self):
        repo = vv.Repo(Path("/repo"), vv._VCS_BY_NAME["git"], "repo", {})
        discovery = vv.DiscoveryResult([repo], ["basedir does not exist: /missing"])

        with (
            patch("vv.load_config", return_value={}),
            patch("vv.get_repos", return_value=discovery),
            redirect_stdout(io.StringIO()) as stdout,
            redirect_stderr(io.StringIO()) as stderr,
        ):
            exit_code = vv.cmd_list(argparse.Namespace())

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "repo (git)\n")
        self.assertEqual(stderr.getvalue(), "error: basedir does not exist: /missing\n")


class DirtyCheckTests(unittest.TestCase):
    def test_nonzero_exit_is_an_error_not_a_clean_tree(self):
        result = subprocess.CompletedProcess([], 128, stdout="", stderr="fatal: broken repository")

        with patch("vv.subprocess.run", return_value=result):
            with self.assertRaisesRegex(vv.DirtyCheckError, "broken repository"):
                vv.is_dirty(Path("/repo"), vv._VCS_BY_NAME["git"])

    def test_dirty_command_reports_check_errors_and_returns_failure(self):
        repo = vv.Repo(Path("/repo"), vv._VCS_BY_NAME["git"], "repo", {})

        with (
            tempfile.TemporaryDirectory() as tempdir,
            patch("vv.load_config", return_value={"basedirs": {tempdir: {}}}),
            patch("vv.get_repos", return_value=vv.DiscoveryResult([repo], [])),
            patch("vv.is_dirty", side_effect=vv.DirtyCheckError("status failed")),
            redirect_stderr(io.StringIO()) as stderr,
        ):
            exit_code = vv.cmd_dirty(argparse.Namespace())

        self.assertEqual(exit_code, 1)
        self.assertIn("repo: status failed", stderr.getvalue())

    def test_dirty_processes_repositories_but_fails_for_discovery_errors(self):
        repo = vv.Repo(Path("/repo"), vv._VCS_BY_NAME["git"], "repo", {})
        discovery = vv.DiscoveryResult([repo], ["included repository does not exist: /missing"])

        with (
            patch("vv.load_config", return_value={}),
            patch("vv.get_repos", return_value=discovery),
            patch("vv.is_dirty", return_value=False) as is_dirty,
            redirect_stderr(io.StringIO()) as stderr,
        ):
            exit_code = vv.cmd_dirty(argparse.Namespace())

        self.assertEqual(exit_code, 1)
        is_dirty.assert_called_once()
        self.assertIn("included repository does not exist: /missing", stderr.getvalue())


class UpdateWorkerTests(unittest.TestCase):
    def setUp(self):
        self.repo = vv.Repo(Path("/repo"), vv._VCS_BY_NAME["git"], "repo", {})

    def test_fetch_failure_stops_update_and_fails_repository(self):
        with (
            patch("vv.is_dirty", return_value=False),
            patch("vv.get_all_remotes", return_value=["origin"]),
            patch("vv.git_fetch", return_value=(False, "network unavailable")),
            patch("vv.run_update") as run_update,
        ):
            status, display, detail = vv._update_worker(self.repo, 10)

        self.assertEqual(status, "failed")
        self.assertIn("fetch origin: network unavailable", display)
        self.assertEqual(display, detail)
        run_update.assert_not_called()


class RecoveryEligibilityTests(unittest.TestCase):
    class TTY(io.StringIO):
        def isatty(self):
            return True

    def test_update_help_documents_noninteractive_option(self):
        result = subprocess.run(
            [sys.executable, str(Path(vv.__file__)), "update", "--help"],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("--no-interactive", result.stdout)

    def test_explicit_noninteractive_mode_overrides_ttys(self):
        with (
            patch("vv.sys.stdin", self.TTY()),
            patch("vv.sys.stdout", self.TTY()),
        ):
            enabled = vv.interactive_recovery_enabled(
                argparse.Namespace(no_interactive=True)
            )

        self.assertFalse(enabled)

    def test_recovery_requires_both_input_and_output_ttys(self):
        cases = [
            (self.TTY(), self.TTY(), True),
            (io.StringIO(), self.TTY(), False),
            (self.TTY(), io.StringIO(), False),
        ]

        for stdin, stdout, expected in cases:
            with self.subTest(stdin=stdin.isatty(), stdout=stdout.isatty()):
                with patch("vv.sys.stdin", stdin), patch("vv.sys.stdout", stdout):
                    enabled = vv.interactive_recovery_enabled(
                        argparse.Namespace(no_interactive=False)
                    )
                self.assertEqual(enabled, expected)


class UpdateCommandTests(unittest.TestCase):
    @staticmethod
    def config(tempdir: str) -> dict:
        return {
            "basedirs": {tempdir: {}},
            "logfile": str(Path(tempdir) / "vv.log"),
        }

    def test_explicit_noninteractive_failure_is_logged_without_shell(self):
        repo = vv.Repo(Path("/repo"), vv._VCS_BY_NAME["git"], "repo", {})

        with tempfile.TemporaryDirectory() as tempdir:
            config = self.config(tempdir)
            with (
                patch("vv.load_config", return_value=config),
                patch("vv.get_repos", return_value=vv.DiscoveryResult([repo], [])),
                patch(
                    "vv._update_worker",
                    return_value=("failed", "fetch failed", "details"),
                ),
                patch("vv.spawn_shell") as spawn_shell,
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()) as stderr,
            ):
                exit_code = vv.cmd_update(argparse.Namespace(no_interactive=True))

            log = Path(config["logfile"]).read_text()

        self.assertEqual(exit_code, 1)
        spawn_shell.assert_not_called()
        self.assertIn(
            "interactive recovery skipped: --no-interactive was specified",
            stderr.getvalue(),
        )
        self.assertIn("repo: failed", log)
        self.assertIn("details", log)

    def test_non_tty_failure_skips_recovery_automatically(self):
        repo = vv.Repo(Path("/repo"), vv._VCS_BY_NAME["git"], "repo", {})

        with tempfile.TemporaryDirectory() as tempdir:
            with (
                patch("vv.load_config", return_value=self.config(tempdir)),
                patch("vv.get_repos", return_value=vv.DiscoveryResult([repo], [])),
                patch("vv._update_worker", return_value=("failed", "failed", None)),
                patch("vv.interactive_recovery_enabled", return_value=False),
                patch("vv.spawn_shell") as spawn_shell,
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()) as stderr,
            ):
                exit_code = vv.cmd_update(argparse.Namespace(no_interactive=False))

        self.assertEqual(exit_code, 1)
        spawn_shell.assert_not_called()
        self.assertIn("not attached to a terminal", stderr.getvalue())

    def test_multiple_noninteractive_failures_are_all_logged(self):
        repos = [
            vv.Repo(Path("/one"), vv._VCS_BY_NAME["git"], "one", {}),
            vv.Repo(Path("/two"), vv._VCS_BY_NAME["git"], "two", {}),
        ]

        with tempfile.TemporaryDirectory() as tempdir:
            with (
                patch("vv.load_config", return_value=self.config(tempdir)),
                patch("vv.get_repos", return_value=vv.DiscoveryResult(repos, [])),
                patch("vv._update_worker", return_value=("failed", "failed", "detail")),
                patch("vv.write_log") as write_log,
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                exit_code = vv.cmd_update(argparse.Namespace(no_interactive=True))

        entries = write_log.call_args.args[1]
        self.assertEqual(exit_code, 1)
        failed_names = {name for name, status, _detail in entries if status == "failed"}
        self.assertEqual(failed_names, {"one", "two"})

    def test_update_processes_repositories_but_fails_for_discovery_errors(self):
        repo = vv.Repo(Path("/repo"), vv._VCS_BY_NAME["git"], "repo", {})
        discovery = vv.DiscoveryResult([repo], ["basedir does not exist: /missing"])

        with (
            patch("vv.load_config", return_value={}),
            patch("vv.get_repos", return_value=discovery),
            patch("vv._update_worker", return_value=("ok", None, None)) as worker,
            patch("vv.write_log"),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()) as stderr,
        ):
            exit_code = vv.cmd_update(argparse.Namespace())

        self.assertEqual(exit_code, 1)
        worker.assert_called_once_with(repo, 60)
        self.assertIn("basedir does not exist: /missing", stderr.getvalue())

    def test_worker_exception_is_recovered_and_full_workflow_is_retried(self):
        repo = vv.Repo(Path("/repo"), vv._VCS_BY_NAME["git"], "repo", {})
        results = [RuntimeError("missing git"), ("ok", None, "retry succeeded")]

        with tempfile.TemporaryDirectory() as tempdir:
            config = {"basedirs": {tempdir: {}}, "logfile": str(Path(tempdir) / "vv.log")}
            with (
                patch("vv.load_config", return_value=config),
                patch("vv.get_repos", return_value=vv.DiscoveryResult([repo], [])),
                patch("vv._update_worker", side_effect=results) as worker,
                patch("vv.interactive_recovery_enabled", return_value=True),
                patch("vv.spawn_shell") as spawn_shell,
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()) as stderr,
            ):
                exit_code = vv.cmd_update(argparse.Namespace())

        self.assertEqual(exit_code, 0)
        self.assertIn("unexpected error: missing git", stderr.getvalue())
        self.assertEqual(worker.call_args_list, [call(repo, 60), call(repo, 60)])
        spawn_shell.assert_called_once_with(repo.path)

    def test_interactive_retry_failure_is_logged_and_returns_failure(self):
        repo = vv.Repo(Path("/repo"), vv._VCS_BY_NAME["git"], "repo", {})
        results = [
            ("failed", "initial failure", "initial detail"),
            ("failed", "retry failure", "retry detail"),
        ]

        with tempfile.TemporaryDirectory() as tempdir:
            config = self.config(tempdir)
            with (
                patch("vv.load_config", return_value=config),
                patch("vv.get_repos", return_value=vv.DiscoveryResult([repo], [])),
                patch("vv._update_worker", side_effect=results),
                patch("vv.interactive_recovery_enabled", return_value=True),
                patch("vv.spawn_shell") as spawn_shell,
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                exit_code = vv.cmd_update(argparse.Namespace(no_interactive=False))

            log = Path(config["logfile"]).read_text()

        self.assertEqual(exit_code, 1)
        spawn_shell.assert_called_once_with(repo.path)
        self.assertIn("repo: failed", log)
        self.assertIn("retry detail", log)


if __name__ == "__main__":
    unittest.main()
