"""Every Python step in the reusable workflow: the rules that hold for all of them."""

import ast
import unittest

from harness import STEPS_DIR, file_steps, python_steps, python_steps_without_id, step_script

# The shared helper module the step files import, and the one copy of the scaffolding that the inline steps must match.
HELPERS = STEPS_DIR / "helpers.py"

# The Python steps still inline in the workflow, as (job, step id). None are left: every Python step runs from a file
# in steps/. A new inline step must be added here, and so is checked against the helper module below.
INLINE_STEPS = set()


class PythonStepsTest(unittest.TestCase):
    def test_the_inline_python_steps_are_the_listed_ones(self):
        self.assertEqual(set(python_steps()), INLINE_STEPS)

    def test_the_lookup_the_report_and_the_routine_call_run_from_files(self):
        self.assertLessEqual({("decide", "lookup"), ("decide", "final"), ("fire", "send")}, set(file_steps()))

    def test_each_python_step_has_an_id_so_it_is_listed_and_checked(self):
        self.assertEqual(python_steps_without_id(), [])

    def test_each_python_step_is_python_3_10(self):
        # The steps keep to Python 3.10, so a runner image with an older Python than today's still runs them. This
        # checks the grammar only, not which standard library functions a step calls.
        for job, step_id in python_steps():
            with self.subTest(job=job, step=step_id):
                script, _ = step_script(job, step_id)
                ast.parse(script, filename=f"{job}.{step_id}", feature_version=(3, 10))
        for path in sorted(STEPS_DIR.glob("*.py")):
            with self.subTest(file=path.name):
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 10))


# Scaffolding that a step still inline in the workflow defines as its own copy, because an inline script cannot import
# the shared helper module. Each copy must be identical to the module's definition, so that a hardening fix made in the
# module is not missed in a copy. While INLINE_STEPS is empty these checks find no copies, and they stay to catch a new
# inline step.


# Scaffolding every inline step that makes a request defines.
REQUEST_SCAFFOLDING = (
    "TIMEOUT_SECONDS",
    "TOTAL_SECONDS",
    "TotalTimeout",
    "escaped",
    "NoRedirects",
    "opener",
    "on_total_timeout",
    "total_time_limit",
)
# Scaffolding any inline step may define; every copy of it must be identical to the module's.
SHARED_SCAFFOLDING = REQUEST_SCAFFOLDING + ("output",)


def top_level_definitions(script):
    """Maps each name a script defines at its top level (a function, a class or an assignment) to its source text."""
    definitions = {}
    for node in ast.parse(script).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        else:
            continue
        # Decorators are part of the definition, though ast places them before the node's own start.
        start = min([node.lineno] + [decorator.lineno for decorator in getattr(node, "decorator_list", [])])
        text = "\n".join(script.splitlines()[start - 1:node.end_lineno])
        for name in names:
            definitions[name] = text
    return definitions


def makes_requests(script):
    return any(
        isinstance(node, ast.Import) and any(alias.name == "urllib.request" for alias in node.names)
        for node in ast.walk(ast.parse(script))
    )


class SharedScaffoldingTest(unittest.TestCase):
    def setUp(self):
        self.helpers = top_level_definitions(HELPERS.read_text(encoding="utf-8"))
        self.steps = {}
        for job, step_id in python_steps():
            script, _ = step_script(job, step_id)
            self.steps[f"{job}.{step_id}"] = script

    def test_the_helper_module_defines_all_the_shared_scaffolding(self):
        self.assertEqual(set(SHARED_SCAFFOLDING) - set(self.helpers), set())

    def test_each_inline_step_that_makes_requests_defines_all_the_request_scaffolding(self):
        requesting = [step for step, script in self.steps.items() if makes_requests(script)]
        for step in requesting:
            with self.subTest(step=step):
                missing = set(REQUEST_SCAFFOLDING) - set(top_level_definitions(self.steps[step]))
                self.assertEqual(missing, set())

    def test_every_inline_copy_of_the_shared_scaffolding_is_identical_to_the_helper_module(self):
        for name in SHARED_SCAFFOLDING:
            for step, script in self.steps.items():
                definition = top_level_definitions(script).get(name)
                if definition is not None:
                    with self.subTest(name=name, step=step):
                        self.assertEqual(definition, self.helpers[name], f"{name} in {step} differs from {HELPERS.name}")


if __name__ == "__main__":
    unittest.main()
