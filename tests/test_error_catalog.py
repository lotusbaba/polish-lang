"""Ensure bundled definitions match every error call's parameter contract."""
import ast
from pathlib import Path

from polish.configuration import load_config


def test_all_error_calls_have_matching_definitions():
    root = Path(__file__).resolve().parents[1] / "polish"
    catalog = load_config()["errors"]
    for filename, function, phase in (("compiler.py", "error", "compile"),
                                      ("storage.py", "error", "compile"),
                                      ("simulator.py", "fail", "simulation")):
        for node in ast.walk(ast.parse((root / filename).read_text())):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != function:
                continue
            definition = catalog[node.args[0].value]
            assert definition["phase"] == phase
            actual = {arg.arg for arg in node.keywords} - {"line", "column"}
            assert actual == set(definition["parameters"]), node.args[0].value
