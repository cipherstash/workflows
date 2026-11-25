"""The example caller, examples/claude-review.yml: which events its calling job's `if:` lets through."""

import unittest

from harness import EXAMPLE, _yq
from test_self_test import COMMANDS, NOT_COMMANDS, Expression, comment_event, context_of, decision_for, event


def runs(payload):
    """Whether the example's calling job starts for the payload."""
    return Expression(_yq(".jobs.review.if", EXAMPLE).strip(), context_of(payload)).evaluate()


class ExampleCallerTest(unittest.TestCase):
    def test_every_comment_command_starts_the_job(self):
        for body in COMMANDS:
            with self.subTest(body=body):
                self.assertTrue(runs(comment_event(body)))

    def test_a_comment_that_only_mentions_the_review_agent_later_starts_no_job(self):
        for body in (
            "thanks @cipherstash-agent",
            "Please do this.\n@cipherstash-agent review",
            "> @cipherstash-agent review",
        ):
            with self.subTest(body=body):
                self.assertEqual(decision_for(comment_event(body)), {"decision": "skip", "reason": "not-a-command"})
                self.assertFalse(runs(comment_event(body)))

    def test_no_comment_it_drops_is_a_comment_command(self):
        # The filter may let through comments the shared workflow then skips, but must never drop a command.
        for body in COMMANDS + NOT_COMMANDS:
            with self.subTest(body=body):
                if decision_for(comment_event(body))["decision"] == "fire":
                    self.assertTrue(runs(comment_event(body)))

    def test_a_comment_on_a_plain_issue_starts_no_job(self):
        self.assertFalse(runs(comment_event(on_pull_request=False)))

    def test_ready_events_start_the_job_and_only_review_requests_for_the_review_agent_do(self):
        self.assertTrue(runs(event("opened")))
        self.assertTrue(runs(event("review_requested", reviewer="cipherstash-agent")))
        self.assertFalse(runs(event("review_requested", reviewer="monalisa")))


if __name__ == "__main__":
    unittest.main()
