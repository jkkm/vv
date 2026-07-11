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
            patch("vv.get_repos", return_value=[repo]),
            patch("vv.is_dirty", side_effect=vv.DirtyCheckError("status failed")),
            redirect_stderr(io.StringIO()) as stderr,
        ):
            exit_code = vv.cmd_dirty(argparse.Namespace())

        self.assertEqual(exit_code, 1)
        self.assertIn("repo: status failed", stderr.getvalue())


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


class UpdateCommandTests(unittest.TestCase):
    def test_worker_exception_is_recovered_and_full_workflow_is_retried(self):
        repo = vv.Repo(Path("/repo"), vv._VCS_BY_NAME["git"], "repo", {})
        results = [RuntimeError("missing git"), ("ok", None, "retry succeeded")]

        with tempfile.TemporaryDirectory() as tempdir:
            config = {"basedirs": {tempdir: {}}, "logfile": str(Path(tempdir) / "vv.log")}
            with (
                patch("vv.load_config", return_value=config),
                patch("vv.get_repos", return_value=[repo]),
                patch("vv._update_worker", side_effect=results) as worker,
                patch("vv.spawn_shell") as spawn_shell,
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()) as stderr,
            ):
                exit_code = vv.cmd_update(argparse.Namespace())

        self.assertEqual(exit_code, 0)
        self.assertIn("unexpected error: missing git", stderr.getvalue())
        self.assertEqual(worker.call_args_list, [call(repo, 60), call(repo, 60)])
        spawn_shell.assert_called_once_with(repo.path)


if __name__ == "__main__":
    unittest.main()
