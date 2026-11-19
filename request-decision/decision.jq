# The request decision: reads one review request's event and decides whether to send it on.
#
# The reusable workflow's decide job checks out this repository at the commit the caller pinned and runs this file,
# and request-decision/test runs it against the saved cases, so the tests run exactly what ships.
#
# Input: {event_name, event, review_agent, membership}, where membership is null before a lookup, or "member",
# "not-member" or "lookup-failed" after one. Output, exactly one of:
#   {"decision": "skip", "reason": "<code>"}
#   {"decision": "lookup", "requester": "<login>"}
#   {"decision": "fire", "kind": "ready", "requester": "<login>", "pull_request": {"number": N, "url": "…"}, "text": "…"}

def skip($reason): {decision: "skip", reason: $reason};

# Fires for a member, or asks for a lookup when membership is not known yet.
def gate($requester; $fire):
  if .membership == null then {decision: "lookup", requester: $requester}
  elif .membership == "member" then $fire
  elif .membership == "not-member" then skip("not-a-team-member")
  # "lookup-failed", or anything unexpected: membership is never assumed.
  else skip("membership-lookup-failed")
  end;

# GitHub logins are not case-sensitive.
def is_review_agent($login): ($login // "" | ascii_downcase) as $l | $l != "" and $l == (.review_agent // "" | ascii_downcase);

# Ready: a non-draft pull request opened or reopened, or a draft marked ready for review. The requester is the
# author. A pull request the review agent opened is reviewed without a lookup: the agent opens most pull
# requests, and they need review too.
def ready:
  .event.pull_request as $pr
  | .event.repository.full_name as $event_repo
  | (
      {
        decision: "fire",
        kind: "ready",
        requester: $pr.user.login,
        pull_request: {number: $pr.number, url: $pr.html_url}
      }
      # The text names the kind, from the decision's own kind field: the first line says which pull request to
      # review, the second says why the review was requested.
      | .text = "Review pull request #\($pr.number) in \($event_repo): \($pr.html_url)\n\nKind of review request: \(.kind)."
    ) as $fire
  # A payload with no author is not one this decision can act on: there is no one to look up or credit.
  | if $pr.user == null then skip("not-a-review-request")
    elif $pr.draft == true then skip("draft")
    elif $pr.user.type == "Bot" then skip("bot")
    elif $pr.head.repo == null or $pr.head.repo.full_name != $pr.base.repo.full_name then skip("fork")
    elif is_review_agent($pr.user.login) then $fire
    else gate($pr.user.login; $fire)
    end;

if .event_name == "pull_request" and (.event.action | IN("opened", "reopened", "ready_for_review"))
then ready
else skip("not-a-review-request")
end
