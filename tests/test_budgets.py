from pathlib import Path

import pytest

from polish import compile_source, simulate

SOURCE = (Path(__file__).resolve().parents[1] / "examples/budgets.polishd").read_text()


def run(source=SOURCE, index=0):
    result = compile_source(source)
    assert result.ok, result.diagnostics
    return simulate(result.architecture, result.architecture.scenarios[index])


def test_examples_and_math():
    for index in range(5):
        assert run(index=index).passed
    records = run().budgets
    assert records[0]["base_memory_mib"] == 2048
    assert records[0]["inflight_memory_mib"] == 80
    assert records[1]["potential_per_instance"] == 80
    result = run(index=2)
    assert result.error == "CONNECTION_BUDGET_EXCEEDED"
    assert all(not r["overloaded"] for r in result.capacity)
    assert result.budgets[-1]["potential_per_instance"] == 400


def test_smaller_connection_pool_fixes_scale_out():
    assert run(SOURCE.replace("connection_pool_size = 20", "connection_pool_size = 9"), 2).outcome == "success"
    assert run(SOURCE.replace("connection_pool_size = 20", "connection_pool_size = 10"), 2).error == "CONNECTION_BUDGET_EXCEEDED"


def test_repeated_queries_do_not_duplicate_pools():
    source = SOURCE.replace("writes Products.Items { cpu_ms = 0.1 }", "writes Products.Items { cpu_ms = 0.1 } reads Products.Items { cpu_ms = 0.1 }")
    assert run(source).budgets[-1]["potential_per_instance"] == 80


def test_single_request_must_fit_one_instance():
    source = SOURCE.replace("memory_per_request_mib = 200", "memory_per_request_mib = 10000").replace("rate_rps = 200", "rate_rps = 0.01")
    assert run(source, 4).error == "MEMORY_CAPACITY_EXCEEDED"


def test_unvisited_services_still_consume_base_memory():
    source = SOURCE.replace("service Checkout {", "service Worker { hosted_on Apps base_memory_mib = 6000 } service Checkout {")
    assert run(source, 3).error == "MEMORY_CAPACITY_EXCEEDED"


@pytest.mark.parametrize("old,new", [
    ("connection_pool_size = 20", "connection_pool_size = true"),
    ("max_connections = 200", "max_connections = 0"),
    ("reserved_connections = 20", "reserved_connections = 200"),
    ("reserved_connections = 20", "reserved_connections = -1"),
    ("base_memory_mib = 512", "base_memory_mib = -1"),
    ("duration_ms = 100", "duration_ms = false"),
    ("memory_utilization = 0.8", "memory_utilization = 2"),
])
def test_invalid_inputs(old, new):
    result = compile_source(SOURCE.replace(old, new, 1))
    assert "E_SCALING" in {d.code for d in result.diagnostics}


@pytest.mark.parametrize("removed", ["duration_ms = 100", "connection_pool_size = 20", "base_memory_mib = 512"])
def test_incomplete_budgets_fail_explicitly(removed):
    assert run(SOURCE.replace(removed, "", 1)).error == "SCALING_MODEL_INCOMPLETE"
