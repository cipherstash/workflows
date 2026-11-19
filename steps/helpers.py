"""Helpers shared by the reusable workflow's Python steps.

Each step file in this directory imports what it needs from here, so a hardening fix is made once and reaches every
step. Python's standard library only (3.10 features), so nothing is installed when a step runs.

A step reads its inputs only from environment variables that the workflow sets, never from a workflow expression placed
in the code.
"""

import contextlib
import http.client
import os
import signal
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from types import FrameType
from typing import Any, NoReturn

# Seconds to wait to connect, and then for each read of the answer.
TIMEOUT_SECONDS = 10
# Seconds one request may take in all, however slowly the answer arrives.
TOTAL_SECONDS = 30


class TotalTimeout(Exception):
    """One request took longer than TOTAL_SECONDS."""


class Unreachable(Exception):
    """A request got no status: kind names the failure's type, which is all a step reports of it."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def escaped(message: str) -> str:
    """message escaped as GitHub's workflow commands require, so no value can end an annotation or start another."""
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def error(message: str) -> None:
    print(f"::error::{escaped(message)}", flush=True)


def warning(message: str) -> None:
    print(f"::warning::{escaped(message)}", flush=True)


def output(name: str, value: str) -> None:
    # A random delimiter makes any value safe, even one with a newline in it.
    delimiter = f"ghadelim_{uuid.uuid4()}"
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as file:
        file.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")


def redacted(text: str, token: str) -> str:
    """text with every copy of token taken out, in case an answer repeats the token it was sent."""
    return text.replace(token, "[token]") if token else text


class NoRedirects(urllib.request.HTTPRedirectHandler):
    # A redirect is an answer to report, not to follow: following it could send the token somewhere else.
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


# urllib.request reads the proxy settings of the environment (http_proxy, https_proxy and no_proxy, in either
# case) by default, but not ALL_PROXY.
opener = urllib.request.build_opener(NoRedirects)


def on_total_timeout(signum: int, frame: FrameType | None) -> NoReturn:
    raise TotalTimeout()


@contextlib.contextmanager
def total_time_limit() -> Iterator[None]:
    """Raises TotalTimeout inside the block once TOTAL_SECONDS have passed. The block can end the limit early with
    signal.setitimer(signal.ITIMER_REAL, 0). Leaving the block ends the limit and puts the previous SIGALRM handler
    back, even if the alarm comes while that happens."""
    previous = signal.signal(signal.SIGALRM, on_total_timeout)
    signal.setitimer(signal.ITIMER_REAL, TOTAL_SECONDS)
    try:
        yield
    finally:
        try:
            signal.setitimer(signal.ITIMER_REAL, 0)
        finally:
            signal.signal(signal.SIGALRM, previous)


def github_headers(token: str) -> dict[str, str]:
    """The headers of a GitHub API request: the token, only ever as a request header, and the API version."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def send(
    request: urllib.request.Request, read_ok_bytes: int | None = None, read_error_bytes: int = 0
) -> tuple[int, bytes]:
    """Sends request once, with no retry and no redirect followed, and returns (status, body) within TOTAL_SECONDS.

    With read_ok_bytes, the body of a 2xx answer is the answer: at most that many bytes of it are read with the time
    limit running, and the answer counts only once it is read, or, for an error answer (whose body is never read),
    once it is closed. Without it, the status is the answer: it counts as soon as it arrives and the body is b"".
    The time limit then ends, unless read_error_bytes asks for the start of an error answer's body, which is read with
    the time limit running; if that read fails, the status stands with an empty body.

    Raises Unreachable if no answer counted: URLError, timeouts, refused or dropped connections, an answer cut off
    before it counted.
    """
    result = None
    try:
        with total_time_limit():
            try:
                with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                    if read_ok_bytes is None:
                        # Nothing after this can make an answer that arrived look like a failure.
                        result = (response.status, b"")
                        signal.setitimer(signal.ITIMER_REAL, 0)
                    else:
                        result = (response.status, response.read(read_ok_bytes))
            except urllib.error.HTTPError as answer:
                # The status is the answer; a body read below only adds to it.
                result = (answer.code, b"")
                try:
                    if read_ok_bytes is None and read_error_bytes:
                        result = (answer.code, answer.read(read_error_bytes))
                    elif read_ok_bytes is None:
                        signal.setitimer(signal.ITIMER_REAL, 0)
                finally:
                    answer.close()
    except (OSError, http.client.HTTPException, ValueError, TotalTimeout) as failure:
        if result is None:
            # Only the failure's kind is reported.
            reason = getattr(failure, "reason", failure)
            raise Unreachable(type(reason).__name__) from None
        # The answer counted; only what came after it failed or ran out of time, so it stands.
    return result
