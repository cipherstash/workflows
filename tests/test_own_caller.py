"""This repository's own review caller. See "Reviews of this repository's pull requests" in the README."""

import unittest

from harness import EXAMPLE, OWN_CALLER, _yq


class OwnCallerTest(unittest.TestCase):
    def test_is_the_example_caller_byte_for_byte(self) -> None:
        self.assertEqual(OWN_CALLER.read_bytes(), EXAMPLE.read_bytes())

    def test_pins_the_shared_workflow_by_full_commit(self) -> None:
        uses = _yq(".jobs.review.uses", OWN_CALLER).strip()
        name, _, ref = uses.partition("@")
        self.assertEqual(name, "cipherstash/workflows/.github/workflows/claude-review.yml")
        self.assertRegex(ref, r"^[0-9a-f]{40}$")


class ConcurrencyTest(unittest.TestCase):
    def test_the_fire_group_names_the_calling_workflow(self) -> None:
        # The self-test and the real caller fire for the same pull request here; a shared group could drop a request.
        group = _yq(".jobs.fire.concurrency.group")
        self.assertIn("${{ github.workflow }}", group)


if __name__ == "__main__":
    unittest.main()
