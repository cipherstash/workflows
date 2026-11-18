"""The react job's reaction step: how each answer GitHub can give becomes a 👀 reaction, or a warning."""

import json
import unittest

from harness import StubServer, closed_port_url, run_step

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

    def test_the_request_is_one_post_of_eyes_with_the_github_headers(self):
        self.react({REACTIONS_PATH: (201, "{}")})
        self.assertEqual(len(self.stub.requests), 1)
        method, path, headers = self.stub.requests[0]
        self.assertEqual((method, path), ("POST", REACTIONS_PATH))
        self.assertEqual(headers.get("Authorization"), f"Bearer {TOKEN}")
        self.assertEqual(headers.get("Accept"), "application/vnd.github+json")
        self.assertEqual(headers.get("X-GitHub-Api-Version"), "2022-11-28")
        self.assertEqual(self.stub.bodies[0], b'{"content":"eyes"}')


class GivenGitHubRefusesTheReaction(ReactTest):
    def test_each_error_status_is_a_warning(self):
        for status in (403, 404, 500):
            with self.subTest(status=status):
                result = self.react({REACTIONS_PATH: (status, json.dumps({"message": "no"}))})
                self.assert_warned(result, f"HTTP {status}")
                self.assertIn("the review was still requested", result.warnings()[0])

    def test_a_redirect_is_not_followed_and_is_a_warning(self):
        result = self.react({REACTIONS_PATH: (302, "")})
        self.assert_warned(result, "HTTP 302")
        self.assertEqual(self.stub.paths(), [REACTIONS_PATH])


class GivenGitHubCannotBeReached(ReactTest):
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
                           "１２", " 12"):
            with self.subTest(comment_id=comment_id):
                result = self.react(DECISION=decision(comment_id=comment_id))
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


if __name__ == "__main__":
    unittest.main()
