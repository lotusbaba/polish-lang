"""Adapters for configured runtime resources rules."""
from .rule_engine import ConfiguredModel, run

def validate_runtime(arch, error):
    return run(arch, "runtime_resources", "validate_runtime", arch, error)


class RuntimeResources(ConfiguredModel):
    rule_module = "runtime_resources"
