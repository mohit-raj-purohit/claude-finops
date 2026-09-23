"""Every finops module must import cleanly; a removed re-export breaks routes that lazy-import.

Run: python3 -m unittest tests.test_imports -v
"""
import importlib
import os
import pkgutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import finops


class TestImports(unittest.TestCase):
    def test_every_module_imports(self):
        for m in pkgutil.iter_modules(finops.__path__):
            with self.subTest(module=m.name):
                importlib.import_module(f"finops.{m.name}")


if __name__ == "__main__":
    unittest.main()
