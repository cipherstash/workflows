"""The actions-lock job's script, .github/scripts/check-lock, run against a temporary repository and a stand-in `gh`.

The stand-in `gh actions-lock` is a small shell script. What it does is set by environment variables: FAKE_VERIFY is
the exit status of --verify, FAKE_LOCK_EXIT the exit status of the lockfile run, FAKE_LOCK_CONTENT what that run writes
to the lockfile, FAKE_REWRITE what it appends to the workflow, and FAKE_NOISE a line it prints. Needs git, bash,
coreutils and base64.
"""

import os
import pathlib
import subprocess
import tempfile
import unittest

from harness import ROOT

SCRIPT = ROOT / ".github" / "scripts" / "check-lock"
LOCKFILE = ".github/workflows/actions.lock"
WORKFLOW = ".github/workflows/w.yml"

FAKE_GH = """#!/bin/sh
[ "$1" = actions-lock ] || exit 64
[ -z "$FAKE_NOISE" ] || echo "$FAKE_NOISE"
if [ "$2" = --verify ]; then exit "${FAKE_VERIFY:-0}"; fi
[ -z "$FAKE_LOCK_CONTENT" ] || printf '%s\\n' "$FAKE_LOCK_CONTENT" > .github/workflows/actions.lock
[ -z "$FAKE_REWRITE" ] || echo "$FAKE_REWRITE" >> .github/workflows/w.yml
exit "${FAKE_LOCK_EXIT:-0}"
"""


def git(cwd, *args):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args],
        cwd=cwd, env=env, check=True, capture_output=True, text=True,
    )


class CheckLockTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = pathlib.Path(tmp.name)
        self.origin = self.tmp / "origin.git"
        self.work = self.tmp / "work"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        git(self.tmp, "init", "--quiet", "--bare", "-b", "main", str(self.origin))
        git(self.tmp, "clone", "--quiet", str(self.origin), str(self.work))
        (self.work / ".github" / "workflows").mkdir(parents=True)
        (self.work / WORKFLOW).write_text("name: w\n")

    def base_branch(self, with_lockfile):
        """Commits the base branch, main, to the origin, with or without the lockfile, and starts the pull request."""
        if with_lockfile:
            (self.work / LOCKFILE).write_text("locked\n")
        git(self.work, "add", "-A")
        git(self.work, "commit", "--quiet", "-m", "base")
        git(self.work, "push", "--quiet", "origin", "HEAD:main")

    def run_script(self, base_ref="main", **env):
        full = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.tmp),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "BASE_REF": base_ref,
            "GH_TOKEN": "unused",
            **env,
        }
        # A failing check is a result to check, not an error here.
        return subprocess.run([str(SCRIPT)], cwd=self.work, env=full, capture_output=True, text=True, check=False)

    def annotations(self, result):
        return [line for line in result.stdout.splitlines() if line.startswith(("::error", "::warning"))]


class GivenTheLockfileIsInThePullRequest(CheckLockTest):
    def setUp(self):
        super().setUp()
        self.base_branch(with_lockfile=True)

    def test_it_passes_when_the_lockfile_is_current(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.annotations(result), [])

    def test_it_fails_when_verify_fails(self):
        result = self.run_script(FAKE_VERIFY="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("title=Workflow lockfile out of date", result.stdout)

    def test_it_fails_when_the_lockfile_run_changes_the_workflows(self):
        summary = self.tmp / "summary.md"
        result = self.run_script(FAKE_REWRITE="# changed", GITHUB_STEP_SUMMARY=str(summary))
        self.assertEqual(result.returncode, 1)
        self.assertIn("title=Workflow lockfile out of date", result.stdout)
        self.assertIn("# changed", summary.read_text(encoding="utf-8"))

    def test_it_says_so_when_the_tool_itself_fails(self):
        result = self.run_script(FAKE_LOCK_EXIT="3")
        self.assertEqual(result.returncode, 1)
        self.assertIn("title=gh actions-lock failed::gh actions-lock itself failed (exit status 3)", result.stdout)


class GivenTheLockfileIsMissingButTheBaseHasIt(CheckLockTest):
    def setUp(self):
        super().setUp()
        self.base_branch(with_lockfile=True)
        git(self.work, "rm", "--quiet", LOCKFILE)

    def test_it_fails(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("title=Workflow lockfile deleted", result.stdout)
        self.assertNotIn("::warning", result.stdout)


class GivenNeitherHasTheLockfile(CheckLockTest):
    def setUp(self):
        super().setUp()
        self.base_branch(with_lockfile=False)

    def test_it_only_warns(self):
        result = self.run_script(FAKE_LOCK_CONTENT="made")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("::warning file=.github/workflows/actions.lock,title=No workflow lockfile yet::", result.stdout)

    def test_it_only_warns_outside_a_pull_request(self):
        result = self.run_script(base_ref="")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("title=No workflow lockfile yet", result.stdout)

    def test_it_fails_when_the_base_cannot_be_fetched(self):
        result = self.run_script(base_ref="no-such-branch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("title=Base branch not fetched", result.stdout)


class GivenAnAnnotation(CheckLockTest):
    def setUp(self):
        super().setUp()
        self.base_branch(with_lockfile=False)
        self.summary = self.tmp / "summary.md"

    def summary_text(self):
        return self.summary.read_text(encoding="utf-8") if self.summary.exists() else ""

    def run_with_summary(self, **env):
        return self.run_script(GITHUB_STEP_SUMMARY=str(self.summary), **env)

    def test_it_stays_short_and_points_to_the_job_summary(self):
        # GitHub caps an annotation's size, so the lockfile and the diff go to the job summary instead.
        result = self.run_with_summary(FAKE_LOCK_CONTENT="x" * 5000, FAKE_REWRITE="# pinned")
        (annotation,) = self.annotations(result)
        self.assertLess(len(annotation), 500, annotation)
        self.assertIn("job summary", annotation)
        self.assertNotIn("xxxx", annotation)
        self.assertNotIn("# pinned", annotation)
        self.assertNotIn("\n", annotation)

    def test_the_summary_carries_the_whole_lockfile_unescaped(self):
        result = self.run_with_summary(FAKE_LOCK_CONTENT="100% done\r\n::set-output name=x::y")
        self.assertEqual(len(self.annotations(result)), 1)
        text = self.summary_text()
        self.assertIn("100% done", text)
        self.assertIn("::set-output name=x::y", text)
        self.assertIn("SHA-256", text)
        self.assertIn("Base64", text)

    def test_the_summary_carries_the_workflow_changes(self):
        self.run_with_summary(FAKE_REWRITE="# pinned")
        text = self.summary_text()
        self.assertIn(" M .github/workflows/w.yml", text)
        self.assertIn("+# pinned", text)

    def test_the_summary_is_added_to_not_replaced(self):
        self.summary.write_text("earlier step\n")
        self.run_with_summary(FAKE_LOCK_CONTENT="made")
        self.assertTrue(self.summary_text().startswith("earlier step\n"))

    def test_it_still_annotates_when_there_is_no_summary_file(self):
        result = self.run_script(FAKE_LOCK_CONTENT="made")
        (annotation,) = self.annotations(result)
        self.assertIn("title=No workflow lockfile yet", annotation)

    def test_it_shields_the_tools_output_from_workflow_commands(self):
        result = self.run_script(FAKE_NOISE="::add-mask::secret")
        lines = result.stdout.splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith("::stop-commands::"))
        token = lines[start][len("::stop-commands::"):]
        self.assertRegex(token, r"^[0-9a-f]{32}$")
        end = lines.index(f"::{token}::")
        self.assertIn("::add-mask::secret", lines[start + 1:end])
        self.assertNotIn("::add-mask::secret", lines[:start] + lines[end + 1:])


if __name__ == "__main__":
    unittest.main()
