"""Runs one step of the reusable workflow on its own, with a controlled environment, for the unit tests.

A reusable workflow runs in the calling repository, so its steps' scripts are kept inline in
.github/workflows/claude-review.yml. The harness reads a step's script out of the workflow with yq, so the tests run
exactly what ships, and runs it the way the runner would: with the step's shell, and with GITHUB_OUTPUT,
GITHUB_STEP_SUMMARY and RUNNER_TEMP pointing at temporary files. Nothing else is inherited from the environment
running the tests, so no proxy settings or real tokens leak in.

Needs python3 (3.10 or later), bash and yq. Either yq works: mikefarah's (as on GitHub's ubuntu-latest runners) or
the Python jq wrapper.

StubServer is a GitHub API stand-in on 127.0.0.1, answering each path with the status and body a test gives it.
"""

import functools
import http.server
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading

WORKFLOW = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows" / "claude-review.yml"

# Set by the runner for every step; a test passes the step's own inputs, which must be declared in the step's env.
# The shell a Python step names, so the runner runs its script with python3.
PYTHON_SHELL = "python3 {0}"

RUNNER_VARIABLES = {"GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY", "GITHUB_API_URL", "RUNNER_TEMP", "PATH", "LC_ALL"}


def _yq(query):
    result = subprocess.run(["yq", "-r", query, str(WORKFLOW)], capture_output=True, text=True, check=True)
    return result.stdout


def _step_query(job, step_id):
    return f'.jobs.{job}.steps[] | select(.id == "{step_id}")'


class StepResult:
    """What a step run left behind: its exit code, its standard output and error, its outputs and its summary."""

    def __init__(self, returncode, stdout, stderr, output_file, summary):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.output_file = output_file
        self.summary = summary
        self.outputs = parse_outputs(output_file)

    def errors(self):
        """The messages of the ::error:: annotations the step printed."""
        return [line[len("::error::"):] for line in self.stdout.splitlines() if line.startswith("::error::")]

    def everything(self):
        """All the text the step produced, for checking that a secret appears nowhere."""
        return "\n".join([self.stdout, self.stderr, self.output_file, self.summary])


def parse_outputs(text):
    """Reads a GITHUB_OUTPUT file as the runner does. A name written twice keeps its last value."""
    outputs = {}
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if line == "":
            continue
        if "<<" in line and ("=" not in line or line.index("<<") < line.index("=")):
            name, delimiter = line.split("<<", 1)
            value = []
            while i < len(lines) and lines[i] != delimiter:
                value.append(lines[i])
                i += 1
            if i == len(lines):
                raise ValueError(f"output {name!r} has no closing delimiter")
            i += 1
            outputs[name] = "\n".join(value)
        else:
            name, value = line.split("=", 1)
            outputs[name] = value
    return outputs


@functools.lru_cache(maxsize=None)
def step_script(job, step_id):
    """The step's script and shell, read out of the workflow."""
    script = _yq(_step_query(job, step_id) + " | .run")
    if script.strip() in ("", "null"):
        raise LookupError(f"no step with id {step_id!r} and a run script in job {job!r} of {WORKFLOW}")
    shell = _yq(_step_query(job, step_id) + ' | .shell // ""').strip()
    return script, shell


def python_steps():
    """(job, step id) for every step of the workflow that runs with python3."""
    steps = []
    for job in _yq(".jobs | keys | .[]").split():
        for step_id in _yq(f'.jobs.{job}.steps[] | select(.shell == "{PYTHON_SHELL}") | .id').split("\n"):
            if step_id:
                steps.append((job, step_id))
    return steps


@functools.lru_cache(maxsize=None)
def step_env_names(job, step_id):
    """The names in the step's env block."""
    return frozenset(_yq(_step_query(job, step_id) + " | (.env // {}) | keys | .[]").split())


def run_step(job, step_id, env, api_url="http://127.0.0.1:9", substitutions=None, timeout=20):
    """Runs the step with only the given env (plus the runner's own variables) and returns a StepResult.

    substitutions maps text in the script to its replacement, such as a timeout constant lowered for a test. Each
    piece of text must appear exactly once, so a test fails loudly if the script changes under it.
    """
    script, shell = step_script(job, step_id)
    # Untrusted text must reach a script through env, never through an expression placed in the script.
    if "${{" in script:
        raise AssertionError(f"step {step_id!r} has a workflow expression inside its script")
    undeclared = set(env) - step_env_names(job, step_id)
    if undeclared:
        raise AssertionError(f"step {step_id!r} does not declare env {sorted(undeclared)}")
    for old, new in (substitutions or {}).items():
        count = script.count(old)
        if count != 1:
            raise AssertionError(f"expected {old!r} once in step {step_id!r}, found it {count} times")
        script = script.replace(old, new)

    with tempfile.TemporaryDirectory() as temp:
        temp = pathlib.Path(temp)
        output_path = temp / "output"
        summary_path = temp / "summary"
        output_path.touch()
        summary_path.touch()
        script_path = temp / "step"
        script_path.write_text(script, encoding="utf-8")
        if shell == PYTHON_SHELL:
            command = [sys.executable, str(script_path)]
        elif shell in ("", "bash"):
            # GitHub's default for a run step on Linux.
            command = ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script_path)]
        else:
            raise AssertionError(f"step {step_id!r} uses shell {shell!r}, which the harness cannot run")
        runner_temp = temp / "runner"
        runner_temp.mkdir()
        full_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LC_ALL": "C.UTF-8",
            "GITHUB_OUTPUT": str(output_path),
            "GITHUB_STEP_SUMMARY": str(summary_path),
            "GITHUB_API_URL": api_url,
            "RUNNER_TEMP": str(runner_temp),
            **env,
        }
        completed = subprocess.run(command, env=full_env, capture_output=True, text=True, timeout=timeout)
        return StepResult(
            completed.returncode,
            completed.stdout,
            completed.stderr,
            output_path.read_text(encoding="utf-8"),
            summary_path.read_text(encoding="utf-8"),
        )


def closed_port_url():
    """A URL on 127.0.0.1 that nothing listens on, for a connection that is refused."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}"


class StubServer:
    """A stand-in for the GitHub API on 127.0.0.1.

    routes maps a path to (status, body), to HANG for an answer that never comes, or to DROP for a connection closed
    without an answer. A path with no route answers
    404. Every request is recorded as (method, path, headers). Use as a context manager.
    """

    HANG = object()
    DROP = object()

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.requests = []
        self._release = threading.Event()
        stub = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                # Headers stay a message, so a lookup by name ignores case as HTTP does.
                stub.requests.append(("GET", self.path, self.headers))
                route = stub.routes.get(self.path, (404, '{"message": "Not Found"}'))
                if route is StubServer.HANG:
                    stub._release.wait(10)
                    return
                if route is StubServer.DROP:
                    # Closes the connection without an answer.
                    self.close_connection = True
                    return
                status, body = route
                data = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                if 300 <= status < 400:
                    self.send_header("Location", "http://127.0.0.1:9/elsewhere")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._release.set()
        self._server.shutdown()
        self._server.server_close()
        return False

    def paths(self):
        return [path for _, path, _ in self.requests]
