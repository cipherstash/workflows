"""The decide job's membership lookup: is the requester an active member of the team?

Writes the step output membership: "member", "not-member" or "lookup-failed". Membership is never assumed: anything but
a clear answer from GitHub is "lookup-failed", which skips, with an error annotation saying why.

Inputs, all environment variables: APP_TOKEN (the membership App's token), TOKEN_OUTCOME (the outcome of the step that
created it), ORG, TEAM, REQUESTER and the runner's GITHUB_API_URL and GITHUB_OUTPUT.
Python's standard library only (3.10 features); tests/test_lookup.py runs this file.
"""

import http.client
import json
import os
import re
import urllib.error
import urllib.request

from helpers import TIMEOUT_SECONDS, TotalTimeout, error, opener, output, total_time_limit

# The most of an answer's body that is read. A membership is far smaller; a larger answer is not one.
MAX_BODY_BYTES = 64 * 1024

# Names go into the URL, so each must start with a letter, digit or (for a team) underscore, which keeps "."
# and ".." out. A login is letters and digits joined by single hyphens (or underscores, for managed users).
ORG_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")
TEAM_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
LOGIN = re.compile(r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*")


class LookupFailed(Exception):
    """Membership could not be checked; the message says why."""


def api(path, token):
    """GETs path from the GitHub API and returns (status, body), reading at most MAX_BODY_BYTES + 1 bytes of an
    answer. The token is only ever a request header."""
    request = urllib.request.Request(
        f"{os.environ['GITHUB_API_URL']}/{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    result = None
    try:
        with total_time_limit():
            try:
                with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                    # Read with the time limit running: a 200's body is the membership, so one cut off is a
                    # lookup-failed.
                    result = (response.status, response.read(MAX_BODY_BYTES + 1))
            except urllib.error.HTTPError as answer:
                # The body of a status other than 200 is never used.
                answer.close()
                result = (answer.code, b"")
    except (OSError, http.client.HTTPException, ValueError, TotalTimeout) as failure:
        if result is not None:  # The alarm came after the answer was complete.
            return result
        # URLError, timeouts, refused or dropped connections, a cut-off answer. Only the failure's kind is
        # reported.
        reason = getattr(failure, "reason", failure)
        raise LookupFailed(f"Could not reach GitHub to look up team membership ({type(reason).__name__})")
    return result


def look_up(token, org, team, requester):
    if os.environ.get("TOKEN_OUTCOME") != "success" or not token:
        raise LookupFailed(
            "Could not create the membership App's token: check the CLAUDE_REVIEW_APP_ID and "
            f"CLAUDE_REVIEW_APP_PRIVATE_KEY secrets, and that the App is installed on {org} with Members: read"
        )
    if not (ORG_NAME.fullmatch(org) and TEAM_NAME.fullmatch(team) and LOGIN.fullmatch(requester)):
        raise LookupFailed(
            "Cannot look up team membership: the organisation, team or requester is not a valid GitHub name"
        )
    status, body = api(f"orgs/{org}/teams/{team}/memberships/{requester}", token)
    if status == 200:
        try:
            membership = json.loads(body) if len(body) <= MAX_BODY_BYTES else None
        except ValueError:
            membership = None
        if not isinstance(membership, dict):
            raise LookupFailed(
                f"Cannot look up team membership in {org}/{team}: GitHub's answer was not a membership"
            )
        # A pending invitation is not membership.
        return "member" if membership.get("state") == "active" else "not-member"
    if status == 404:
        # GitHub also answers 404 for a team that does not exist, which would otherwise skip everyone quietly.
        team_status, _ = api(f"orgs/{org}/teams/{team}", token)
        if team_status == 200:
            return "not-member"
        raise LookupFailed(
            f"Cannot look up team membership: team {org}/{team} was not found (HTTP {team_status}); "
            "check the team input and the App's installation"
        )
    raise LookupFailed(f"Cannot look up team membership in {org}/{team}: GitHub answered HTTP {status}")


membership = "lookup-failed"
try:
    membership = look_up(
        os.environ.get("APP_TOKEN", ""),
        os.environ.get("ORG", ""),
        os.environ.get("TEAM", ""),
        os.environ.get("REQUESTER", ""),
    )
except LookupFailed as failure:
    error(str(failure))
except Exception as failure:  # Anything unexpected still ends as a reported lookup-failed, never a member.
    error(f"Cannot look up team membership: the lookup broke ({type(failure).__name__})")
output("membership", membership)

