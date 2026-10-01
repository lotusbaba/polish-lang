"""Adapters for configured platforms rules."""
from .rule_engine import ConfiguredModel


class PlatformModel(ConfiguredModel):
    rule_module = "platforms"
