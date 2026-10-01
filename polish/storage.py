"""Adapters for configured storage rules."""
from .rule_engine import ConfiguredModel, run

def copies(properties, arch=None):
    if arch is None:
        from .configuration import load_config
        from .model import Architecture
        config = load_config()
        arch = Architecture("storage", rules=config["rules"])
    return run(arch, "storage", "copies", properties)

def validate_storage(arch, error, config):
    return run(arch, "storage", "validate_storage", arch, error, config)
