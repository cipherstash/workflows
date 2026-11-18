"""Every Python step in the reusable workflow: the rules that hold for all of them."""

import ast
import unittest

from harness import python_steps, python_steps_without_id, step_script


class PythonStepsTest(unittest.TestCase):
    def test_the_workflow_has_python_steps(self):
        self.assertLessEqual(
            {("decide", "lookup"), ("decide", "final"), ("fire", "send"), ("react", "reaction")}, set(python_steps())
        )

    def test_each_python_step_has_an_id_so_it_is_listed_and_checked(self):
        self.assertEqual(python_steps_without_id(), [])

    def test_each_python_step_is_python_3_10(self):
        # The steps keep to Python 3.10, so a runner image with an older Python than today's still runs them. This
        # checks the grammar only, not which standard library functions a step calls.
        for job, step_id in python_steps():
            with self.subTest(job=job, step=step_id):
                script, _ = step_script(job, step_id)
                ast.parse(script, filename=f"{job}.{step_id}", feature_version=(3, 10))


# Scaffolding every step that makes a request defines, as an identical copy: the steps are inline scripts for now and
# cannot share a module, so this check is what keeps a hardening fix from landing in one copy only.
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
# Scaffolding any step may define; every copy of it must be identical.
SHARED_SCAFFOLDING = REQUEST_SCAFFOLDING + ("error", "output")


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
        self.steps = {}
        for job, step_id in python_steps():
            script, _ = step_script(job, step_id)
            self.steps[f"{job}.{step_id}"] = script

    def test_each_step_that_makes_requests_defines_all_the_request_scaffolding(self):
        requesting = [step for step, script in self.steps.items() if makes_requests(script)]
        self.assertLessEqual({"decide.lookup", "fire.send"}, set(requesting))
        for step in requesting:
            with self.subTest(step=step):
                missing = set(REQUEST_SCAFFOLDING) - set(top_level_definitions(self.steps[step]))
                self.assertEqual(missing, set())

    def test_every_copy_of_the_shared_scaffolding_is_identical(self):
        for name in SHARED_SCAFFOLDING:
            copies = {}
            for step, script in self.steps.items():
                definition = top_level_definitions(script).get(name)
                if definition is not None:
                    copies[step] = definition
            with self.subTest(name=name):
                self.assertGreaterEqual(len(copies), 2, f"{name} is no longer shared")
                first_step, first = next(iter(copies.items()))
                for step, definition in copies.items():
                    self.assertEqual(definition, first, f"{name} in {step} differs from its copy in {first_step}")


if __name__ == "__main__":
    unittest.main()
