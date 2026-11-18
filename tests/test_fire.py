"""The fire job's send step: how each answer the routine trigger can give becomes sent, or an error."""

import json
import shutil
import tempfile
import time
import unittest

from harness import ROOT, StubServer, _yq, closed_port_url, run_step, run_step_as_written

TOKEN = "sk-ant-unitTestRoutineTokenThatMustNeverBePrinted0001"
TRIGGER_ID = "trig_01AbCdEf"
FIRE_PATH = f"/v1/claude_code/routines/{TRIGGER_ID}/fire"
# The step's production address, which a test swaps for the stub's. No input or event can change it.
API_BASE_URL = "https://api.anthropic.com"
TEXT = (
    "Review pull request #7 in cipherstash/example: https://github.com/cipherstash/example/pull/7\n\n"
    "Kind of review request: ready."
)


def decision(text=TEXT):
    return json.dumps(
        {
            "decision": "fire",
            "kind": "ready",
            "requester": "alice",
            "pull_request": {"number": 7, "url": "https://github.com/cipherstash/example/pull/7"},
            "text": text,
        },
        separators=(",", ":"),
    )


def inputs(**overrides):
    env = {"CLAUDE_ROUTINE_TOKEN": TOKEN, "TRIGGER_ID": TRIGGER_ID, "DECISION": decision()}
    env.update(overrides)
    return env


class FireTest(unittest.TestCase):
    def fire(self, routes=None, base_url=None, substitutions=None, **overrides):
        with StubServer(routes) as stub:
            result = run_step(
                "fire",
                "send",
                inputs(**overrides),
                substitutions={API_BASE_URL: base_url or stub.url, **(substitutions or {})},
            )
        self.stub = stub
        self.assertNotIn(TOKEN, result.everything(), "the routine token was printed or written to an output")
        return result

    def assert_sent(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.outputs, {"sent": "true"})
        self.assertEqual(result.errors(), [])

    def assert_not_sent(self, result, message):
        # A failed request is a failed step in the run; the job's continue-on-error keeps the check green.
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("sent", result.outputs)
        self.assertEqual(len(result.errors()), 1, result.stdout)
        self.assertIn(message, result.errors()[0])


class GivenTheRoutineAcceptsTheRequest(FireTest):
    def test_each_success_status_is_sent(self):
        for status in (200, 201, 202):
            with self.subTest(status=status):
                result = self.fire({FIRE_PATH: (status, json.dumps({"type": "routine_fire", "id": "ses_1"}))})
                self.assert_sent(result)

    def test_the_request_is_one_post_to_the_trigger_with_the_routine_headers(self):
        self.fire({FIRE_PATH: (200, "{}")})
        self.assertEqual(len(self.stub.requests), 1)
        method, path, headers = self.stub.requests[0]
        self.assertEqual((method, path), ("POST", FIRE_PATH))
        self.assertEqual(headers.get("Authorization"), f"Bearer {TOKEN}")
        self.assertEqual(headers.get("anthropic-version"), "2023-06-01")
        self.assertEqual(headers.get("anthropic-beta"), "experimental-cc-routine-2026-04-01")
        self.assertEqual(headers.get("Content-Type"), "application/json")

    def test_an_accepted_request_whose_answer_never_ends_is_sent(self):
        # The status says the routine accepted the request, so a session may have started: the step does not wait for
        # the rest of the answer, and a body that never ends cannot turn the request into "not sent".
        for status in (200, 201):
            with self.subTest(status=status):
                started = time.monotonic()
                result = self.fire({FIRE_PATH: StubServer.drip(status)},
                                   substitutions={"TOTAL_SECONDS = 30": "TOTAL_SECONDS = 0.5"})
                self.assert_sent(result)
                self.assertLess(time.monotonic() - started, 5, "the step waited for the answer's body")

    def test_the_body_is_the_decision_text_and_nothing_else(self):
        self.fire({FIRE_PATH: (200, "{}")})
        self.assertEqual(json.loads(self.stub.bodies[0]), {"text": TEXT})

    def test_text_with_quotes_newlines_and_unicode_reaches_the_routine_unchanged(self):
        text = 'Review "this" \\ one\nline two\r\n\ttabbed 👀 é ü   {"text": "x"} $(echo hi) `id`'
        self.fire({FIRE_PATH: (200, "{}")}, DECISION=decision(text))
        self.assertEqual(json.loads(self.stub.bodies[0].decode("utf-8")), {"text": text})


class GivenTheRoutineRejectsTheRequest(FireTest):
    def test_each_error_status_is_reported_and_not_sent(self):
        for status in (400, 401, 500):
            with self.subTest(status=status):
                body = json.dumps({"type": "error", "error": {"type": "x", "message": "trigger said no"}})
                result = self.fire({FIRE_PATH: (status, body)})
                self.assert_not_sent(result, f"HTTP {status}")
                self.assertIn("trigger said no", result.errors()[0])

    def test_a_long_error_body_is_cut_short(self):
        result = self.fire({FIRE_PATH: (500, "x" * 5000)})
        self.assert_not_sent(result, "HTTP 500")
        self.assertLess(len(result.errors()[0]), 500)

    def test_an_error_body_cannot_add_a_second_annotation(self):
        result = self.fire({FIRE_PATH: (400, "bad\n::error::injected\r\n::warning::also")})
        self.assert_not_sent(result, "HTTP 400")
        commands = [line for line in result.stdout.splitlines() if line.startswith("::")]
        self.assertEqual(len(commands), 1, result.stdout)
        self.assertTrue(commands[0].startswith("::error::"), result.stdout)

    def test_an_error_body_that_echoes_the_token_does_not_print_it(self):
        result = self.fire({FIRE_PATH: (401, json.dumps({"message": f"bad token Bearer {TOKEN}"}))})
        self.assert_not_sent(result, "HTTP 401")

    def test_an_error_answer_whose_body_never_ends_is_cut_off_and_reported_by_its_status(self):
        # The stub sends a byte every 50 ms, so no single read times out (the per-read timeout stays at its 10 s): only
        # the total time can end the read of the error's body.
        started = time.monotonic()
        result = self.fire({FIRE_PATH: StubServer.drip(500)},
                           substitutions={"TOTAL_SECONDS = 30": "TOTAL_SECONDS = 0.5"})
        elapsed = time.monotonic() - started
        self.assert_not_sent(result, "The routine trigger answered HTTP 500; no review was requested")
        self.assertLess(elapsed, 5, "the request ran on past its total time")

    def test_a_redirect_is_not_followed_and_not_sent(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                # The redirect points at a second server that would accept the request, so following it would show.
                with StubServer({"/elsewhere": (201, "{}")}) as elsewhere:
                    result = self.fire({FIRE_PATH: (status, "", {"Location": f"{elsewhere.url}/elsewhere"})})
                self.assert_not_sent(result, f"HTTP {status}")
                self.assertEqual(self.stub.paths(), [FIRE_PATH])
                self.assertEqual(elsewhere.requests, [], "the redirect was followed")
                self.assertFalse(any(headers.get("Authorization") for _, _, headers in elsewhere.requests))


class GivenTheRoutineCannotBeReached(FireTest):
    def test_no_answer_in_time_is_not_sent(self):
        result = self.fire({FIRE_PATH: StubServer.HANG}, substitutions={"TIMEOUT_SECONDS = 10": "TIMEOUT_SECONDS = 0.5"})
        self.assert_not_sent(result, "Could not reach the routine trigger")
        self.assertEqual(len(self.stub.requests), 1, "the request was retried")

    def test_an_answer_that_never_comes_is_cut_off_after_the_total_time(self):
        # The per-read timeout stays at its 10 s, so only the total time can end the request this soon.
        started = time.monotonic()
        result = self.fire({FIRE_PATH: StubServer.HANG},
                           substitutions={"TOTAL_SECONDS = 30": "TOTAL_SECONDS = 0.5"})
        elapsed = time.monotonic() - started
        self.assert_not_sent(result, "Could not reach the routine trigger (TotalTimeout)")
        self.assertLess(elapsed, 5, "the request ran on past its total time")
        self.assertEqual(len(self.stub.requests), 1, "the request was retried")

    def test_a_connection_closed_without_an_answer_is_not_sent(self):
        result = self.fire({FIRE_PATH: StubServer.DROP})
        self.assert_not_sent(result, "Could not reach the routine trigger")
        self.assertEqual(len(self.stub.requests), 1, "the request was retried")

    def test_a_refused_connection_is_not_sent(self):
        result = self.fire(base_url=closed_port_url())
        self.assert_not_sent(result, "Could not reach the routine trigger")


class GivenTheStepIsNotSetUp(FireTest):
    def test_an_empty_token_is_reported_without_a_request(self):
        result = self.fire(CLAUDE_ROUTINE_TOKEN="")
        self.assert_not_sent(result, "CLAUDE_ROUTINE_TOKEN is empty")
        self.assertEqual(self.stub.requests, [])

    def test_an_invalid_trigger_id_is_reported_without_a_request(self):
        for trigger_id in ("", "trig_", "trig", "TRIG_abc", "trig_abc/../x", "trig_abc?x=1", "trig_abc\n", "trig_ab-c",
                           "trig_abc def", "xtrig_abc", "trig_abc#"):
            with self.subTest(trigger_id=trigger_id):
                result = self.fire(TRIGGER_ID=trigger_id)
                self.assert_not_sent(result, "routine-trigger-id is not a trigger id")
                self.assertEqual(self.stub.requests, [])

    def test_a_decision_with_no_text_is_reported_without_a_request(self):
        for value in ("", "not json", "[]", json.dumps({"decision": "fire"}), json.dumps({"text": 7})):
            with self.subTest(value=value):
                result = self.fire(DECISION=value)
                self.assert_not_sent(result, "has no request text")
                self.assertEqual(self.stub.requests, [])


class GivenTheStepFileWasNotCheckedOut(unittest.TestCase):
    # The fire job checks out the step file from this repository. If that failed, the step's own script reports it.

    def test_without_the_file_nothing_is_sent_and_an_error_says_why(self):
        result = run_step_as_written("fire", "send", inputs(STEP_FILE="/nonexistent/steps/fire.py", CHECKOUT_OUTCOME="success"))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("sent", result.outputs)
        self.assertEqual(
            result.errors(),
            ["Could not get the routine call's step file (checkout failed or file missing); no review was requested"],
        )
        self.assertNotIn(TOKEN, result.everything())

    def test_a_failed_step_does_not_turn_the_check_red(self):
        query = '.jobs.fire.steps[] | select(.id == "send") | ."continue-on-error"'
        self.assertEqual(_yq(query).strip(), "true")

    def test_with_the_file_the_step_runs_it(self):
        # An invalid trigger id stops the file before any request, so this runs without a network.
        result = run_step_as_written(
            "fire", "send", inputs(CHECKOUT_OUTCOME="success", STEP_FILE=str(ROOT / "steps" / "fire.py"), TRIGGER_ID="not-a-trigger")
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("sent", result.outputs)
        self.assertEqual(len(result.errors()), 1, result.stdout)
        self.assertIn("routine-trigger-id is not a trigger id", result.errors()[0])

    def assert_one_error_and_nothing_sent(self, result):
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("sent", result.outputs)
        self.assertEqual(
            result.errors(),
            ["Could not get the routine call's step file (checkout failed or file missing); no review was requested"],
        )
        self.assertNotIn(TOKEN, result.everything())

    def test_with_the_file_but_not_its_helper_module_one_error_says_why_and_nothing_is_sent(self):
        # A partial checkout: fire.py imports helpers.py outside any try, so running it would end in a traceback.
        with tempfile.TemporaryDirectory() as temp:
            shutil.copy(ROOT / "steps" / "fire.py", temp)
            result = run_step_as_written(
                "fire", "send", inputs(CHECKOUT_OUTCOME="success", STEP_FILE=f"{temp}/fire.py")
            )
        self.assert_one_error_and_nothing_sent(result)

    def test_when_the_checkout_failed_nothing_is_sent_even_if_the_files_are_there(self):
        result = run_step_as_written(
            "fire", "send", inputs(CHECKOUT_OUTCOME="failure", STEP_FILE=str(ROOT / "steps" / "fire.py"))
        )
        self.assert_one_error_and_nothing_sent(result)


if __name__ == "__main__":
    unittest.main()
