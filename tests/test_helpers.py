"""The shared helper module's functions that the step tests reach only indirectly."""

import sys
import unittest

from harness import STEPS_DIR

sys.path.insert(0, str(STEPS_DIR))
import helpers


class RedactedTest(unittest.TestCase):
    def test_every_copy_of_the_token_is_taken_out(self):
        self.assertEqual(helpers.redacted("a ghs_x b ghs_x", "ghs_x"), "a [token] b [token]")

    def test_text_without_the_token_is_unchanged(self):
        self.assertEqual(helpers.redacted("nothing here", "ghs_x"), "nothing here")

    def test_an_empty_token_leaves_the_text_unchanged(self):
        # str.replace with an empty string would insert the marker between every character. The routine call never
        # gets this far with an empty token (tests/test_fire.py: an empty token is reported without a request).
        self.assertEqual(helpers.redacted("abc", ""), "abc")


if __name__ == "__main__":
    unittest.main()
