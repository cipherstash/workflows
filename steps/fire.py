"""The fire job's step: fires the Claude code-review routine trigger with the request decision's text.

Only a 2xx answer counts as sent, and writes the step output sent=true. Anything else is an error annotation and a
failed step, which the step's continue-on-error keeps from turning the check red.

Inputs, all environment variables: CLAUDE_ROUTINE_TOKEN (the routine's bearer token), TRIGGER_ID (the routine's trigger
id), DECISION (the request decision) and the runner's GITHUB_OUTPUT.
Python's standard library only (3.10 features); tests/test_fire.py runs this file.
"""

import json
import os
import re
import sys
import urllib.request

from helpers import Unreachable, error, output, redacted, send

# The routine API's address. It is fixed here: no input or event field can change where the token goes. Only the tests
# replace it, with a stand-in server's.
API_BASE_URL = "https://api.anthropic.com"
# The most of an error answer's body shown in the annotation.
EXCERPT_CHARACTERS = 200

# The id goes into the URL, so anything but a trigger id is refused.
TRIGGER_ID = re.compile(r"trig_[A-Za-z0-9]+")


# Failures that can come after the request went out, so the routine may have accepted it. Every other failure (a refused
# connection, a name that does not resolve, a TLS error) comes before anything is sent. A connect that times out also
# shows as a timeout; it is reported as unknown, which is the safe side.
TIMED_OUT = frozenset({"TimeoutError", "timeout", "TotalTimeout"})
DROPPED = frozenset(
    {
        "RemoteDisconnected",
        "ConnectionResetError",
        "ConnectionAbortedError",
        "BrokenPipeError",
        "BadStatusLine",
        "IncompleteRead",
    }
)
CHECK_FIRST = "the request may have been received, so check for a review before re-running"


class NotSent(Exception):
    """The request was not sent, or the routine trigger did not accept it; the message says why."""


def excerpt(body: bytes, token: str) -> str:
    """The start of an answer's body, on one line, with the token taken out in case the answer repeats it."""
    text = redacted(body.decode("utf-8", errors="replace"), token)
    text = " ".join("".join(c if c.isprintable() else " " for c in text).split())
    if len(text) > EXCERPT_CHARACTERS:
        text = text[:EXCERPT_CHARACTERS] + "…"
    return text


def post(url: str, token: str, body: bytes) -> tuple[int, bytes]:
    """POSTs body to url once and returns (status, the start of an error answer's body, or b"" for a 2xx answer).
    There is no retry: a request that timed out may still have started a session, and a second would review twice.
    A failed request is re-run by hand. The token is only ever a request header."""
    # The URL is API_BASE_URL's fixed https address, so no input can choose its scheme.
    request = urllib.request.Request(  # noqa: S310
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "experimental-cc-routine-2026-04-01",
            "Content-Type": "application/json",
        },
    )
    try:
        # Only a 2xx answer's status is waited for: the routine trigger has accepted the request and a session may have
        # started, so the body is not read and nothing after the status can make an accepted request look not sent. An
        # error answer's body is read for the excerpt in the error annotation.
        return send(request, read_error_bytes=4096)
    except Unreachable as failure:
        if failure.kind in TIMED_OUT:
            raise NotSent(f"The routine trigger did not answer in time ({failure.kind}); {CHECK_FIRST}") from None
        if failure.kind in DROPPED:
            raise NotSent(f"The routine trigger dropped the connection ({failure.kind}); {CHECK_FIRST}") from None
        raise NotSent(f"Could not reach the routine trigger ({failure.kind}); no review was requested") from None


def fire(token: str, trigger_id: str, decision: str) -> None:
    # An empty token stops here, so it never reaches a request or the excerpt.
    if not token:
        raise NotSent("CLAUDE_ROUTINE_TOKEN is empty: pass it to the reusable workflow as a secret")
    if not TRIGGER_ID.fullmatch(trigger_id):
        raise NotSent(
            "routine-trigger-id is not a trigger id (trig_…): pass vars.CLAUDE_ROUTINE_TRIGGER_ID, set under "
            "Settings > Secrets and variables > Actions > Variables"
        )
    try:
        text = json.loads(decision).get("text")
    except (ValueError, AttributeError):
        text = None
    if not isinstance(text, str):
        raise NotSent("The request decision has no request text; no review was requested")
    # json writes the body, so nothing in the pull request's fields can break it.
    body = json.dumps({"text": text}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    status, answer = post(f"{API_BASE_URL}/v1/claude_code/routines/{trigger_id}/fire", token, body)
    if not 200 <= status < 300:
        message = f"The routine trigger answered HTTP {status}; no review was requested"
        shown = excerpt(answer, token)
        raise NotSent(f"{message}: {shown}" if shown else message)
    print(f"Sent the review request to the routine trigger (HTTP {status})", flush=True)


try:
    fire(
        os.environ.get("CLAUDE_ROUTINE_TOKEN", ""),
        os.environ.get("TRIGGER_ID", ""),
        os.environ.get("DECISION", ""),
    )
    # Only a request the routine trigger accepted counts as sent.
    output("sent", "true")
except NotSent as failure:
    error(str(failure))
    sys.exit(1)
except Exception as failure:  # noqa: BLE001 Anything unexpected is reported by its kind only, and is not sent.
    error(f"Could not send the review request: the step broke ({type(failure).__name__})")
    sys.exit(1)
