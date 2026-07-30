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
            "ff_default_branch": True,
            "basedirs": {
                "~/src": {
                    "exclude": ["ignored"],
                    "trees": {
                        "git-tree": {
                            "type": "git",
                            "remotes": ["upstream", "origin"],
                            "updatecmd": "make update",
                            "submodules": False,
                            "ff_default_branch": False,
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
            ({"ff_default_branch": "yes"}, "ff_default_branch must be a boolean"),
            (
                {"include": {"~/repo": {"ff_default_branch": 1}}},
                "include.~/repo.ff_default_branch must be a boolean",
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


class BranchStateTests(unittest.TestCase):
    def test_current_branch_is_returned(self):
        result = subprocess.CompletedProcess([], 0, stdout="main\n", stderr="")

        with patch("vv.subprocess.run", return_value=result) as run:
            self.assertEqual(vv.get_current_branch(Path("/repo")), "main")

        self.assertEqual(
            run.call_args.args[0], ["git", "symbolic-ref", "-q", "--short", "HEAD"]
        )

    def test_detached_head_returns_none(self):
        result = subprocess.CompletedProcess([], 1, stdout="", stderr="")

        with patch("vv.subprocess.run", return_value=result):
            self.assertIsNone(vv.get_current_branch(Path("/repo")))

    def test_branch_lookup_failure_is_an_error_not_a_detached_head(self):
        result = subprocess.CompletedProcess(
            [], 128, stdout="", stderr="fatal: not a git repository"
        )

        with patch("vv.subprocess.run", return_value=result):
            with self.assertRaisesRegex(vv.BranchCheckError, "not a git repository"):
                vv.get_current_branch(Path("/repo"))

    def test_upstream_remote_track_state_and_ref_are_parsed(self):
        result = subprocess.CompletedProcess(
            [],
            0,
            stdout="origin\t[ahead 1, behind 2]\trefs/remotes/origin/topic\n",
            stderr="",
        )

        with patch("vv.subprocess.run", return_value=result) as run:
            upstream = vv.get_branch_upstream(Path("/repo"), "topic")

        self.assertEqual(
            upstream, ("origin", "[ahead 1, behind 2]", "refs/remotes/origin/topic")
        )
        self.assertEqual(run.call_args.args[0][-1], "refs/heads/topic")

    def test_branch_without_upstream_has_empty_fields(self):
        result = subprocess.CompletedProcess([], 0, stdout="\t\t\n", stderr="")

        with patch("vv.subprocess.run", return_value=result):
            self.assertEqual(vv.get_branch_upstream(Path("/repo"), "topic"), ("", "", ""))

    def test_missing_branch_returns_none(self):
        result = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        with patch("vv.subprocess.run", return_value=result):
            self.assertIsNone(vv.get_branch_upstream(Path("/repo"), "topic"))

    def test_upstream_lookup_failure_is_an_error(self):
        result = subprocess.CompletedProcess(
            [], 128, stdout="", stderr="fatal: not a git repository"
        )

        with patch("vv.subprocess.run", return_value=result):
            with self.assertRaisesRegex(vv.BranchCheckError, "not a git repository"):
                vv.get_branch_upstream(Path("/repo"), "topic")


class WorkingBranchReasonTests(unittest.TestCase):
    @staticmethod
    def reason(branch, upstream, fetch_remotes=("origin",)):
        with (
            patch("vv.get_current_branch", return_value=branch),
            patch("vv.get_branch_upstream", return_value=upstream),
        ):
            return vv.working_branch_reason(Path("/repo"), list(fetch_remotes))

    def test_working_states_are_reported_with_their_cause(self):
        upstream_ref = "refs/remotes/origin/topic"
        cases = [
            (None, None, "detached HEAD"),
            ("topic", ("", "", ""), "working branch 'topic': no upstream"),
            ("topic", (".", "", ""), "working branch 'topic': tracks a local branch"),
            (
                "topic",
                ("github", "", "refs/remotes/github/topic"),
                "working branch 'topic': upstream remote 'github' is not fetched",
            ),
            (
                "topic",
                ("origin", "[gone]", upstream_ref),
                "working branch 'topic': upstream is gone",
            ),
            (
                "topic",
                ("origin", "[ahead 2]", upstream_ref),
                "working branch 'topic': ahead of upstream",
            ),
            (
                "topic",
                ("origin", "[ahead 1, behind 3]", upstream_ref),
                "working branch 'topic': diverged from upstream",
            ),
        ]

        for branch, upstream, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(self.reason(branch, upstream), expected)

    def test_tracking_branch_that_can_fast_forward_is_not_a_working_state(self):
        for track in ("", "[behind 3]"):
            with self.subTest(track=track):
                self.assertIsNone(
                    self.reason("main", ("origin", track, "refs/remotes/origin/main"))
                )

    def test_missing_branch_ref_is_an_error(self):
        with self.assertRaisesRegex(vv.BranchCheckError, "no ref for branch 'topic'"):
            self.reason("topic", None)


class FfDefaultBranchConfigTests(unittest.TestCase):
    def test_tree_setting_overrides_top_level_setting(self):
        cases = [
            ({}, {}, False),
            ({"ff_default_branch": True}, {}, True),
            ({}, {"ff_default_branch": True}, True),
            ({"ff_default_branch": True}, {"ff_default_branch": False}, False),
            ({"ff_default_branch": False}, {"ff_default_branch": True}, True),
        ]

        for config, tree_cfg, expected in cases:
            with self.subTest(config=config, tree_cfg=tree_cfg):
                self.assertEqual(vv.get_ff_default_branch(config, tree_cfg), expected)


class RemoteDefaultBranchTests(unittest.TestCase):
    def test_remote_head_yields_the_branch_name(self):
        result = subprocess.CompletedProcess([], 0, stdout="origin/main\n", stderr="")

        with patch("vv.subprocess.run", return_value=result) as run:
            self.assertEqual(vv.get_remote_default_branch(Path("/repo"), "origin"), "main")

        self.assertEqual(run.call_args.args[0][-1], "refs/remotes/origin/HEAD")

    def test_unset_remote_head_returns_none(self):
        result = subprocess.CompletedProcess([], 1, stdout="", stderr="")

        with patch("vv.subprocess.run", return_value=result):
            self.assertIsNone(vv.get_remote_default_branch(Path("/repo"), "origin"))

    def test_lookup_failure_is_an_error(self):
        result = subprocess.CompletedProcess(
            [], 128, stdout="", stderr="fatal: not a git repository"
        )

        with patch("vv.subprocess.run", return_value=result):
            with self.assertRaisesRegex(vv.BranchCheckError, "not a git repository"):
                vv.get_remote_default_branch(Path("/repo"), "origin")


class FastForwardDefaultBranchTests(unittest.TestCase):
    @staticmethod
    def fast_forward(
        current="topic",
        default="main",
        upstream=("origin", "[behind 2]", "refs/remotes/origin/main"),
        fetch_result=subprocess.CompletedProcess([], 0, stdout=b"", stderr=b""),
    ):
        with (
            patch("vv.get_current_branch", return_value=current),
            patch("vv.get_remote_default_branch", return_value=default),
            patch("vv.get_branch_upstream", return_value=upstream),
            patch("vv.subprocess.run", return_value=fetch_result) as run,
        ):
            note = vv.fast_forward_default_branch(Path("/repo"), ["origin"], 10)
        return note, run

    def test_default_branch_behind_upstream_is_fast_forwarded(self):
        note, run = self.fast_forward()

        self.assertEqual(note, "fast-forwarded 'main' to origin/main")
        run.assert_called_once()
        self.assertEqual(
            run.call_args.args[0],
            ["git", "fetch", ".", "refs/remotes/origin/main:refs/heads/main"],
        )

    def test_refused_fast_forward_is_reported_not_forced(self):
        rejected = subprocess.CompletedProcess(
            [],
            1,
            stdout=b"",
            stderr=b"From .\n ! [rejected] origin/main -> main  (non-fast-forward)\n",
        )

        note, _run = self.fast_forward(fetch_result=rejected)

        self.assertEqual(
            note,
            "default branch 'main' not fast-forwarded: "
            "! [rejected] origin/main -> main  (non-fast-forward)",
        )

    def test_states_with_nothing_to_do_are_silent(self):
        upstream_ref = "refs/remotes/origin/main"
        cases = [
            {"default": None},
            {"default": "topic"},  # the default branch is checked out
            {"upstream": None},  # no local branch for the remote default
            {"upstream": ("", "", "")},
            {"upstream": (".", "", "")},
            {"upstream": ("github", "[behind 2]", "refs/remotes/github/main")},
            {"upstream": ("origin", "", upstream_ref)},
            {"upstream": ("origin", "[gone]", upstream_ref)},
            {"upstream": ("origin", "[ahead 1]", upstream_ref)},
            {"upstream": ("origin", "[ahead 1, behind 2]", upstream_ref)},
        ]

        for kwargs in cases:
            with self.subTest(**kwargs):
                note, run = self.fast_forward(**kwargs)
                self.assertIsNone(note)
                run.assert_not_called()


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

    def test_working_branch_is_fetched_but_not_merged(self):
        with (
            patch("vv.is_dirty", return_value=False),
            patch("vv.get_all_remotes", return_value=["origin"]),
            patch("vv.git_fetch", return_value=(True, "new refs")) as git_fetch,
            patch(
                "vv.working_branch_reason",
                return_value="working branch 'topic': no upstream",
            ) as reason,
            patch("vv.run_update") as run_update,
        ):
            status, display, detail = vv._update_worker(self.repo, 10)

        self.assertEqual(status, "branch")
        self.assertEqual(display, "working branch 'topic': no upstream")
        self.assertEqual(
            detail, "fetch origin: new refs\nworking branch 'topic': no upstream"
        )
        git_fetch.assert_called_once_with(Path("/repo"), "origin", 10)
        reason.assert_called_once_with(Path("/repo"), ["origin"])
        run_update.assert_not_called()

    def test_working_branch_skip_fast_forwards_default_branch_when_enabled(self):
        with (
            patch("vv.is_dirty", return_value=False),
            patch("vv.get_all_remotes", return_value=["origin"]),
            patch("vv.git_fetch", return_value=(True, "new refs")),
            patch(
                "vv.working_branch_reason",
                return_value="working branch 'topic': no upstream",
            ),
            patch(
                "vv.fast_forward_default_branch",
                return_value="fast-forwarded 'main' to origin/main",
            ) as fast_forward,
            patch("vv.run_update") as run_update,
        ):
            status, display, detail = vv._update_worker(self.repo, 10, ff_default=True)

        self.assertEqual(status, "branch")
        self.assertEqual(
            display,
            "working branch 'topic': no upstream; fast-forwarded 'main' to origin/main",
        )
        self.assertEqual(
            detail,
            "fetch origin: new refs\n"
            "working branch 'topic': no upstream\n"
            "fast-forwarded 'main' to origin/main",
        )
        fast_forward.assert_called_once_with(Path("/repo"), ["origin"], 10)
        run_update.assert_not_called()

    def test_working_branch_skip_leaves_default_branch_alone_by_default(self):
        with (
            patch("vv.is_dirty", return_value=False),
            patch("vv.get_all_remotes", return_value=["origin"]),
            patch("vv.git_fetch", return_value=(True, "")),
            patch(
                "vv.working_branch_reason",
                return_value="working branch 'topic': no upstream",
            ),
            patch("vv.fast_forward_default_branch") as fast_forward,
        ):
            status, display, _detail = vv._update_worker(self.repo, 10)

        self.assertEqual(status, "branch")
        self.assertEqual(display, "working branch 'topic': no upstream")
        fast_forward.assert_not_called()

    def test_fast_forward_with_nothing_to_do_keeps_the_skip_reason_alone(self):
        with (
            patch("vv.is_dirty", return_value=False),
            patch("vv.get_all_remotes", return_value=["origin"]),
            patch("vv.git_fetch", return_value=(True, "")),
            patch(
                "vv.working_branch_reason",
                return_value="working branch 'topic': no upstream",
            ),
            patch("vv.fast_forward_default_branch", return_value=None),
        ):
            status, display, detail = vv._update_worker(self.repo, 10, ff_default=True)

        self.assertEqual(status, "branch")
        self.assertEqual(display, "working branch 'topic': no upstream")
        self.assertEqual(detail, "working branch 'topic': no upstream")

    def test_tracking_branch_in_sync_is_merged(self):
        with (
            patch("vv.is_dirty", return_value=False),
            patch("vv.get_all_remotes", return_value=["origin"]),
            patch("vv.git_fetch", return_value=(True, "")),
            patch("vv.working_branch_reason", return_value=None),
            patch("vv.run_update", return_value=(True, "Fast-forward")) as run_update,
        ):
            status, display, _detail = vv._update_worker(self.repo, 10)

        self.assertEqual(status, "ok")
        self.assertEqual(display, "Fast-forward")
        run_update.assert_called_once()

    def test_custom_updatecmd_bypasses_working_branch_check(self):
        repo = vv.Repo(
            Path("/repo"), vv._VCS_BY_NAME["git"], "repo", {"updatecmd": "make update"}
        )

        with (
            patch("vv.is_dirty", return_value=False),
            patch("vv.get_all_remotes", return_value=["origin"]),
            patch("vv.git_fetch", return_value=(True, "")),
            patch("vv.working_branch_reason") as reason,
            patch("vv.run_update", return_value=(True, "")) as run_update,
        ):
            status, _display, _detail = vv._update_worker(repo, 10)

        self.assertEqual(status, "ok")
        reason.assert_not_called()
        run_update.assert_called_once_with(Path("/repo"), repo.driver, "make update", 10)

    def test_non_git_repositories_skip_working_branch_check(self):
        repo = vv.Repo(Path("/repo"), vv._VCS_BY_NAME["hg"], "repo", {})

        with (
            patch("vv.is_dirty", return_value=False),
            patch("vv.working_branch_reason") as reason,
            patch("vv.run_update", return_value=(True, "")),
        ):
            status, _display, _detail = vv._update_worker(repo, 10)

        self.assertEqual(status, "ok")
        reason.assert_not_called()

    def test_branch_check_error_propagates_instead_of_merging(self):
        with (
            patch("vv.is_dirty", return_value=False),
            patch("vv.get_all_remotes", return_value=["origin"]),
            patch("vv.git_fetch", return_value=(True, "")),
            patch(
                "vv.working_branch_reason",
                side_effect=vv.BranchCheckError("git branch check failed: boom"),
            ),
            patch("vv.run_update") as run_update,
        ):
            with self.assertRaisesRegex(vv.BranchCheckError, "boom"):
                vv._update_worker(self.repo, 10)

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
        worker.assert_called_once_with(repo, 60, False)
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
        self.assertEqual(
            worker.call_args_list, [call(repo, 60, False), call(repo, 60, False)]
        )
        spawn_shell.assert_called_once_with(repo.path)

    def test_ff_default_branch_setting_reaches_workers_per_tree(self):
        repos = [
            vv.Repo(Path("/plain"), vv._VCS_BY_NAME["git"], "plain", {}),
            vv.Repo(
                Path("/pinned"),
                vv._VCS_BY_NAME["git"],
                "pinned",
                {"ff_default_branch": False},
            ),
        ]

        with tempfile.TemporaryDirectory() as tempdir:
            config = self.config(tempdir) | {"ff_default_branch": True}
            with (
                patch("vv.load_config", return_value=config),
                patch("vv.get_repos", return_value=vv.DiscoveryResult(repos, [])),
                patch("vv._update_worker", return_value=("ok", None, None)) as worker,
                patch("vv.write_log"),
                redirect_stdout(io.StringIO()),
            ):
                exit_code = vv.cmd_update(argparse.Namespace(no_interactive=True))

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            sorted(worker.call_args_list, key=lambda c: c.args[0].label),
            [call(repos[1], 60, False), call(repos[0], 60, True)],
        )

    def test_working_branch_skip_is_reported_without_shell_or_failure(self):
        repo = vv.Repo(Path("/repo"), vv._VCS_BY_NAME["git"], "repo", {})

        with tempfile.TemporaryDirectory() as tempdir:
            config = self.config(tempdir)
            with (
                patch("vv.load_config", return_value=config),
                patch("vv.get_repos", return_value=vv.DiscoveryResult([repo], [])),
                patch(
                    "vv._update_worker",
                    return_value=(
                        "branch",
                        "working branch 'topic': no upstream",
                        "fetch origin: new refs\nworking branch 'topic': no upstream",
                    ),
                ),
                patch("vv.spawn_shell") as spawn_shell,
                redirect_stdout(io.StringIO()) as stdout,
                redirect_stderr(io.StringIO()) as stderr,
            ):
                exit_code = vv.cmd_update(argparse.Namespace(no_interactive=False))

            log = Path(config["logfile"]).read_text()

        self.assertEqual(exit_code, 0)
        spawn_shell.assert_not_called()
        self.assertIn(
            "repo: skipped (working branch 'topic': no upstream)\n", stdout.getvalue()
        )
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("repo: branch", log)
        self.assertIn("fetch origin: new refs", log)

    def test_interactive_retry_landing_on_working_branch_is_a_skip(self):
        repo = vv.Repo(Path("/repo"), vv._VCS_BY_NAME["git"], "repo", {})
        results = [
            ("failed", "fetch failed", "initial detail"),
            ("branch", "working branch 'topic': no upstream", "reason detail"),
        ]

        with tempfile.TemporaryDirectory() as tempdir:
            config = self.config(tempdir)
            with (
                patch("vv.load_config", return_value=config),
                patch("vv.get_repos", return_value=vv.DiscoveryResult([repo], [])),
                patch("vv._update_worker", side_effect=results),
                patch("vv.interactive_recovery_enabled", return_value=True),
                patch("vv.spawn_shell") as spawn_shell,
                redirect_stdout(io.StringIO()) as stdout,
                redirect_stderr(io.StringIO()),
            ):
                exit_code = vv.cmd_update(argparse.Namespace(no_interactive=False))

            log = Path(config["logfile"]).read_text()

        self.assertEqual(exit_code, 0)
        spawn_shell.assert_called_once_with(repo.path)
        self.assertIn(
            "repo: skipped (working branch 'topic': no upstream)\n", stdout.getvalue()
        )
        self.assertIn("repo: branch", log)
        self.assertIn("reason detail", log)

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


class ProgressCollapseTests(unittest.TestCase):
    def test_carriage_return_redraws_collapse_to_final_line(self):
        raw = (
            "Updating files:  71% (3449/4809)\r"
            "Updating files:  99% (4761/4809)\r"
            "Updating files: 100% (4809/4809)\r"
            "Updating files: 100% (4809/4809), done.\n"
            "Fast-forward\n"
        )
        collapsed = vv._collapse_progress(raw)
        self.assertEqual(
            collapsed.splitlines(),
            ["Updating files: 100% (4809/4809), done.", "Fast-forward"],
        )
        self.assertNotIn("\r", collapsed)
        self.assertNotIn("71%", collapsed)

    def test_plain_output_including_blank_lines_is_unchanged(self):
        raw = "line one\n\nline two\n"
        self.assertEqual(vv._collapse_progress(raw), raw)

    def test_trailing_carriage_return_keeps_visible_text(self):
        # A terminal only moves the cursor on a trailing CR, so text before
        # it stays visible and must not be dropped.
        self.assertEqual(vv._collapse_progress("foo\r\nbar"), "foo\nbar")

    def test_git_fetch_output_has_progress_collapsed(self):
        # Output must be captured as bytes; text mode would translate the
        # carriage returns to newlines and defeat the collapse.
        stderr = (
            b"remote: Counting objects: 100% (5/5), done.\n"
            b"Receiving objects:  50% (1/2)\r"
            b"Receiving objects: 100% (2/2), done.\n"
        )
        result = subprocess.CompletedProcess([], 0, stdout=b"", stderr=stderr)

        with patch("vv.subprocess.run", return_value=result) as run:
            ok, output = vv.git_fetch(Path("/repo"), "origin", 60)

        self.assertNotEqual(run.call_args.kwargs.get("text"), True)
        self.assertTrue(ok)
        self.assertNotIn("\r", output)
        self.assertNotIn("50%", output)
        self.assertIn("Receiving objects: 100% (2/2), done.", output)

    def test_run_update_output_has_progress_collapsed(self):
        stderr = b"Updating files:  40% (2/5)\rUpdating files: 100% (5/5), done.\n"
        result = subprocess.CompletedProcess([], 0, stdout=b"Fast-forward\n", stderr=stderr)

        with patch("vv.subprocess.run", return_value=result) as run:
            ok, output = vv.run_update(Path("/repo"), vv._VCS_BY_NAME["git"])

        self.assertNotEqual(run.call_args.kwargs.get("text"), True)
        self.assertTrue(ok)
        self.assertNotIn("\r", output)
        self.assertNotIn("40%", output)
        self.assertIn("Fast-forward", output)
        self.assertIn("Updating files: 100% (5/5), done.", output)


if __name__ == "__main__":
    unittest.main()
