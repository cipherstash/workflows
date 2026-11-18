"""The shared helper module's token redaction, which the routine call's error annotation relies on."""

import importlib.util
import unittest

from harness import STEPS_DIR


def load_helpers():
    spec = importlib.util.spec_from_file_location("step_helpers", STEPS_DIR / "helpers.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helpers = load_helpers()


class RedactedTest(unittest.TestCase):
    def test_every_copy_of_the_token_is_taken_out(self):
        self.assertEqual(helpers.redacted("bad sk-1 and sk-1 again", "sk-1"), "bad [token] and [token] again")

    def test_text_without_the_token_is_unchanged(self):
        self.assertEqual(helpers.redacted("no secret here", "sk-1"), "no secret here")

    def test_an_empty_token_leaves_the_text_unchanged(self):
        # str.replace with an empty string would put "[token]" between every character. The routine call never gets
        # this far with an empty token (tests/test_fire.py: an empty token is reported without a request), and the
        # helper keeps the text as it is in any case.
        self.assertEqual(helpers.redacted("bad token", ""), "bad token")


if __name__ == "__main__":
    unittest.main()
