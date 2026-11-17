# The request decision: reads one review request candidate and decides whether to fire the routine trigger.
#
# Input (one JSON object on standard input):
#   event_name  the GitHub event name, for example "pull_request" or "issue_comment"
#   event       the event payload GitHub delivers
#
# Output (one JSON object):
#   {"decision": "skip", "reason": "<why>"}
#
# Only the skip for events that are not a kind of review request exists so far; the kinds of review request are
# added by later tickets (cipherstash/workflows#5, #6 and #7).

{decision: "skip", reason: "not-a-review-request"}
