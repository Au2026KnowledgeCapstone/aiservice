"""Offline prompt-size comparison; estimates are heuristics, never measured model usage."""
import argparse
import ast
import json
from pathlib import Path


def measure(text):
    return {
        "characters": len(text), "utf8_bytes": len(text.encode()),
        "english_token_heuristic": round(len(text) / 4),
        "reservation_input_bytes_plus_framing": len(text.encode()) + 1024,
        "measured_tokens": None, "cost_usd": None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--python-constant", help="Extract a literal string constant without executing source")
    args = parser.parse_args()
    result = []
    for path in args.files:
        text = path.read_text()
        if args.python_constant:
            matches = [node.value for node in ast.walk(ast.parse(text))
                       if isinstance(node, ast.Assign) and any(
                           isinstance(target, ast.Name) and target.id == args.python_constant
                           for target in node.targets)]
            if len(matches) != 1:
                parser.error(f"Expected exactly one {args.python_constant} assignment in {path}")
            text = ast.literal_eval(matches[0])
            if not isinstance(text, str):
                parser.error("Selected constant must be a literal string")
        result.append({"file": str(path), **measure(text)})
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
