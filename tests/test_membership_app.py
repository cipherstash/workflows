"""How the review workflow names the membership GitHub App, and how its callers pass it on.

The App is named by its client ID, a readable organisation variable passed as an input, rather than by its app ID as a
secret: actions/create-github-app-token deprecated its app-id input in favour of client-id, and an ID is not secret.
Only the private key stays a secret.
"""

import unittest

from harness import ROOT, SELF_TEST, _yq

EXAMPLE = ROOT / "examples" / "claude-review.yml"
TOKEN_STEP = '.jobs.decide.steps[] | select(.id == "app-token")'


class MembershipAppTest(unittest.TestCase):
    def test_the_workflow_takes_the_client_id_as_a_required_input(self) -> None:
        self.assertEqual(_yq('.on.workflow_call.inputs."app-client-id".type').strip(), "string")
        self.assertEqual(_yq('.on.workflow_call.inputs."app-client-id".required').strip(), "true")

    def test_the_workflow_no_longer_takes_an_app_id_secret(self) -> None:
        self.assertEqual(
            sorted(_yq(".on.workflow_call.secrets | keys | .[]").split()),
            ["CLAUDE_REVIEW_APP_PRIVATE_KEY", "CLAUDE_ROUTINE_TOKEN"],
        )

    def test_the_token_step_names_the_app_by_its_client_id(self) -> None:
        self.assertEqual(_yq(f'{TOKEN_STEP} | .with."client-id"').strip(), "${{ inputs.app-client-id }}")
        self.assertEqual(_yq(f'{TOKEN_STEP} | .with | has("app-id")').strip(), "false")

    def test_callers_pass_the_client_id_from_the_organisation_variable(self) -> None:
        for caller in (EXAMPLE, SELF_TEST):
            with self.subTest(caller=caller.name):
                self.assertEqual(
                    _yq('.jobs.review.with."app-client-id"', caller).strip(), "${{ vars.CLAUDE_REVIEW_APP_CLIENT_ID }}"
                )
                self.assertEqual(_yq('.jobs.review.secrets | has("CLAUDE_REVIEW_APP_ID")', caller).strip(), "false")


if __name__ == "__main__":
    unittest.main()
