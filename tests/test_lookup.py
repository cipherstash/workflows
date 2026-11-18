"""The decide job's membership lookup step: how each answer GitHub can give becomes a membership."""

import json
import time
import unittest
from typing import ClassVar

from harness import ROOT, StubServer, closed_port_url, run_step, run_step_as_written

TOKEN = "ghs_unitTestTokenThatMustNeverBePrinted0001"
MEMBERSHIP_PATH = "/orgs/cipherstash/teams/developers/memberships/alice"
TEAM_PATH = "/orgs/cipherstash/teams/developers"


def inputs(**overrides):
    env = {
        "APP_TOKEN": TOKEN,
        "TOKEN_OUTCOME": "success",
        "ORG": "cipherstash",
        "TEAM": "developers",
        "REQUESTER": "alice",
    }
    env.update(overrides)
    return env


class LookupTest(unittest.TestCase):
    def lookup(self, routes=None, api_url=None, substitutions=None, **overrides):
        with StubServer(routes) as stub:
            result = run_step("decide", "lookup", inputs(**overrides), api_url=api_url or stub.url,
                              substitutions=substitutions)
        self.stub = stub
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(TOKEN, result.everything(), "the App token was printed or written to an output")
        return result

    def assert_member(self, result, membership):
        self.assertEqual(result.outputs.get("membership"), membership)
        self.assertEqual(result.errors(), [])

    def assert_failed(self, result, message):
        self.assertEqual(result.outputs.get("membership"), "lookup-failed")
        self.assertEqual(len(result.errors()), 1, result.stdout)
        self.assertIn(message, result.errors()[0])


class GivenGitHubAnswersTheMembership(LookupTest):
    def test_an_active_membership_is_a_member(self):
        result = self.lookup({MEMBERSHIP_PATH: (200, json.dumps({"state": "active", "role": "member"}))})
        self.assert_member(result, "member")

    def test_a_pending_membership_is_not_a_member(self):
        result = self.lookup({MEMBERSHIP_PATH: (200, json.dumps({"state": "pending", "role": "member"}))})
        self.assert_member(result, "not-member")

    def test_a_membership_with_no_state_is_not_a_member(self):
        result = self.lookup({MEMBERSHIP_PATH: (200, json.dumps({"role": "member"}))})
        self.assert_member(result, "not-member")

    def test_the_team_is_not_asked_about_when_the_membership_is_found(self):
        self.lookup({MEMBERSHIP_PATH: (200, json.dumps({"state": "active"}))})
        self.assertEqual(self.stub.paths(), [MEMBERSHIP_PATH])

    def test_the_token_is_sent_as_a_bearer_authorization_header(self):
        self.lookup({MEMBERSHIP_PATH: (200, json.dumps({"state": "active"}))})
        _, _, headers = self.stub.requests[0]
        self.assertEqual(headers.get("Authorization"), f"Bearer {TOKEN}")
        self.assertEqual(headers.get("Accept"), "application/vnd.github+json")
        self.assertEqual(headers.get("X-GitHub-Api-Version"), "2022-11-28")


class GivenGitHubAnswersNotFound(LookupTest):
    def test_an_existing_team_means_not_a_member(self):
        result = self.lookup({TEAM_PATH: (200, json.dumps({"slug": "developers"}))})
        self.assert_member(result, "not-member")
        self.assertEqual(self.stub.paths(), [MEMBERSHIP_PATH, TEAM_PATH])

    def test_a_missing_team_fails_the_lookup(self):
        result = self.lookup({})
        self.assert_failed(result, "team cipherstash/developers was not found (HTTP 404)")

    def test_a_team_lookup_that_errors_fails_the_lookup(self):
        result = self.lookup({TEAM_PATH: (500, "{}")})
        self.assert_failed(result, "team cipherstash/developers was not found (HTTP 500)")


class GivenGitHubAnswersSomethingElse(LookupTest):
    def test_a_server_error_fails_the_lookup(self):
        result = self.lookup({MEMBERSHIP_PATH: (500, "{}")})
        self.assert_failed(result, "GitHub answered HTTP 500")

    def test_forbidden_fails_the_lookup(self):
        result = self.lookup({MEMBERSHIP_PATH: (403, json.dumps({"message": "Resource not accessible"}))})
        self.assert_failed(result, "GitHub answered HTTP 403")

    def test_a_redirect_is_not_followed_and_fails_the_lookup(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                # The redirect points at a second server that would answer an active membership, so following it would
                # show.
                active = (200, json.dumps({"state": "active"}))
                with StubServer({"/elsewhere": active}) as elsewhere:
                    result = self.lookup({MEMBERSHIP_PATH: (status, "", {"Location": f"{elsewhere.url}/elsewhere"})})
                self.assert_failed(result, f"GitHub answered HTTP {status}")
                self.assertEqual(self.stub.paths(), [MEMBERSHIP_PATH])
                self.assertEqual(elsewhere.requests, [], "the redirect was followed")
                self.assertFalse(any(headers.get("Authorization") for _, _, headers in elsewhere.requests))

    def test_a_body_that_is_not_json_fails_the_lookup(self):
        result = self.lookup({MEMBERSHIP_PATH: (200, "<html>not json</html>")})
        self.assert_failed(result, "GitHub's answer was not a membership")

    def test_a_body_that_is_not_an_object_fails_the_lookup(self):
        result = self.lookup({MEMBERSHIP_PATH: (200, json.dumps(["active"]))})
        self.assert_failed(result, "GitHub's answer was not a membership")

    def test_an_answer_larger_than_a_membership_fails_the_lookup_as_malformed(self):
        body = json.dumps({"state": "active", "padding": "x" * 70_000})
        result = self.lookup({MEMBERSHIP_PATH: (200, body)})
        self.assert_failed(result, "GitHub's answer was not a membership")

    def test_an_answer_that_never_ends_is_cut_off_after_the_total_time(self):
        # The stub sends a byte every 50 ms, so no single read times out (the per-read timeout stays at its 10 s): only
        # the total time can end the request.
        started = time.monotonic()
        result = self.lookup(
            {MEMBERSHIP_PATH: StubServer.DRIP}, substitutions={"TOTAL_SECONDS = 30": "TOTAL_SECONDS = 0.5"}
        )
        elapsed = time.monotonic() - started
        self.assert_failed(result, "Could not reach GitHub to look up team membership (TotalTimeout)")
        self.assertLess(elapsed, 5, "the lookup ran on past its total time")

    def test_no_answer_in_time_fails_the_lookup(self):
        result = self.lookup(
            {MEMBERSHIP_PATH: StubServer.HANG}, substitutions={"TIMEOUT_SECONDS = 10": "TIMEOUT_SECONDS = 0.5"}
        )
        self.assert_failed(result, "Could not reach GitHub")

    def test_a_connection_closed_without_an_answer_fails_the_lookup(self):
        result = self.lookup({MEMBERSHIP_PATH: StubServer.DROP})
        self.assert_failed(result, "Could not reach GitHub")

    def test_a_refused_connection_fails_the_lookup(self):
        result = self.lookup(api_url=closed_port_url())
        self.assert_failed(result, "Could not reach GitHub")


class GivenNoUsableToken(LookupTest):
    def test_an_empty_token_fails_the_lookup_without_asking_github(self):
        result = self.lookup(APP_TOKEN="")
        self.assert_failed(result, "Could not create the membership App's token")
        self.assertEqual(self.stub.paths(), [])

    def test_a_failed_token_step_fails_the_lookup_without_asking_github(self):
        result = self.lookup(TOKEN_OUTCOME="failure")
        self.assert_failed(result, "Could not create the membership App's token")
        self.assertEqual(self.stub.paths(), [])

    def test_a_skipped_token_step_fails_the_lookup(self):
        result = self.lookup(TOKEN_OUTCOME="skipped", APP_TOKEN="")
        self.assert_failed(result, "Could not create the membership App's token")

    def test_a_missing_token_fails_the_lookup(self):
        env = inputs()
        del env["APP_TOKEN"]
        with StubServer() as stub:
            result = run_step("decide", "lookup", env, api_url=stub.url)
        self.assertEqual(result.outputs.get("membership"), "lookup-failed")
        self.assertIn("Could not create the membership App's token", result.errors()[0])
        self.assertEqual(stub.paths(), [])


class GivenANameThatIsNotValid(LookupTest):
    INVALID: ClassVar[dict[str, list[str]]] = {
        "ORG": ["", "-cipherstash", "cipher/stash", "..", "cipher stash", "cipherstash\n"],
        "TEAM": ["", ".", "..", "-team", "dev/../admins", "dev%2F", "dév"],
        "REQUESTER": ["", "-alice", "alice-", "al--ice", "al.ice", "alice/x", "alice\n", "alice?x=1"],
    }

    def test_a_name_with_a_newline_cannot_add_a_second_annotation(self):
        # The token step failed, so the message names the organisation, which is where the newline arrives.
        result = self.lookup(APP_TOKEN="", ORG="cipherstash\n::error::injected")
        self.assert_failed(result, "installed on cipherstash%0A::error::injected with Members: read")

    def test_each_invalid_name_fails_the_lookup_without_asking_github(self):
        for name, values in self.INVALID.items():
            for value in values:
                with self.subTest(name=name, value=value):
                    result = self.lookup(**{name: value})
                    self.assert_failed(result, "is not a valid GitHub name")
                    self.assertEqual(self.stub.paths(), [])

    def test_valid_names_reach_github(self):
        cases = [
            {"ORG": "Cipher-Stash9"},
            {"TEAM": "_dev.team-1"},
            {"REQUESTER": "a-b_c9"},
        ]
        for case in cases:
            with self.subTest(**case):
                env = inputs(**case)
                path = f"/orgs/{env['ORG']}/teams/{env['TEAM']}/memberships/{env['REQUESTER']}"
                result = self.lookup({path: (200, json.dumps({"state": "active"}))}, **case)
                self.assert_member(result, "member")


class GivenTheStepAsTheWorkflowWritesIt(unittest.TestCase):
    def test_the_run_line_starts_the_lookup_file(self):
        env = inputs(TOKEN_OUTCOME="failure", APP_TOKEN="", STEP_FILE=str(ROOT / "steps" / "lookup.py"))
        result = run_step_as_written("decide", "lookup", env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.outputs.get("membership"), "lookup-failed")
        self.assertEqual(len(result.errors()), 1, result.stdout)


if __name__ == "__main__":
    unittest.main()
