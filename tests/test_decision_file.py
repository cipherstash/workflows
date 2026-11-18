"""The workflow's paths to the files it checks out from this repository must point at real files in this repository.

The decide job checks out the request decision and the Python step files from this repository, and the fire job checks
out the step files, and each runs them by path. A rename would pass every other test and fail only in production, where
every run would end as decision-failed or with no review requested.
"""

import unittest

from harness import CHECKOUT_PATH, ROOT, STEPS_DIR, WORKFLOW_FILES, _yq, checked_out_file, file_steps, step_file_path


def checkout(job="decide"):
    """The job's checkout directory and its sparse-checkout patterns."""
    query = f'.jobs.{job}.steps[] | select(.id == "checkout") | .with.'
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
    def test_each_env_path_is_in_the_checkout_and_matched_by_a_sparse_pattern(self):
        _, patterns = checkout()
        for step_id in ("first", "second"):
            with self.subTest(step=step_id):
                path = checked_out_file("decide", step_id, "REQUEST_DECISION_FILE")
                self.assertTrue(path.is_file(), path)
                relative = path.relative_to(ROOT).as_posix()
                self.assertTrue(checked_out(relative, patterns), (relative, patterns))


class CheckoutTest(unittest.TestCase):
    def test_the_harness_and_the_workflow_agree_on_the_checkout_path(self):
        checkout_path, _ = checkout()
        self.assertEqual(checkout_path, CHECKOUT_PATH)

    def test_each_job_that_checks_out_this_repository_names_the_checkout_in_its_env(self):
        # The step files and the request decision are named from WORKFLOW_FILES, so it must be the checkout's path.
        for job in sorted({job for job, _ in file_steps()}):
            with self.subTest(job=job):
                self.assertEqual(_yq(f".jobs.{job}.env.WORKFLOW_FILES").strip(), WORKFLOW_FILES)

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
        # A job sees only its own workspace, so each job that runs a step file checks out this repository itself, in
        # a step before the file's step, and its sparse patterns take in the file and the helper module beside it.
        for job, step_id in file_steps():
            with self.subTest(job=job, step=step_id):
                ids = _yq(f".jobs.{job}.steps[].id").split()
                self.assertIn("checkout", ids)
                self.assertLess(ids.index("checkout"), ids.index(step_id))
                checkout_path, patterns = checkout(job)
                self.assertEqual(checkout_path, CHECKOUT_PATH)
                for file in sorted(STEPS_DIR.glob("*.py")):
                    relative = file.relative_to(ROOT).as_posix()
                    self.assertTrue(checked_out(relative, patterns), (relative, patterns))


class CheckoutStepsTest(unittest.TestCase):
    """Every job that checks out this repository does it the same way, so a hardening fix to one reaches them all."""

    def jobs(self):
        return sorted({job for job, _ in file_steps()})

    def test_the_decide_and_fire_jobs_check_out_this_repository(self):
        self.assertEqual(self.jobs(), ["decide", "fire"])

    def test_each_checkout_is_the_same_apart_from_its_name_and_sparse_patterns(self):
        # Compared value by value, because each yq prints a whole step differently (one keeps its comments).
        def step(job):
            query = f'.jobs.{job}.steps[] | select(.id == "checkout")'
            keys = [key for key in _yq(query + " | keys | .[]").split() if key != "name"]
            settings = [key for key in _yq(query + " | .with | keys | .[]").split() if key != "sparse-checkout"]
            values = {key: _yq(f'{query} | ."{key}"').strip() for key in keys if key != "with"}
            values.update({f"with.{key}": _yq(f'{query} | .with."{key}"').strip() for key in settings})
            return sorted(keys), sorted(settings), values

        for job in self.jobs():
            with self.subTest(job=job):
                self.assertEqual(step(job), step("decide"))

    def test_each_checkout_is_of_this_repository_at_the_pinned_commit_without_credentials(self):
        for job in self.jobs():
            with self.subTest(job=job):
                query = f'.jobs.{job}.steps[] | select(.id == "checkout") | '
                self.assertEqual(_yq(query + ".if").strip(),
                                 "job.workflow_repository != '' && job.workflow_sha != ''")
                self.assertEqual(_yq(query + '."continue-on-error"').strip(), "true")
                self.assertEqual(_yq(query + ".with.repository").strip(), "${{ job.workflow_repository }}")
                self.assertEqual(_yq(query + ".with.ref").strip(), "${{ job.workflow_sha }}")
                self.assertEqual(_yq(query + '.with."persist-credentials"').strip(), "false")
                self.assertEqual(_yq(query + '.with."sparse-checkout-cone-mode"').strip(), "false")

if __name__ == "__main__":
    unittest.main()
