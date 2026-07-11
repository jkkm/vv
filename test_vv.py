import argparse
import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import call, patch

import vv


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
