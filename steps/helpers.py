"""Helpers shared by the reusable workflow's Python steps.

Each step file in this directory imports what it needs from here, so a hardening fix is made once and reaches every
step. Python's standard library only (3.10 features), so nothing is installed when a step runs.

A step reads its inputs only from environment variables that the workflow sets, never from a workflow expression placed
in the code.
"""

import contextlib
import os
import signal
import urllib.request
import uuid

# Seconds to wait to connect, and then for each read of the answer.
TIMEOUT_SECONDS = 10
# Seconds one request may take in all, however slowly the answer arrives.
TOTAL_SECONDS = 30


class TotalTimeout(Exception):
    """One request took longer than TOTAL_SECONDS."""


def escaped(message):
    """message escaped as GitHub's workflow commands require, so no value can end an annotation or start another."""
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def error(message):
    print(f"::error::{escaped(message)}", flush=True)


def warning(message):
    print(f"::warning::{escaped(message)}", flush=True)


def output(name, value):
    # A random delimiter makes any value safe, even one with a newline in it.
    delimiter = f"ghadelim_{uuid.uuid4()}"
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as file:
        file.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")


def redacted(text, token):
    """text with every copy of token taken out, in case an answer repeats the token it was sent."""
    return text.replace(token, "[token]") if token else text


class NoRedirects(urllib.request.HTTPRedirectHandler):
    # A redirect is an answer to report, not to follow: following it could send the token somewhere else.
    def redirect_request(self, *args, **kwargs):
        return None


# urllib.request reads the proxy settings of the environment (http_proxy, https_proxy and no_proxy, in either
# case) by default, but not ALL_PROXY.
opener = urllib.request.build_opener(NoRedirects)


def on_total_timeout(signum, frame):
    raise TotalTimeout()


@contextlib.contextmanager
def total_time_limit():
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
