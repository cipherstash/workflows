# CipherStash shared workflows

Reusable GitHub Actions workflows shared by CipherStash repositories.

The first one asks Claude to review a pull request.
It is being built.
The plan is in [issue #1](https://github.com/cipherstash/workflows/issues/1).

The words this repository uses, such as *review request* and *team member*, are defined in [CONTEXT.md](CONTEXT.md).

## Claude review

[`.github/workflows/claude-review.yml`](.github/workflows/claude-review.yml) is a reusable workflow.
It decides whether an event is a review request from a team member and, if it is, fires the Claude code-review routine trigger for that pull request.

So far it handles one kind of review request, **Ready**: a non-draft pull request opened or reopened, or a draft marked ready for review.
It skips drafts, pull requests from bots, and pull requests from forks, including deleted forks.
It reviews a pull request only if its author is a team member.
A pull request opened by the review agent is reviewed without a membership check, because the agent opens most pull requests.

A failed request never turns a pull request's checks red.
It shows as an error in the workflow run instead.

### Adding it to a repository

Copy [`examples/claude-review.yml`](examples/claude-review.yml) to `.github/workflows/claude-review.yml` in the repository.
It pins the reusable workflow to a full commit SHA, with the version in a comment.

The caller needs these settings, which CipherStash sets once for the whole organisation:

| Name | Kind | What it is |
| --- | --- | --- |
| `CLAUDE_ROUTINE_TOKEN` | secret | The code-review routine's bearer token. |
| `CLAUDE_REVIEW_APP_ID` | secret | The id of the membership GitHub App. |
| `CLAUDE_REVIEW_APP_PRIVATE_KEY` | secret | The membership GitHub App's private key. |
| `CLAUDE_ROUTINE_TRIGGER_ID` | variable | The routine's trigger id, which starts `trig_`. |

The membership GitHub App is installed on the organisation with only the "Members: read" permission.
The workflow uses it to get a short-lived token and look up whether the pull request's author is in the team.

### Inputs and secrets

| Input | Required | Default | What it is |
| --- | --- | --- | --- |
| `routine-trigger-id` | yes | | The routine's trigger id (`trig_…`). |
| `team` | no | `developers` | The slug of the team, in the calling repository's organisation, whose members may request a review. |
| `review-agent` | no | `cipherstash-agent` | The login of the review agent. |

All three secrets, `CLAUDE_ROUTINE_TOKEN`, `CLAUDE_REVIEW_APP_ID` and `CLAUDE_REVIEW_APP_PRIVATE_KEY`, are required and must be passed explicitly, as the example does.

### What a run shows

A run has two jobs:

- `decide` reads the event and decides. It writes a one-line summary, such as `Claude review: skipped (draft)`, to the run's summary page. A line break in a reason is flattened to a space, so a reason cannot start a workflow command. It does not fail: every step that can fail is allowed to, and the decision then becomes a skip with the reason `decision-failed`.
- `fire` sends the request to the routine trigger. It runs only when `decide` decided to fire. It sends the request once, with no retry, and does not follow a redirect. Only a `2xx` answer counts as sent. A failed request shows as a failed step with an error giving the HTTP status, but the job and the check stay green.
  If the job could not check out its step file, nothing is sent, and the run shows an error, but the check stays green.

`fire` holds no permissions.
Only one `fire` runs per pull request at a time: a second waits rather than cancelling the first.

## The request decision

The request decision is the part that reads a GitHub event and decides whether it is a review request to send on.
It is one `jq` program, in `request-decision/decision.jq`.

A reusable workflow runs in the calling repository, so the `decide` job's first step checks out this repository to get the program and the Python step files.
It checks out only that file and the `steps/` directory, at the commit the caller pinned (`job.workflow_sha`), into `.cipherstash-workflows` in the job's workspace, and keeps no credentials.
If the checkout fails, the decision is a skip with the reason `decision-failed`, and the run shows an error.

`request-decision/test` runs the same file against the saved cases, so the tests run exactly what ships.

### Input

One JSON object:

| Field | What it is |
| --- | --- |
| `event_name` | The GitHub event name, such as `pull_request`. |
| `event` | The event payload GitHub delivers. |
| `review_agent` | The review agent's login. |
| `membership` | `null` before the membership lookup; after it, `"member"`, `"not-member"` or `"lookup-failed"`. |

### Output

Exactly one of:

- `{"decision": "skip", "reason": "<reason>"}`: no review.
- `{"decision": "lookup", "requester": "<login>"}`: look up whether the requester is a team member, then run the decision again with `membership` set. Only returned when `membership` is `null`.
- `{"decision": "fire", "kind": "ready", "requester": "<login>", "pull_request": {"number": <n>, "url": "<url>"}, "text": "<request text>"}`: fire the routine trigger with `{"text": <text>}`.
  The text is two paragraphs: `Review pull request #<n> in <owner/repo>: <url>`, then `Kind of review request: <kind>.`, which names the kind of review request, such as `ready`.

### Reasons for skipping

| Reason | When |
| --- | --- |
| `not-a-review-request` | The event is not a kind of review request, such as a push to a pull request (`synchronize`) or an issue event. |
| `draft` | The pull request is a draft. |
| `bot` | The pull request's author is a bot. No membership lookup is made. |
| `fork` | The pull request comes from a fork, or from a fork that was deleted. |
| `not-a-team-member` | The requester is not an active member of the team. |
| `membership-lookup-failed` | Team membership could not be checked: GitHub could not be reached within 30 seconds, answered with an error status or a redirect (the lookup does not follow redirects), or gave an answer that is malformed or larger than 64 KiB. The run shows an error. |
| `decision-failed` | The decision itself could not be checked out or run, or did not finish. The workflow adds this reason itself, not the `jq` program. The run shows an error. |

An event that has no pull request author, such as a payload whose `user` is `null`, is skipped as `not-a-review-request`.

## Python steps

Some steps, such as the membership lookup and the request to the routine trigger, are written in Python.
They use only Python's standard library and keep to Python 3.10, so nothing is installed when they run.
They read their inputs only from environment variables that the workflow sets.

The code for these steps is in the `steps/` directory:

| File | What it is |
| --- | --- |
| `steps/lookup.py` | The membership lookup, in the `decide` job. |
| `steps/report.py` | The last step of the `decide` job. It passes on the decision, or the `decision-failed` skip, and writes the summary line. |
| `steps/fire.py` | The request to the routine trigger, in the `fire` job. |
| `steps/helpers.py` | Code every step shares: the time limits, the redirect blocker, annotations, step outputs and hiding a token. |

The `decide` job checks out `steps/` with the request decision, as described above.
The `fire` job checks out `steps/` in the same way, because a job cannot see another job's files.
Each step names its file in its `env` as `STEP_FILE`, and runs it with the runner's `python3`.
If the report's file was not checked out, the step still writes the `decision-failed` skip, an error and the summary line by itself.
If the routine call's file was not checked out, the step sends nothing and shows an error.

The routine API's address is fixed in `steps/fire.py`, so no input or event can change where the token goes.
Only the unit tests replace it, with a stand-in server's address.

The workflows repository must stay public. A caller's built-in token reads the request decision and the step files from it, so if it became private, every run would end as decision-failed with an error.
The unit tests in `tests/` run the same step files, so they test exactly what ships.

## Checks

Every pull request, and every push to `main`, runs these checks:

- `actionlint` over the workflows in this repository and the example callers in `examples/`.
- `python-checks`, which runs Ruff and mypy over every Python file in this repository. See [Linting and type checking](#linting-and-type-checking).
- `tests`, once for each of Python 3.10, 3.12 and 3.14, which runs:
  - the request decision's saved cases, run by `request-decision/test`.
    It fails if a case is missing a file, if a file is not valid JSON, or if there are no cases at all.
  - a check of `request-decision/test` itself, run by `request-decision/test-runner`.
  - the unit tests of the Python steps, in `tests/`.
- `zizmor`, a security linter for GitHub Actions, over the workflows, the example callers in `examples/`, and `.github/dependabot.yml`.
- `actions-lock`, which checks the workflow lockfile.

The request decision's rules are tested against saved event payloads. `request-decision/test` runs `request-decision/decision.jq` on each one, so the tests need no network and run anywhere with `bash` and `jq`:

```sh
request-decision/test
```

### Adding a test case

1. Make a new directory under `request-decision/cases/`, named for what it checks, such as `ready-draft-skips`.
2. Add `input.json`: the decision's input, with `event_name`, `event` (the event payload, trimmed to the fields that matter), `review_agent` (usually `"cipherstash-agent"`) and `membership` (`null` to test the decision before a lookup).
3. Add `expected.json`: the decision expected for that input, such as `{"decision": "skip", "reason": "draft"}`.
4. Run `request-decision/test` and check the new case passes. A case that fails prints the difference between the expected and actual decision.

### Running the unit tests

The unit tests need `python3` (3.10 or later), `bash` and `yq`, and no network: a test that needs GitHub's API or the routine trigger starts a stand-in server on 127.0.0.1.

```sh
python3 -m unittest discover --start-directory tests
```

The `tests` check runs them on Python 3.10, 3.12 and 3.14.
3.10 is the oldest Python the steps support.
3.14 is there to cover the runner's own `python3`, whatever `ubuntu-latest` provides, which the reusable workflow uses.

### Adding a unit test

`tests/harness.py` runs one step of the reusable workflow on its own:

- `run_step(job, step_id, env, api_url=...)` runs the step's file from `steps/` (or, for an inline step, reads its script out of the workflow with `yq`) with `python3`, and returns what it left: its exit code, standard output and error, its outputs (`.outputs`), its summary (`.summary`) and its `::error::` annotations (`.errors()`). The step sees only the `env` the test gives it, which must be names the step declares in its `env`, plus the runner's own variables, such as `GITHUB_OUTPUT`.
- `StubServer(routes)` stands in for GitHub's API or the routine trigger. `routes` maps a path to a status and body (and, optionally, headers to add, such as a redirect's `Location`), to `StubServer.HANG` for an answer that never comes, to `StubServer.DROP` for a connection closed without one, or to `StubServer.drip(status)` for an answer whose body never ends, for `GET` and `POST` alike. It records every request's method, path and headers in `.requests`, and its body in `.bodies`.
- `substitutions` replaces text in the step's code before it runs, such as a timeout constant lowered so a test does not wait, or the routine API's fixed address swapped for the stand-in server's. For a step file, the code includes `steps/helpers.py`. Each piece of text must appear exactly once in all of it. The files in `steps/` are not changed: the harness runs a changed copy.
- `run_step_as_written(job, step_id, env)` runs a step's `run` script exactly as the workflow writes it, with `bash`. It checks what a step does around its file, such as when the file was not checked out.

1. Add a test method to the step's test file, such as `tests/test_lookup.py`, or a new `tests/test_<step>.py` for a new step. Name it for what the step does, such as `test_a_pending_membership_is_not_a_member`.
2. Run the unit tests and check the new test passes.

Every Python step now runs from a file in `steps/`, so none keeps its own copy of the shared code.
A step written inline in the workflow, with `shell: python3 {0}`, could not import `steps/helpers.py`.
`tests/test_python_steps.py` lists the inline steps, which is none, and checks that any copy of the shared code in one is identical to `steps/helpers.py`.
So a new inline step fails the tests until it is added to that list, and then its copies are checked.

`tests/test_decision_file.py` checks that each file the workflow runs from its checkout exists in this repository and is included in the checkout of its own job, and that the `decide` and `fire` jobs check out this repository in the same way. So a renamed file fails the tests rather than every run.

### Linting and type checking

[Ruff](https://docs.astral.sh/ruff/) finds likely mistakes and unsafe code in the Python files.
[mypy](https://mypy.readthedocs.io/) checks their types.
Both check every Python file in this repository, as written for Python 3.10, and the check fails on any finding.
Their settings are in `pyproject.toml`, which holds nothing else: this repository is not a Python package.

The files in `steps/` must have type hints on every function, so that mypy checks every call between them.
A new step file must be added to the list of step files in `pyproject.toml`.
In `tests/`, mypy checks the code inside each function, with or without type hints.

Fix a finding where you can.
If the code is right as it is, add a comment on the line that names the rule and gives the reason, such as:

```python
except Exception as failure:  # noqa: BLE001 Anything unexpected still ends as a reported lookup-failed.
```

To run them yourself, install the pinned versions into a virtual environment, then run them from the repository's root:

```sh
python3 -m venv .venv
.venv/bin/pip install --only-binary :all: --require-hashes --requirement .github/python-checks/requirements.txt
.venv/bin/ruff check .
.venv/bin/mypy
```

Ruff and mypy are installed only for this repository's own checks.
The reusable workflow installs nothing: it runs its steps with the runner's `python3`.

`.github/python-checks/requirements.txt` pins Ruff, mypy and every package mypy needs, each by version and the hash of every file PyPI publishes for it.
Dependabot (`.github/dependabot.yml`) checks both requirements files every week (`.github/python-checks` and `.github/zizmor`, with the same 7-day cooldown as for actions) and opens a pull request for a new release.
I could not check Dependabot's documentation for whether it rewrites the `--hash` lines of a requirements file, so look at each of its pull requests: if the hashes are not updated, or do not match, update them by hand:

1. Pick a release at least 7 days old.
2. Change its version in the file, and replace its hashes with those listed at `https://pypi.org/pypi/<package>/<version>/json`.
3. For mypy, check the packages it needs, listed in that file under `requires_dist`, and add or update any that changed in the same way.

### Security checks

[zizmor](https://docs.zizmor.sh/) looks for security problems in the workflows, such as untrusted text used in a script, more permissions than a job needs, or a risky trigger.
It runs version 1.32.0, installed from `.github/zizmor/requirements.txt`, which pins the version and the hash of every file.
The check fails on any finding.
Fix the finding, or, if the risk is accepted, add a comment on the line zizmor reports that names the audit and gives the reason:

```yaml
  issue_comment: # zizmor: ignore[dangerous-triggers] only a team member's command is acted on
```

zizmor's findings show as annotations on the pull request's changed files.

The workflow lockfile, `.github/workflows/actions.lock`, records the exact commit of every action the workflows use.
It is made by the `gh actions-lock` command and is never edited by hand.
The lockfile is not committed yet, so `actions-lock` only warns for now.
Once it is committed, `actions-lock` fails if the lockfile does not match the workflows.
Either way, its annotation shows the lockfile `gh actions-lock` made from the pull request's workflows.
Once the base branch has the lockfile, a pull request that deletes it fails the check.
The check is `.github/scripts/check-lock`, tested by `tests/test_check_lock.py`.

#### Updating gh-actions-lock

The `actions-lock` job installs a pinned release of the `gh-actions-lock` extension.
Nothing updates the pin, because Dependabot does not read it, so update it by hand when a new release comes out:

1. Pick the release at <https://github.com/github/gh-actions-lock/releases>.
2. Set `GH_ACTIONS_LOCK_VERSION` in `.github/workflows/checks.yml` to its tag.
3. Set `GH_ACTIONS_LOCK_SHA256` to the SHA-256 digest that the release page lists for the `linux-amd64` file.
4. Check the digest yourself if you can: download the file and run `sha256sum` on it.

The current hash was taken from the digest the v0.4.1 release lists, and was not checked against another source (trust on first use).
The job fails if the downloaded file does not match the hash.

Dependabot (`.github/dependabot.yml`) checks the actions every week and opens a pull request to update one.
It waits until a new version is 7 days old, so a bad release has time to be found first.
Once the lockfile is committed, Dependabot updates it in the same pull request as the action.
