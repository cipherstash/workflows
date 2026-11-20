"""scripts/setup-organisation.sh, run against a stand-in `gh`, a stand-in browser opener and typed answers.

The stand-in `gh` keeps the organisation's state in a JSON file: its secrets and variables, the GitHub Apps installed on
it, the apps GitHub knows by name, and what the manifest conversion answers. It answers the few `gh api`, `gh secret
set` and `gh variable set` calls the script makes, changes the state as GitHub would, and logs every call, with what it
read on stdin, so a test can check that a private key or token only ever travels on stdin.

The stand-in opener (`xdg-open`, as on Linux) logs each address it is given. Given the manifest listener's address, it
plays the browser: it loads the form, keeps the manifest it would post to GitHub, and calls the listener back with a
code, the way GitHub redirects after "Create GitHub App".
"""

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from harness import ROOT

SCRIPT = ROOT / "scripts" / "setup-organisation.sh"
ORG = "cipherstash"
CLIENT_ID = "Iv23liUnitTestClientId01"
PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIunittestkeythatmustneverbeprinted\n-----END RSA PRIVATE KEY-----\n"
ROUTINE_TOKEN = "sk-ant-unitTestRoutineTokenThatMustNeverBePrinted"
TRIGGER_ID = "trig_01UnitTestTrigger"
CODE = "a1b2c3d4e5f6"

FAKE_GH = """#!PYTHON
import json, os, sys

state_path = os.environ["FAKE_GH_STATE"]
with open(state_path) as f:
    state = json.load(f)
args = sys.argv[1:]
entry = {"argv": args}
if args[:2] in (["secret", "set"],):
    entry["stdin"] = sys.stdin.read()
with open(os.environ["FAKE_GH_LOG"], "a") as f:
    f.write(json.dumps(entry) + "\\n")
org = state["org"]


def save():
    with open(state_path, "w") as f:
        json.dump(state, f)


def answer(body):
    print(json.dumps(body))
    sys.exit(0)


def fail(message="gh: Not Found (HTTP 404)"):
    print(message, file=sys.stderr)
    sys.exit(1)


if args[0] == "api":
    rest = [a for a in args[1:] if a != "--paginate"]
    if rest == [f"orgs/{org}/actions/secrets/public-key"]:
        answer({"key_id": "1", "key": "x"}) if state.get("auth_ok", True) else fail("gh: Forbidden (HTTP 403)")
    if rest == [f"orgs/{org}/actions/secrets"]:
        answer({"secrets": [{"name": n, "visibility": "all"} for n in state["secrets"]]})
    if rest == [f"orgs/{org}/actions/variables"]:
        answer({"variables": [{"name": n, "value": v, "visibility": "all"} for n, v in state["variables"].items()]})
    if rest == [f"orgs/{org}/installations"]:
        if state.get("installations_fail"):
            fail("gh: Forbidden (HTTP 403)")
        answer({"total_count": len(state["installations"]), "installations": state["installations"]})
    if len(rest) == 1 and rest[0].startswith("apps/"):
        app = state["apps"].get(rest[0][len("apps/"):])
        answer(app) if app else fail()
    if rest[:2] == ["-X", "POST"] and rest[2] == f"app-manifests/{state['code']}/conversions":
        conversion = state["conversion"]
        if state.get("install_on_conversion"):
            state["installations"].append({"app_slug": conversion["slug"], "client_id": conversion["client_id"],
                                           "permissions": {"members": "read"}, "suspended_at": None})
            save()
        answer(conversion)
    fail()
if args[:2] == ["secret", "set"] and args[3:] == ["--org", org, "--visibility", "all"]:
    state["secrets"][args[2]] = entry["stdin"]
    save()
    sys.exit(0)
if args[:2] == ["variable", "set"] and args[3:7] == ["--org", org, "--visibility", "all"] and args[7] == "--body":
    state["variables"][args[2]] = args[8]
    save()
    sys.exit(0)
print("fake gh: unexpected call " + " ".join(args), file=sys.stderr)
sys.exit(64)
"""

FAKE_OPENER = """#!PYTHON
import html.parser, os, subprocess, sys, urllib.parse, urllib.request

url = sys.argv[1]
with open(os.environ["FAKE_OPENER_LOG"], "a") as f:
    f.write(url + "\\n")
if url.startswith("http://127.0.0.1:") and not os.environ.get("FAKE_BROWSER_CHILD"):
    # Play the browser in the background, so the opener returns at once, as a real one does.
    subprocess.Popen([sys.executable, __file__, url], env={**os.environ, "FAKE_BROWSER_CHILD": "1"},
                     start_new_session=True)
elif os.environ.get("FAKE_BROWSER_CHILD"):
    page = urllib.request.urlopen(url, timeout=10).read().decode()

    class Form(html.parser.HTMLParser):
        action = manifest = None

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "form":
                self.action = attrs["action"]
            if tag == "input" and attrs.get("name") == "manifest":
                self.manifest = attrs["value"]

    form = Form()
    form.feed(page)
    with open(os.environ["FAKE_BROWSER_FORM"], "w") as f:
        f.write(form.action + "\\n" + form.manifest)
    state = urllib.parse.parse_qs(urllib.parse.urlparse(form.action).query)["state"][0]
    redirect = __import__("json").loads(form.manifest)["redirect_url"]
    query = urllib.parse.urlencode({"code": os.environ["FAKE_BROWSER_CODE"], "state": state})
    urllib.request.urlopen(redirect + "?" + query, timeout=10).read()
"""


def base_state(**overrides):
    state = {
        "org": ORG,
        "secrets": {},
        "variables": {},
        "installations": [],
        "apps": {},
        "code": CODE,
        "conversion": {
            "id": 123,
            "slug": "cipherstash-review-membership",
            "client_id": CLIENT_ID,
            "client_secret": "unused-client-secret",
            "webhook_secret": None,
            "pem": PEM,
            "html_url": "https://github.com/apps/cipherstash-review-membership",
        },
        "install_on_conversion": True,
    }
    state.update(overrides)
    return state


def installed(slug="cipherstash-review-membership", client_id=CLIENT_ID, members="read"):
    return {"app_slug": slug, "client_id": client_id, "permissions": {"members": members}, "suspended_at": None}


def complete_state(**overrides):
    """An organisation with all four settings and the app installed."""
    state = base_state(
        secrets={"CLAUDE_REVIEW_APP_PRIVATE_KEY": PEM, "CLAUDE_ROUTINE_TOKEN": ROUTINE_TOKEN},
        variables={"CLAUDE_REVIEW_APP_CLIENT_ID": CLIENT_ID, "CLAUDE_ROUTINE_TRIGGER_ID": TRIGGER_ID},
        installations=[installed()],
    )
    state.update(overrides)
    return state


class SetupTest(unittest.TestCase):
    state: dict

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = pathlib.Path(tmp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, text in (("gh", FAKE_GH), ("xdg-open", FAKE_OPENER)):
            path = self.bin / name
            path.write_text(text.replace("PYTHON", sys.executable, 1))
            path.chmod(0o755)
        # The script needs jq, and python3 for the listener: link just those, so nothing else on the PATH (a real gh,
        # or a real opener) is reached.
        for tool in ("jq", "python3"):
            found = subprocess.run(["sh", "-c", f"command -v {tool}"], capture_output=True, text=True, check=True)
            (self.bin / tool).symlink_to(found.stdout.strip())
        self.state_file = self.tmp / "state.json"
        self.gh_log = self.tmp / "gh.log"
        self.opener_log = self.tmp / "opener.log"
        self.form_file = self.tmp / "form.txt"
        self.downloads = self.tmp / "Downloads"
        self.downloads.mkdir()

    def given(self, state):
        self.state_file.write_text(json.dumps(state))

    def run_script(self, *args, answers=(), **env):
        full = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.tmp),
            "FAKE_GH_STATE": str(self.state_file),
            "FAKE_GH_LOG": str(self.gh_log),
            "FAKE_OPENER_LOG": str(self.opener_log),
            "FAKE_BROWSER_FORM": str(self.form_file),
            "FAKE_BROWSER_CODE": CODE,
            **env,
        }
        stdin = "".join(f"{answer}\n" for answer in answers)
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            input=stdin, env=full, capture_output=True, text=True, timeout=60, check=False,
        )

    def final_state(self):
        return json.loads(self.state_file.read_text())

    def gh_calls(self):
        if not self.gh_log.exists():
            return []
        return [json.loads(line) for line in self.gh_log.read_text().splitlines()]

    def writes(self):
        return [call for call in self.gh_calls() if call["argv"][1:2] == ["set"]]

    def opened(self):
        return self.opener_log.read_text().splitlines() if self.opener_log.exists() else []

    def assert_secrets_never_shown(self, result):
        everything = result.stdout + result.stderr
        for secret in ("MIIunittestkey", ROUTINE_TOKEN, "unused-client-secret"):
            self.assertNotIn(secret, everything)
        for call in self.gh_calls():
            for arg in call["argv"]:
                self.assertNotIn("MIIunittestkey", arg)
                self.assertNotIn(ROUTINE_TOKEN, arg)


class GivenBadArguments(SetupTest):
    def test_it_rejects_an_unknown_option(self):
        self.given(base_state())
        result = self.run_script("--frobnicate")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Usage:", result.stderr)
        self.assertEqual(self.gh_calls(), [])

    def test_it_rejects_an_organisation_name_that_is_not_one(self):
        self.given(base_state())
        result = self.run_script("--org", "cipher/stash", "--check")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.gh_calls(), [])

    def test_help_prints_the_usage(self):
        result = self.run_script("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stdout)


class GivenGhCannotManageTheOrganisation(SetupTest):
    def test_it_says_how_to_sign_in_and_stops(self):
        self.given(base_state(auth_ok=False))
        result = self.run_script("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("gh auth refresh -h github.com -s admin:org", result.stderr)
        self.assertEqual(len(self.gh_calls()), 1)


class Check(SetupTest):
    def test_a_complete_organisation_passes(self):
        self.given(complete_state())
        result = self.run_script("--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"CLAUDE_REVIEW_APP_CLIENT_ID is {CLIENT_ID}", result.stdout)
        self.assertIn("cipherstash-review-membership is installed on cipherstash with Members: read", result.stdout)
        self.assertIn(f"CLAUDE_ROUTINE_TRIGGER_ID is {TRIGGER_ID}", result.stdout)
        self.assertIn("Everything is set up.", result.stdout)
        self.assertEqual(self.writes(), [])
        self.assert_secrets_never_shown(result)

    def test_an_empty_organisation_reports_each_missing_setting(self):
        self.given(base_state())
        result = self.run_script("--check")
        self.assertEqual(result.returncode, 1)
        for name in ("CLAUDE_REVIEW_APP_CLIENT_ID", "CLAUDE_REVIEW_APP_PRIVATE_KEY", "CLAUDE_ROUTINE_TOKEN",
                     "CLAUDE_ROUTINE_TRIGGER_ID"):
            self.assertIn(f"missing  {name}", result.stdout)
        self.assertEqual(self.writes(), [])

    def test_an_app_that_is_not_installed_fails(self):
        # The failure seen live on 2026-11-20: the settings were there, but their app was not installed.
        self.given(complete_state(installations=[installed(slug="something-else", client_id="Iv23liOther")]))
        result = self.run_script("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"No app with client id {CLIENT_ID} is installed on cipherstash", result.stdout)

    def test_an_app_without_members_read_fails(self):
        self.given(complete_state(installations=[installed(members=None)]))
        result = self.run_script("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not have Members: read", result.stdout)

    def test_a_suspended_app_fails(self):
        self.given(complete_state(installations=[{**installed(), "suspended_at": "2026-11-01T00:00:00Z"}]))
        result = self.run_script("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("is suspended", result.stdout)

    def test_an_installation_without_a_client_id_is_matched_through_its_app(self):
        entry = installed()
        del entry["client_id"]
        self.given(complete_state(installations=[entry],
                                  apps={"cipherstash-review-membership": {"client_id": CLIENT_ID}}))
        result = self.run_script("--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_it_says_so_when_it_cannot_list_installed_apps(self):
        self.given(complete_state(installations_fail=True))
        result = self.run_script("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Could not list the apps installed on cipherstash", result.stdout)

    def test_a_trigger_id_that_does_not_start_trig_fails(self):
        self.given(complete_state(variables={"CLAUDE_REVIEW_APP_CLIENT_ID": CLIENT_ID,
                                             "CLAUDE_ROUTINE_TRIGGER_ID": "nope"}))
        result = self.run_script("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not start with trig_", result.stdout)

    def test_it_points_out_the_old_app_id_secret(self):
        self.given(complete_state(secrets={"CLAUDE_REVIEW_APP_PRIVATE_KEY": PEM, "CLAUDE_ROUTINE_TOKEN": "t",
                                           "CLAUDE_REVIEW_APP_ID": "1"}))
        result = self.run_script("--check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("gh secret delete CLAUDE_REVIEW_APP_ID --org cipherstash", result.stdout)

    def test_it_checks_another_organisation(self):
        self.given({**complete_state(), "org": "acme"})
        result = self.run_script("--org", "acme", "--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class SetUpAnEmptyOrganisation(SetupTest):
    # New app, default name, Enter once it is installed, the routine token, the trigger id.
    ANSWERS = ("", "", "", ROUTINE_TOKEN, TRIGGER_ID)

    def test_it_creates_the_app_and_stores_everything(self):
        self.given(base_state())
        result = self.run_script(answers=self.ANSWERS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        state = self.final_state()
        self.assertEqual(state["variables"]["CLAUDE_REVIEW_APP_CLIENT_ID"], CLIENT_ID)
        self.assertEqual(state["secrets"]["CLAUDE_REVIEW_APP_PRIVATE_KEY"].strip(), PEM.strip())
        self.assertEqual(state["secrets"]["CLAUDE_ROUTINE_TOKEN"], ROUTINE_TOKEN)
        self.assertEqual(state["variables"]["CLAUDE_ROUTINE_TRIGGER_ID"], TRIGGER_ID)
        self.assertIn("Everything is set up.", result.stdout)
        self.assert_secrets_never_shown(result)

    def test_the_manifest_asks_for_members_read_only_on_a_private_app_with_no_webhook(self):
        self.given(base_state())
        self.run_script(answers=self.ANSWERS)
        action, manifest_text = self.form_file.read_text().split("\n", 1)
        self.assertTrue(action.startswith("https://github.com/organizations/cipherstash/settings/apps/new?state="))
        manifest = json.loads(manifest_text)
        self.assertEqual(manifest["name"], "cipherstash-review-membership")
        self.assertEqual(manifest["default_permissions"], {"members": "read"})
        self.assertEqual(manifest["default_events"], [])
        self.assertIs(manifest["public"], False)
        self.assertIs(manifest["hook_attributes"]["active"], False)
        self.assertRegex(manifest["redirect_url"], r"^http://127\.0\.0\.1:\d+/callback$")

    def test_it_opens_the_install_page(self):
        self.given(base_state())
        self.run_script(answers=self.ANSWERS)
        self.assertIn("https://github.com/apps/cipherstash-review-membership/installations/new", self.opened())

    def test_it_uses_the_name_typed(self):
        self.given(base_state())
        self.run_script(answers=("", "acme-membership", "", ROUTINE_TOKEN, TRIGGER_ID))
        manifest = json.loads(self.form_file.read_text().split("\n", 1)[1])
        self.assertEqual(manifest["name"], "acme-membership")

    def test_a_name_longer_than_34_characters_is_asked_again(self):
        self.given(base_state())
        result = self.run_script(answers=("", "x" * 35, "", "", ROUTINE_TOKEN, TRIGGER_ID))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("at most 34 characters", result.stdout)

    def test_it_asks_again_for_a_trigger_id_that_does_not_start_trig(self):
        self.given(base_state())
        result = self.run_script(answers=(*self.ANSWERS[:-1], "nope", TRIGGER_ID))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("starts with trig_", result.stdout)
        self.assertEqual(self.final_state()["variables"]["CLAUDE_ROUTINE_TRIGGER_ID"], TRIGGER_ID)

    def test_without_python_it_takes_the_pasted_address(self):
        self.given(base_state())
        pasted = f"http://127.0.0.1:1/callback?code={CODE}&state=whatever"
        result = self.run_script(answers=("", "", pasted, "", ROUTINE_TOKEN, TRIGGER_ID),
                                 SETUP_ORGANISATION_PYTHON="no-such-python")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.final_state()["variables"]["CLAUDE_REVIEW_APP_CLIENT_ID"], CLIENT_ID)
        (page,) = [url for url in self.opened() if url.startswith("file://")]
        # The page held only the manifest, and is removed once the script is done with it.
        self.assertFalse(pathlib.Path(page[len("file://"):]).exists())

    def test_a_pasted_address_without_a_code_is_asked_again(self):
        self.given(base_state())
        pasted = f"http://127.0.0.1:1/callback?code={CODE}&state=s"
        result = self.run_script(answers=("", "", "http://127.0.0.1:1/oops", pasted, "", ROUTINE_TOKEN, TRIGGER_ID),
                                 SETUP_ORGANISATION_PYTHON="no-such-python")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("No code in that address", result.stdout)

    def test_it_keeps_asking_until_the_app_is_installed(self):
        self.given(base_state(install_on_conversion=False))
        result = self.run_script(answers=("", "", "", "skip", ROUTINE_TOKEN, TRIGGER_ID))
        self.assertEqual(result.returncode, 1)
        self.assertIn("is not installed on cipherstash yet", result.stdout)
        self.assertIn("No app with client id", result.stdout)


class SetUpAgain(SetupTest):
    def test_it_changes_nothing_when_every_answer_is_the_default(self):
        self.given(complete_state())
        result = self.run_script(answers=("", "", ""))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.writes(), [])
        self.assertIn("is set up and installed", result.stdout)
        self.assertIn("CLAUDE_ROUTINE_TOKEN is already set", result.stdout)
        self.assertIn(f"CLAUDE_ROUTINE_TRIGGER_ID is already {TRIGGER_ID}", result.stdout)

    def test_it_replaces_only_what_is_asked(self):
        self.given(complete_state())
        result = self.run_script(answers=("n", "y", "new-token", "n"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([call["argv"][2] for call in self.writes()], ["CLAUDE_ROUTINE_TOKEN"])
        self.assertEqual(self.final_state()["secrets"]["CLAUDE_ROUTINE_TOKEN"], "new-token")
        self.assertNotIn("new-token", result.stdout + result.stderr)


class UseAnExistingApp(SetupTest):
    def setUp(self):
        super().setUp()
        self.pem = self.downloads / "old-app.2026-11-20.private-key.pem"
        self.pem.write_text(PEM)

    def test_it_installs_the_named_app_and_stores_a_new_key(self):
        # The organisation as found live: only the old app id secret, for an app that is not installed.
        self.given(base_state(
            secrets={"CLAUDE_REVIEW_APP_ID": "1", "CLAUDE_REVIEW_APP_PRIVATE_KEY": "old"},
            apps={"old-app": {"client_id": "Iv23liOldApp", "permissions": {"members": "read"}}},
            installations=[installed(slug="old-app", client_id="Iv23liOldApp")],
        ))
        result = self.run_script(answers=("old-app", "", "", ROUTINE_TOKEN, TRIGGER_ID))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        state = self.final_state()
        self.assertEqual(state["variables"]["CLAUDE_REVIEW_APP_CLIENT_ID"], "Iv23liOldApp")
        self.assertEqual(state["secrets"]["CLAUDE_REVIEW_APP_PRIVATE_KEY"], PEM)
        self.assertIn("https://github.com/organizations/cipherstash/settings/apps/old-app", self.opened())
        self.assertFalse(self.pem.exists())
        self.assertIn("delete the old key", result.stdout)
        self.assert_secrets_never_shown(result)

    def test_it_asks_for_the_client_id_when_github_will_not_say(self):
        self.given(base_state(installations=[installed(slug="old-app", client_id="Iv23liOldApp")]))
        result = self.run_script(answers=("old-app", "Iv23liOldApp", "", "", ROUTINE_TOKEN, TRIGGER_ID))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.final_state()["variables"]["CLAUDE_REVIEW_APP_CLIENT_ID"], "Iv23liOldApp")

    def test_it_opens_the_install_page_when_the_app_is_not_installed(self):
        self.given(base_state(apps={"old-app": {"client_id": "Iv23liOldApp", "permissions": {"members": "read"}}}))
        result = self.run_script(answers=("old-app", "skip", "", "", ROUTINE_TOKEN, TRIGGER_ID))
        self.assertIn("https://github.com/apps/old-app/installations/new", self.opened())
        self.assertEqual(result.returncode, 1)

    def test_it_keeps_the_key_file_when_asked(self):
        self.given(base_state(apps={"old-app": {"client_id": "Iv23liOldApp", "permissions": {"members": "read"}}},
                              installations=[installed(slug="old-app", client_id="Iv23liOldApp")]))
        self.run_script(answers=("old-app", "", "n", ROUTINE_TOKEN, TRIGGER_ID))
        self.assertTrue(self.pem.exists())

    def test_a_file_that_is_not_a_private_key_is_asked_again(self):
        other = self.tmp / "notes.txt"
        other.write_text("hello\n")
        self.given(base_state(apps={"old-app": {"client_id": "Iv23liOldApp", "permissions": {"members": "read"}}},
                              installations=[installed(slug="old-app", client_id="Iv23liOldApp")]))
        result = self.run_script(answers=("old-app", str(other), "", "", ROUTINE_TOKEN, TRIGGER_ID))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("is not a private key", result.stdout)
        self.assertEqual(self.final_state()["secrets"]["CLAUDE_REVIEW_APP_PRIVATE_KEY"], PEM)
        self.assertTrue(other.exists())


class RotateKey(SetupTest):
    def test_it_stores_the_downloaded_key_and_deletes_the_file(self):
        pem = self.downloads / "cipherstash-review-membership.2026-11-20.private-key.pem"
        pem.write_text(PEM.replace("MIIunittest", "MIIunittestNEW"))
        self.given(complete_state(secrets={"CLAUDE_REVIEW_APP_PRIVATE_KEY": "old", "CLAUDE_ROUTINE_TOKEN": "t"}))
        result = self.run_script("--rotate-key", answers=("", ""))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("MIIunittestNEW", self.final_state()["secrets"]["CLAUDE_REVIEW_APP_PRIVATE_KEY"])
        self.assertIn("https://github.com/organizations/cipherstash/settings/apps/cipherstash-review-membership",
                      self.opened())
        self.assertFalse(pem.exists())
        self.assertEqual([call["argv"][2] for call in self.writes()], ["CLAUDE_REVIEW_APP_PRIVATE_KEY"])
        self.assert_secrets_never_shown(result)

    def test_it_asks_for_the_app_when_the_client_id_is_not_installed(self):
        pem = self.downloads / "other.private-key.pem"
        pem.write_text(PEM)
        self.given(complete_state(installations=[]))
        result = self.run_script("--rotate-key", answers=("other", "", ""))
        # --rotate-key only replaces the key, so it succeeds even though the app is not installed.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("https://github.com/organizations/cipherstash/settings/apps/other", self.opened())


if __name__ == "__main__":
    unittest.main()
