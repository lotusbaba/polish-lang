"""Adapters for configured cache connections rules."""
from .rule_engine import ConfiguredModel, run

def validate_cache_connections(arch, error):
    return run(arch, "cache_connections", "validate_cache_connections", arch, error)


class CacheConnections(ConfiguredModel):
    rule_module = "cache_connections"
