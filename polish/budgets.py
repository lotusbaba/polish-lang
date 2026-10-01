"""Adapters for configured budgets rules."""
from .rule_engine import ConfiguredModel


class ResourceBudgets(ConfiguredModel):
    rule_module = "budgets"
