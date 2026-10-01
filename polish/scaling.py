"""Adapters for configured scaling rules."""
from .rule_engine import ConfiguredModel, run
import math

def number(value, positive=False):
    try:
        return type(value) in (int, float) and math.isfinite(value) and (value > 0 if positive else value >= 0)
    except OverflowError:
        return False

def hardware(arch, host):
    return run(arch, "scaling", "hardware", arch, host)

def validate_scaling(arch, error):
    return run(arch, "scaling", "validate_scaling", arch, error)


class CapacityModel(ConfiguredModel):
    rule_module = "scaling"
