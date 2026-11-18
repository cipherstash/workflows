"""The react job's reaction step: how each answer GitHub can give becomes a 👀 reaction, or a warning."""

import json
import pathlib
import shutil
import tempfile
import time
import unittest

from harness import ROOT, StubServer, _yq, closed_port_url, run_step, run_step_as_written

TOKEN = "ghs_unitTestReactionTokenThatMustNeverBePrinted0001"
REPOSITORY = "cipherstash/example"
COMMENT_ID = 2468013579
REACTIONS_PATH = f"/repos/{REPOSITORY}/issues/comments/{COMMENT_ID}/reactions"


def decision(**fields):
    value = {
        "decision": "fire",
        "kind": "comment-command",
        "requester": "alice",
        "pull_request": {"number": 7, "url": "https://github.com/cipherstash/example/pull/7"},
        "comment_id": COMMENT_ID,
        "instructions": None,
        "react": True,
        "text": "Review pull request #7",
    }
    value.update(fields)
    return json.dumps(value, separators=(",", ":"))


def inputs(**overrides):
    env = {"GITHUB_TOKEN": TOKEN, "DECISION": decision()}
    env.update(overrides)
    return env


class ReactTest(unittest.TestCase):
    def react(self, routes=None, api_url=None, substitutions=None, **overrides):
        with StubServer(routes) as stub:
            result = run_step(
                "react",
                "reaction",
                inputs(**overrides),
                api_url=api_url or stub.url,
                repository=REPOSITORY,
                substitutions=substitutions,
            )
        self.stub = stub
        self.assertNotIn(TOKEN, result.everything(), "the job's token was printed or written to an output")
        # A reaction is cosmetic: it never reports an error, only a warning.
        self.assertEqual(result.errors(), [])
        return result

    def assert_reacted(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.warnings(), [])

    def assert_warned(self, result, message):
        # A failed reaction is a failed step in the run; the step's continue-on-error keeps the check green.
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(result.warnings()), 1, result.stdout)
        self.assertIn(message, result.warnings()[0])


class GivenGitHubAddsTheReaction(ReactTest):
    def test_a_new_reaction_is_success(self):
        result = self.react({REACTIONS_PATH: (201, json.dumps({"id": 1, "content": "eyes"}))})
        self.assert_reacted(result)

    def test_a_reaction_that_was_already_there_is_success(self):
        result = self.react({REACTIONS_PATH: (200, json.dumps({"id": 1, "content": "eyes"}))})
        self.assert_reacted(result)

    def test_a_reaction_whose_answer_never_ends_is_success(self):
        # The status says the reaction is there, so the step does not wait for the rest of the answer.
        for status in (200, 201):
            with self.subTest(status=status):
                started = time.monotonic()
                result = self.react({REACTIONS_PATH: StubServer.drip(status)},
                                    substitutions={"TOTAL_SECONDS = 30": "TOTAL_SECONDS = 0.5"})
                self.assert_reacted(result)
                self.assertLess(time.monotonic() - started, 5, "the step waited for the answer's body")

    def test_the_request_is_one_post_of_eyes_with_the_github_headers(self):
        self.react({REACTIONS_PATH: (201, "{}")})
        self.assertEqual(len(self.stub.requests), 1)
        method, path, headers = self.stub.requests[0]
        self.assertEqual((method, path), ("POST", REACTIONS_PATH))
        self.assertEqual(headers.get("Authorization"), f"Bearer {TOKEN}")
        self.assertEqual(headers.get("Accept"), "application/vnd.github+json")
        self.assertEqual(headers.get("X-GitHub-Api-Version"), "2022-11-28")
        self.assertEqual(headers.get("Content-Type"), "application/json")
        self.assertEqual(self.stub.bodies[0], b'{"content":"eyes"}')


class GivenGitHubRefusesTheReaction(ReactTest):
    def test_each_error_status_is_a_warning(self):
        for status in (403, 404, 500):
            with self.subTest(status=status):
                result = self.react({REACTIONS_PATH: (status, json.dumps({"message": "no"}))})
                self.assert_warned(result, f"HTTP {status}")
                self.assertIn("the review was still requested", result.warnings()[0])

    def test_a_redirect_is_not_followed_and_is_a_warning(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                # The redirect points at a second server that would add the reaction, so following it would show.
                with StubServer({"/elsewhere": (201, "{}")}) as elsewhere:
                    result = self.react({REACTIONS_PATH: (status, "", {"Location": f"{elsewhere.url}/elsewhere"})})
                self.assert_warned(result, f"HTTP {status}")
                self.assertEqual(self.stub.paths(), [REACTIONS_PATH])
                self.assertEqual(elsewhere.requests, [], "the redirect was followed")
                self.assertFalse(any(headers.get("Authorization") for _, _, headers in elsewhere.requests))

    def test_an_error_answer_whose_body_never_ends_is_a_warning_with_its_status(self):
        started = time.monotonic()
        result = self.react({REACTIONS_PATH: StubServer.drip(403)},
                            substitutions={"TOTAL_SECONDS = 30": "TOTAL_SECONDS = 0.5"})
        self.assert_warned(result, "HTTP 403")
        self.assertLess(time.monotonic() - started, 5, "the step waited for the answer's body")


class GivenGitHubCannotBeReached(ReactTest):
    def test_no_answer_in_time_is_a_warning(self):
        result = self.react({REACTIONS_PATH: StubServer.HANG},
                            substitutions={"TIMEOUT_SECONDS = 10": "TIMEOUT_SECONDS = 0.5"})
        self.assert_warned(result, "the review was still requested")
        self.assertEqual(len(self.stub.requests), 1, "the request was retried")

    def test_the_whole_request_is_cut_off_after_the_total_time(self):
        result = self.react({REACTIONS_PATH: StubServer.HANG},
                            substitutions={"TOTAL_SECONDS = 30": "TOTAL_SECONDS = 0.5"})
        self.assert_warned(result, "the review was still requested")

    def test_a_connection_closed_without_an_answer_is_a_warning(self):
        result = self.react({REACTIONS_PATH: StubServer.DROP})
        self.assert_warned(result, "the review was still requested")
        self.assertEqual(len(self.stub.requests), 1, "the request was retried")

    def test_a_refused_connection_is_a_warning(self):
        result = self.react(api_url=closed_port_url())
        self.assert_warned(result, "the review was still requested")


class GivenTheStepCannotReact(ReactTest):
    def test_an_invalid_comment_id_is_a_warning_without_a_request(self):
        for comment_id in (None, "", "12a", "1/../2", "12/reactions", "12?x=1", -1, 1.5, 1e30, True, [12], {"id": 12},
                           "１２", "12\n", " 12"):
            with self.subTest(comment_id=comment_id):
                result = self.react(DECISION=decision(comment_id=comment_id))
                self.assert_warned(result, "its id is not a number")
                self.assertEqual(self.stub.requests, [])

    def test_a_decision_with_no_comment_id_is_a_warning_without_a_request(self):
        for value in ("", "not json", "[]", json.dumps({"decision": "fire"})):
            with self.subTest(value=value):
                result = self.react(DECISION=value)
                self.assert_warned(result, "its id is not a number")
                self.assertEqual(self.stub.requests, [])

    def test_a_comment_id_given_as_a_string_of_digits_is_used(self):
        path = "/repos/cipherstash/example/issues/comments/42/reactions"
        result = self.react({path: (201, "{}")}, DECISION=decision(comment_id="42"))
        self.assert_reacted(result)
        self.assertEqual(self.stub.paths(), [path])

    def test_a_large_comment_id_keeps_every_digit(self):
        comment_id = 123456789012345678901234567890
        path = f"/repos/cipherstash/example/issues/comments/{comment_id}/reactions"
        result = self.react({path: (201, "{}")}, DECISION=decision(comment_id=comment_id))
        self.assert_reacted(result)
        self.assertEqual(self.stub.paths(), [path])

    def test_an_empty_token_is_a_warning_without_a_request(self):
        result = self.react(GITHUB_TOKEN="")
        self.assert_warned(result, "GITHUB_TOKEN is empty")
        self.assertEqual(self.stub.requests, [])


NOT_CHECKED_OUT = (
    "Could not get the reaction's step files (checkout failed, or react.py or helpers.py missing); no 👀 reaction was "
    "added, but the review was still requested"
)


class GivenTheStepAsTheWorkflowWritesIt(unittest.TestCase):
    """The step's run script, run by bash. The react job checks out the step file from this repository; if that failed,
    or the file cannot run, the step still shows one warning, adds no reaction and never turns the check red."""

    def run_as_written(self, step_file, **overrides):
        env = inputs(STEP_FILE=str(step_file), CHECKOUT_OUTCOME="success")
        env.update(overrides)
        with StubServer({REACTIONS_PATH: (201, "{}")}) as stub:
            result = run_step_as_written(
                "react", "reaction", env, api_url=stub.url,
                repository=REPOSITORY,
            )
        self.assertEqual(stub.requests, [], "a reaction was attempted")
        self.assertNotIn(TOKEN, result.everything())
        self.assertEqual(result.errors(), [])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(result.warnings()), 1, result.stdout + result.stderr)
        return result

    def run_with_helpers(self, helpers_source):
        with tempfile.TemporaryDirectory() as temp:
            shutil.copy(ROOT / "steps" / "react.py", temp)
            if helpers_source is not None:
                (pathlib.Path(temp) / "helpers.py").write_text(helpers_source, encoding="utf-8")
            return self.run_as_written(pathlib.Path(temp) / "react.py")

    def test_without_the_file_no_reaction_is_added_and_a_warning_says_why(self):
        result = self.run_as_written("/nonexistent/steps/react.py")
        self.assertEqual(
            result.warnings(),
            [NOT_CHECKED_OUT],
        )

    def test_a_failed_checkout_is_a_warning_without_a_reaction_even_if_a_file_is_there(self):
        for outcome in ("failure", "skipped", ""):
            with self.subTest(outcome=outcome):
                result = self.run_as_written(ROOT / "steps" / "react.py", CHECKOUT_OUTCOME=outcome)
                self.assertEqual(result.warnings(), [NOT_CHECKED_OUT])

    def test_with_the_file_the_step_runs_it(self):
        # An empty token stops the file before any request.
        result = self.run_as_written(ROOT / "steps" / "react.py", GITHUB_TOKEN="")
        self.assertIn("GITHUB_TOKEN is empty", result.warnings()[0])

    def test_missing_helpers_are_a_warning_without_a_reaction(self):
        result = self.run_with_helpers(None)
        self.assertEqual(result.warnings(), [NOT_CHECKED_OUT])

    def test_broken_helpers_are_a_warning_without_a_reaction(self):
        result = self.run_with_helpers("def broken(:\n")
        self.assertIn("the shared helper module could not be loaded", result.warnings()[0])

    def test_a_failed_step_does_not_turn_the_check_red(self):
        query = '.jobs.react.steps[] | select(.id == "reaction") | ."continue-on-error"'
        self.assertEqual(_yq(query).strip(), "true")


if __name__ == "__main__":
    unittest.main()
