import ast
from pathlib import Path


ALLOWED_ROOTS = {
    "collections",
    "ctypes",
    "datetime",
    "hashlib",
    "json",
    "math",
    "os",
    "pathlib",
    "random",
    "re",
    "sys",
    "threading",
    "time",
    "tkinter",
    "traceback",
    "uuid",
    "minoru_studio_resolve",
}


def test_adapter_is_python36_and_standard_library_only():
    root = Path("resolve_adapter/minoru_studio_resolve")
    for path in root.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path), feature_version=(3, 6))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                roots = [(node.module or "").split(".", 1)[0]]
            else:
                continue
            assert set(roots) <= ALLOWED_ROOTS, (path, roots)
