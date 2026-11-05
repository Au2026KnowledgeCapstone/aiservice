import ast
from pathlib import Path

ENGINES_DIR = Path(__file__).resolve().parents[1] / "engines"


def _app_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders += [
                alias.name
                for alias in node.names
                if alias.name == "app" or alias.name.startswith("app.")
            ]
        elif isinstance(node, ast.ImportFrom):
            if node.module and (node.module == "app" or node.module.startswith("app.")):
                offenders.append(node.module)
    return offenders


def test_engines_do_not_import_app():
    offenders = {
        str(path): found
        for path in ENGINES_DIR.rglob("*.py")
        if (found := _app_imports(path))
    }
    assert not offenders, offenders
