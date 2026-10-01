"""Deterministic functional simulation; no network requests or deployments."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from .model import Architecture, Scenario


@dataclass
class SimulationResult:
    scenario: str
    passed: bool = False
    outcome: str = "success"
    error: str | None = None
    trace: list[str] = field(default_factory=list)
    reached: list[str] = field(default_factory=list)
    accessed: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    capacity: list[dict] = field(default_factory=list)
    budgets: list[dict] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


class RequestFailure(Exception):
    def __init__(self, code, message, outcome="error"):
        self.code, self.message, self.outcome = code, message, outcome


def simulate(arch: Architecture, scenario: Scenario) -> SimulationResult:
    from .rule_engine import run, RuleError
    try:
        return run(arch, 'simulator', 'simulate', arch, scenario)
    except RuleError as exc:
        result = SimulationResult(scenario.name, outcome='error', error='E_CONFIG')
        result.trace.append(str(exc))
        result.failures.append(str(exc))
        result.diagnostics.append({'rule': 'CONFIG_RULE', 'code': 'E_CONFIG', 'message': str(exc)})
        return result
