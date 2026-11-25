"""This repository's own review caller, which asks the real Claude code-review routine to review its pull requests.

It is the example caller, copied byte for byte, so this repository is reviewed exactly as every other repository is,
by the released workflow the example pins. It is named code-review.yml because claude-review.yml here is the reusable
workflow itself.
"""

import unittest

from harness import ROOT, _yq

EXAMPLE = ROOT / "examples" / "claude-review.yml"
OWN_CALLER = ROOT / ".github" / "workflows" / "code-review.yml"


class OwnCallerTest(unittest.TestCase):
    def test_is_the_example_caller_byte_for_byte(self) -> None:
        # When the example's pin moves to a new release, copy it here again in the same pull request.
        self.assertEqual(OWN_CALLER.read_bytes(), EXAMPLE.read_bytes())

    def test_pins_a_released_commit_not_this_pull_requests_copy(self) -> None:
        # `$/` or `./` would run the pull request's own version of the workflow, so a pull request could change the
        # workflow that reviews it. A full commit pin runs the released version.
        uses = _yq(".jobs.review.uses", OWN_CALLER).strip()
        name, _, ref = uses.partition("@")
        self.assertEqual(name, "cipherstash/workflows/.github/workflows/claude-review.yml")
        self.assertRegex(ref, r"^[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
