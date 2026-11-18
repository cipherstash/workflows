"""The workflow's path strings for the request decision must point at the real file in this repository.

The decide job checks the file out from this repository and runs it by path, and a rename would pass every other test
and fail only in production, where every run would end as decision-failed.
"""

import pathlib
import unittest

from harness import WORKFLOW, _yq

ROOT = WORKFLOW.parent.parent.parent
WORKSPACE_PREFIX = "${{ github.workspace }}/"


def decision_paths():
    """The decide job's decision file path, and the checkout's directory and sparse-checkout pattern."""
    env_path = _yq(".jobs.decide.env.REQUEST_DECISION_FILE").strip()
    checkout = '.jobs.decide.steps[] | select(.id == "checkout") | .with.'
    return env_path, _yq(checkout + "path").strip(), _yq(checkout + '"sparse-checkout"').strip()


class DecisionFileTest(unittest.TestCase):
    def test_the_env_path_is_the_checkout_path_and_the_sparse_pattern(self):
        env_path, checkout_path, pattern = decision_paths()
        self.assertTrue(env_path.startswith(WORKSPACE_PREFIX), env_path)
        self.assertTrue(pattern.startswith("/"), pattern)
        self.assertEqual(env_path[len(WORKSPACE_PREFIX):], f"{checkout_path}/{pattern[1:]}")

    def test_the_sparse_pattern_names_a_file_in_this_repository(self):
        _, _, pattern = decision_paths()
        self.assertTrue((ROOT / pattern[1:]).is_file(), pattern)


if __name__ == "__main__":
    unittest.main()
