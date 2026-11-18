"""The workflow's paths to the files it checks out from this repository must point at real files in this repository.

The decide job checks out the request decision and the Python step files from this repository and runs them by path.
A rename would pass every other test and fail only in production, where every run would end as decision-failed.
"""

import unittest

from harness import CHECKOUT_PATH, ROOT, STEPS_DIR, WORKSPACE_PREFIX, _yq, file_steps, step_file_path


def checkout():
    """The decide job's checkout directory and its sparse-checkout patterns."""
    query = '.jobs.decide.steps[] | select(.id == "checkout") | .with.'
    patterns = [line.strip() for line in _yq(query + '"sparse-checkout"').splitlines() if line.strip()]
    return _yq(query + "path").strip(), patterns


def checked_out(path, patterns):
    """Whether path, relative to this repository's root, matches a sparse-checkout pattern: a file's own path, or a
    directory's path ending in /. These are the only two kinds of pattern the workflow uses."""
    for pattern in patterns:
        if pattern == f"/{path}" or (pattern.endswith("/") and f"/{path}".startswith(pattern)):
            return True
    return False


class DecisionFileTest(unittest.TestCase):
    def test_the_env_path_is_in_the_checkout_and_matched_by_a_sparse_pattern(self):
        env_path = _yq(".jobs.decide.env.REQUEST_DECISION_FILE").strip()
        checkout_path, patterns = checkout()
        self.assertTrue(env_path.startswith(f"{WORKSPACE_PREFIX}{checkout_path}/"), env_path)
        relative = env_path[len(f"{WORKSPACE_PREFIX}{checkout_path}/"):]
        self.assertTrue((ROOT / relative).is_file(), relative)
        self.assertTrue(checked_out(relative, patterns), (relative, patterns))


class CheckoutTest(unittest.TestCase):
    def test_the_harness_and_the_workflow_agree_on_the_checkout_path(self):
        checkout_path, _ = checkout()
        self.assertEqual(checkout_path, CHECKOUT_PATH)

    def test_each_sparse_pattern_names_a_file_or_directory_in_this_repository(self):
        _, patterns = checkout()
        self.assertNotEqual(patterns, [])
        for pattern in patterns:
            with self.subTest(pattern=pattern):
                self.assertTrue(pattern.startswith("/"), pattern)
                path = ROOT / pattern[1:]
                self.assertTrue(path.is_dir() if pattern.endswith("/") else path.is_file(), pattern)

    def test_every_python_file_the_steps_need_is_checked_out(self):
        # A step file imports the shared helper module, so every Python file beside it must be checked out too.
        _, patterns = checkout()
        files = sorted(STEPS_DIR.glob("*.py"))
        self.assertNotEqual(files, [])
        for file in files:
            relative = file.relative_to(ROOT).as_posix()
            with self.subTest(file=relative):
                self.assertTrue(checked_out(relative, patterns), (relative, patterns))


class StepFilesTest(unittest.TestCase):
    def test_each_step_file_is_a_file_in_this_repository_in_the_steps_directory(self):
        self.assertNotEqual(file_steps(), [])
        for job, step_id in file_steps():
            with self.subTest(job=job, step=step_id):
                path = step_file_path(job, step_id)
                self.assertTrue(path.is_file(), path)
                self.assertEqual(path.parent, STEPS_DIR)

    def test_each_step_file_is_checked_out_by_its_own_job(self):
        # Only the decide job checks out this repository so far, so a step file in any other job could not be found.
        for job, step_id in file_steps():
            with self.subTest(job=job, step=step_id):
                self.assertEqual(job, "decide")


if __name__ == "__main__":
    unittest.main()
