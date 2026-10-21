"""Find every module a Python file imports, directly or indirectly.

This is the example from the README. Run it from the repository root with
`python examples/py-file-imports/main.py` (or install cachify first).
"""

import ast
import os
import shutil
import sys

# Use the cachify package in this repository when it is not installed.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from cachify import cache  # noqa: E402

@cache
def direct_imports(path: str) -> frozenset[str]:
    print(f"  parsing {os.path.basename(path)}")
    with open(path) as f:  # Cachify notices when this file changes
        tree = ast.parse(f.read(), path)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):  # import x, y
            names.update(alias.name for alias in node.names)
        # from x import y, but not relative imports like from . import y
        elif (isinstance(node, ast.ImportFrom) and node.module
              and not node.level):
            names.add(node.module)
    return frozenset(names)

# Modules can import each other, so this function can call itself.
# cycle_initial makes Cachify solve that cycle instead of raising CycleError.
@cache(cycle_initial=lambda path: frozenset())
def all_imports(path: str) -> frozenset[str]:
    print(f"  resolving {os.path.basename(path)}")
    folder = os.path.dirname(path)
    result = set(direct_imports(path))
    for name in sorted(direct_imports(path)):
        result |= all_imports(os.path.join(folder, f"{name}.py"))
    return frozenset(result)

def write(name: str, text: str) -> None:
    with open(name, "w") as f:
        f.write(text)

# Write the sample modules to modules/ next to this script, starting empty.
modules = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modules")
shutil.rmtree(modules, ignore_errors=True)
os.makedirs(modules)
os.chdir(modules)

write("app.py", "import db\nimport ui\n")
write("db.py", "import models\n")
write("models.py", "import db\n")  # db and models import each other
write("ui.py", "")

print("First run:")
print(sorted(all_imports(os.path.abspath("app.py"))))

print("Nothing changed:")
print(sorted(all_imports(os.path.abspath("app.py"))))

print("Only a comment changed in ui.py:")
write("ui.py", "# TODO: add widgets\n")
print(sorted(all_imports(os.path.abspath("app.py"))))

print("ui.py gained an import:")
write("ui.py", "import theme\n")
write("theme.py", "")
print(sorted(all_imports(os.path.abspath("app.py"))))
