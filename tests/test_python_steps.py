"""Every Python step in the reusable workflow: the rules that hold for all of them."""

import ast
import unittest

from harness import python_steps, step_script


class PythonStepsTest(unittest.TestCase):
    def test_the_workflow_has_python_steps(self):
        self.assertLessEqual({("decide", "lookup"), ("decide", "final"), ("fire", "send")}, set(python_steps()))

    def test_each_python_step_is_python_3_10(self):
        # The steps keep to Python 3.10, so a runner image with an older Python than today's still runs them. This
        # checks the grammar only, not which standard library functions a step calls.
        for job, step_id in python_steps():
            with self.subTest(job=job, step=step_id):
                script, _ = step_script(job, step_id)
                ast.parse(script, filename=f"{job}.{step_id}", feature_version=(3, 10))


if __name__ == "__main__":
    unittest.main()
