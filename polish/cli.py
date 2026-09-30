import argparse
import json
from pathlib import Path

from .compiler import compile_source
from .simulator import simulate
from .configuration import load_config, ConfigurationError
from .errors import render_error


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="polish", description="Check and simulate architecture specifications.")
    parser.add_argument("command", choices=["check", "simulate"])
    parser.add_argument("file", type=Path)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable diagnostics and traces")
    parser.add_argument("--scenario", help="Run one named scenario")
    parser.add_argument("--config-dir", type=Path, help="Directory containing language definition JSON files")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config_dir)
        compilation = compile_source(args.file.read_text(encoding="utf-8"), config_dir=args.config_dir)
    except ConfigurationError as exc:
        payload = {"ok": False, "diagnostics": [{"code": "E_CONFIG", "message": str(exc)}], "results": []}
    except (OSError, UnicodeError) as exc:
        code, message = render_error(config["errors"], "E_FILE", detail=str(exc))
        payload = {"ok": False, "diagnostics": [{"code": code, "message": message}], "results": []}
    else:
        payload = {"ok": compilation.ok, "diagnostics": [d.to_dict() for d in compilation.diagnostics], "results": []}
        if compilation.ok:
            arch = compilation.architecture
            payload["architecture"] = arch.name
            payload["components"] = len(arch.nodes)
            if args.command == "simulate":
                scenarios = [s for s in arch.scenarios if args.scenario is None or s.name == args.scenario]
                if not scenarios:
                    payload["ok"] = False
                    code, message = render_error(arch.errors, "E_NO_SCENARIOS")
                    payload["diagnostics"].append({"code": code, "message": message})
                else:
                    payload["results"] = [simulate(arch, s).to_dict() for s in scenarios]
                    payload["ok"] = all(r["passed"] for r in payload["results"])
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for diagnostic in payload["diagnostics"]:
            location = f"{args.file}:{diagnostic.get('line', 1)}:{diagnostic.get('column', 1)}"
            print(f"{location}: {diagnostic['code']}: {diagnostic['message']}")
        for result in payload["results"]:
            print(f"{'PASS' if result['passed'] else 'FAIL'} {result['scenario']} ({result['outcome']})")
            for step in result["trace"]:
                print(f"  {step}")
            for failure in result["failures"]:
                print(f"  Assertion failed: {failure}")
        if args.command == "check" and payload["ok"]:
            print(f"OK {payload['architecture']}: {payload['components']} components checked")
    return 0 if payload["ok"] else 1
