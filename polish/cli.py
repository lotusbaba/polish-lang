import argparse
import json
from pathlib import Path

from .compiler import compile_source
from .simulator import simulate
from .configuration import load_config, ConfigurationError
from .errors import render_error


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="polish", description="Check and simulate architecture specifications.")
    parser.add_argument("command", choices=["check", "simulate", "plan"])
    parser.add_argument("file", type=Path)
    parser.add_argument("--proposed", type=Path, help="Proposed architecture for plan")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable diagnostics and traces")
    parser.add_argument("--scenario", help="Run one named scenario")
    parser.add_argument("--config-dir", type=Path, help="Directory containing language definition JSON files")
    parser.add_argument("--candidates", type=Path, help="JSON manifest of candidate changes for plan")
    parser.add_argument("--objective", help="Objective for choosing among validated candidate changes")
    parser.add_argument("--decision-provider", choices=["none", "jev", "laya"], default="none",
                        help="Optional recommendation model; none performs offline candidate checks")
    parser.add_argument("--choice-evaluation", type=Path, help="Independent labeled evaluation JSON for historical choice accuracy intervals")
    parser.add_argument("--laya-checkpoint", type=Path, help="Existing local Laya checkpoint directory (no downloads)")
    parser.add_argument("--laya-device", choices=["cpu", "mps", "cuda"], default=None)
    parser.add_argument("--laya-timeout", type=float, default=None, help="Local worker time limit, default 180 seconds")
    parser.add_argument("--laya-python", type=Path, help="Optional separate Python runtime with Laya installed")
    args = parser.parse_args(argv)
    if args.decision_provider == "laya" and not args.laya_checkpoint:
        parser.error("--decision-provider laya requires --laya-checkpoint")
    if args.decision_provider != "laya" and any(v is not None for v in (args.laya_checkpoint,args.laya_device,args.laya_timeout,args.laya_python)):
        parser.error("--laya-* options require --decision-provider laya")
    if (args.candidates or args.objective or args.decision_provider != "none") and args.command != "plan":
        parser.error("recommendation options are only supported with plan")
    if bool(args.candidates) != bool(args.objective):
        parser.error("--candidates and --objective must be supplied together")
    if args.decision_provider != "none" and not args.candidates:
        parser.error("--decision-provider requires --candidates and --objective")
    if args.choice_evaluation and (not args.candidates or args.decision_provider == "none"):
        parser.error("--choice-evaluation requires candidates and a decision provider")
    if args.command == "plan":
        if args.proposed is None:
            parser.error("plan requires --proposed FILE")
        from .planner import plan
        from .recommendations import recommend, load_candidates, JevDecisionModel
        from .choice_evaluation import load_evaluation
        try:
            model = JevDecisionModel() if args.decision_provider == "jev" else None
            if args.decision_provider == "laya":
                from .laya_provider import LayaDecisionModel
                model = LayaDecisionModel(args.laya_checkpoint, device=args.laya_device or "cpu",
                                          timeout=args.laya_timeout if args.laya_timeout is not None else 180,
                                          python=args.laya_python.absolute() if args.laya_python else None)
            if args.candidates:
                payload = recommend(args.file.read_text(), args.proposed.read_text(),
                                    load_candidates(args.candidates), objective=args.objective,
                                    model=model,
                                    config_dir=args.config_dir, scenario_name=args.scenario,
                                    evaluation=load_evaluation(args.choice_evaluation) if args.choice_evaluation else None)
            else:
                payload = plan(args.file.read_text(), args.proposed.read_text(), config_dir=args.config_dir, scenario_name=args.scenario)
        except (OSError, UnicodeError, ConfigurationError, ValueError) as exc:
            payload = {"ok": False, "diagnostics": [{"message": str(exc)}]}
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            for diagnostic in payload.get("diagnostics", []):
                print(f"{diagnostic.get('side', '')}: {diagnostic['message']}")
            print(f"Potentially affected components: {len(payload.get('impact', []))}")
            for comparison in payload.get("comparisons", []):
                print(f"{comparison['status']}: {comparison['scenario']} ({comparison['origin']} requirements)")
            for finding in payload.get("findings", []):
                print(f"{finding['category']}: {finding['code']}")
                print(f"  Evidence: {finding.get('evidence', '')}")
                for suggestion in finding.get("suggestions", []):
                    print(f"  Suggestion: {suggestion}")
            advice = payload.get("recommendations")
            if advice:
                print(f"Recommendations: {advice['status']}")
                for candidate in advice['candidates']:
                    print(f"  {candidate['id']}: {'eligible' if candidate['eligible'] else 'rejected'} — {candidate['description']}")
                print(f"  Selected: {advice['selected']} ({advice['selection']})")
                if advice['probabilities'] is not None:
                    print(f"  Choice probabilities (uncalibrated): {advice['probabilities']}")
                if advice.get('choice_evaluation'):
                    print("  Historical choice accuracy: " + json.dumps(advice['choice_evaluation']))
                if advice.get('evaluation_error'):
                    print(f"  Evaluation unavailable: {advice['evaluation_error']}")
                if advice.get('error'):
                    print(f"  Model error: {advice['error']}")
                print(f"  Confidence interval: unavailable. {advice['confidence_interval_reason']}")
            for limitation in payload.get("limitations", []):
                print(f"Note: {limitation}")
        return 0 if payload["ok"] else 1
    if args.proposed is not None:
        parser.error("--proposed is only supported with plan")
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
            print(f"{'PASS' if result['passed'] else 'FAIL'} {result['scenario']}")
            if result['passed']:
                label = {"success": "Expected success observed", "error": "Expected failure observed", "denied": "Expected denial observed"}[result['outcome']]
            else:
                label = f"Observed request outcome: {result['outcome']}"
            detail = f": {result['error']}" if result['error'] else ""
            print(f"  {label}{detail}")
            for step in result["trace"]:
                print(f"  {step}")
            for failure in result["failures"]:
                print(f"  Assertion failed: {failure}")
        if args.command == "check" and payload["ok"]:
            print(f"OK {payload['architecture']}: {payload['components']} components checked")
    return 0 if payload["ok"] else 1
