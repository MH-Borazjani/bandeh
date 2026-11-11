# Copyright (C) 2026 Mohammadhossein Borazjani
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""Unified unittest runner for all standalone check_*.py scripts.

Allows running every check script and unit/integration test via:

    .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SCHEMA_CHECK = REPO_ROOT / "app" / "e-commerce" / "schema" / "check_models.py"


class StandaloneCheckScriptsTest(unittest.TestCase):
    """Run each standalone self-check script in an isolated subprocess."""

    def test_all_check_scripts(self) -> None:
        scripts = sorted(TESTS_DIR.glob("check_*.py"))
        if SCHEMA_CHECK.is_file():
            scripts.append(SCHEMA_CHECK)

        self.assertGreater(len(scripts), 0)
        for script in scripts:
            with self.subTest(script=script.name):
                proc = subprocess.run(
                    [sys.executable, str(script)],
                    cwd=REPO_ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(
                    proc.returncode,
                    0,
                    msg=f"{script.name} failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
                )


if __name__ == "__main__":
    unittest.main()
