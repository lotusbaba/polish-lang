"""Adapters for configured cloud products rules."""
from .rule_engine import ConfiguredModel, run

def validate_cloud_products(arch, error):
    return run(arch, "cloud_products", "validate_cloud_products", arch, error)
