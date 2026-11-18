# CipherStash shared workflows

Reusable GitHub Actions workflows shared by CipherStash repositories.

The first one asks Claude to review a pull request.
It is being built.
The plan is in [issue #1](https://github.com/cipherstash/workflows/issues/1).

The words this repository uses, such as *review request* and *team member*, are defined in [CONTEXT.md](CONTEXT.md).

## Checks

Every pull request, and every push to `main`, runs these checks:

- `actionlint` over the workflows in this repository.
- The request decision's saved cases, run by `request-decision/test`.
  It fails if a case is missing a file, if a file is not valid JSON, or if there are no cases at all.
- A check of `request-decision/test` itself, run by `request-decision/test-runner`.

The request decision is the part that reads a GitHub event and decides whether it is a review request to send on.
Its rules are tested against saved event payloads, so the tests need no network and run anywhere with `bash` and `jq`:

```sh
request-decision/test
```

### Adding a test case

1. Make a new directory under `request-decision/cases/`, named for what it checks, such as `draft-pull-request-skips`.
2. Add `input.json`: an object with `event_name` (the GitHub event name, such as `pull_request`) and `event` (the event payload, trimmed to the fields that matter).
3. Add `expected.json`: the decision expected for that input, such as `{"decision": "skip", "reason": "draft"}`.
4. Run `request-decision/test` and check the new case passes. A case that fails prints the difference between the expected and actual decision.
