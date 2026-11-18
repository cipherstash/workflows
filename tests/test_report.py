"""The decide job's last step, which always leaves the fire job a finished decision and writes the run's summary."""

import json
import unittest

from harness import run_step

FIRE = {
    "decision": "fire",
    "kind": "ready",
    "requester": "alice",
    "pull_request": {"number": 7, "url": "https://github.com/cipherstash/example/pull/7"},
    "text": "Review pull request #7 in cipherstash/example: https://github.com/cipherstash/example/pull/7\n\n"
    "Kind of review request: ready.",
}
LOOKUP = {"decision": "lookup", "requester": "alice"}
DECISION_FAILED = {"decision": "skip", "reason": "decision-failed"}
NOT_FINISHED = "The request decision did not finish; no review was requested"


def compact(value):
    return json.dumps(value, separators=(",", ":"))


class ReportTest(unittest.TestCase):
    def report(self, first="", second=""):
        result = run_step("decide", "final", {"FIRST": first, "SECOND": second})
        # The fire job reads this step's output, so the step must never fail.
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def decision(self, result):
        return json.loads(result.outputs["decision"])


class GivenAFinishedDecision(ReportTest):
    def test_a_fire_decision_is_passed_on(self):
        result = self.report(first=compact(LOOKUP), second=compact(FIRE))
        self.assertEqual(self.decision(result), FIRE)
        self.assertEqual(result.errors(), [])
        self.assertEqual(result.summary, "Claude review: requested (ready, by @alice, pull request #7)\n")

    def test_a_skip_decision_is_passed_on(self):
        result = self.report(first=compact({"decision": "skip", "reason": "draft"}))
        self.assertEqual(self.decision(result), {"decision": "skip", "reason": "draft"})
        self.assertEqual(result.errors(), [])
        self.assertEqual(result.summary, "Claude review: skipped (draft)\n")

    def test_the_decision_after_the_lookup_wins_over_the_first(self):
        result = self.report(first=compact(FIRE), second=compact({"decision": "skip", "reason": "not-a-team-member"}))
        self.assertEqual(self.decision(result), {"decision": "skip", "reason": "not-a-team-member"})

    def test_a_failed_membership_lookup_is_passed_on_with_an_error(self):
        skip = {"decision": "skip", "reason": "membership-lookup-failed"}
        result = self.report(first=compact(LOOKUP), second=compact(skip))
        self.assertEqual(self.decision(result), skip)
        self.assertEqual(result.errors(), ["Team membership could not be checked, so no review was requested"])
        self.assertEqual(result.summary, "Claude review: skipped (membership-lookup-failed)\n")

    def test_a_decision_written_over_several_lines_is_passed_on_whole(self):
        result = self.report(first=json.dumps(FIRE, indent=2))
        self.assertEqual(self.decision(result), FIRE)
        self.assertEqual(result.errors(), [])

    def test_a_fire_decision_of_an_unexpected_shape_does_not_fail_the_step(self):
        odd = {"decision": "fire", "kind": "ready", "requester": "alice", "pull_request": "7"}
        result = self.report(first=compact(odd))
        self.assertEqual(self.decision(result), odd)


class GivenNoFinishedDecision(ReportTest):
    def assert_decision_failed(self, result):
        self.assertEqual(self.decision(result), DECISION_FAILED)
        self.assertEqual(result.errors(), [NOT_FINISHED])
        self.assertEqual(result.summary, "Claude review: skipped (decision-failed)\n")

    def test_a_leftover_lookup_becomes_decision_failed(self):
        self.assert_decision_failed(self.report(first=compact(LOOKUP)))

    def test_no_decision_at_all_becomes_decision_failed(self):
        self.assert_decision_failed(self.report())

    def test_a_decision_that_is_not_json_becomes_decision_failed(self):
        self.assert_decision_failed(self.report(first='{"decision": "fire"'))

    def test_json_that_is_not_an_object_becomes_decision_failed(self):
        for value in ("[]", '"fire"', "null", "7"):
            with self.subTest(value=value):
                self.assert_decision_failed(self.report(first=value))

    def test_an_unknown_decision_becomes_decision_failed(self):
        self.assert_decision_failed(self.report(first=compact({"decision": "maybe"})))

    def test_a_value_with_newlines_cannot_write_a_second_output(self):
        value = 'not json\ndecision={"decision":"fire"}\nghadelim_x'
        self.assert_decision_failed(self.report(first=value))


if __name__ == "__main__":
    unittest.main()
