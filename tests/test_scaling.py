from pathlib import Path

import pytest

from polish import compile_source, simulate

SOURCE = (Path(__file__).resolve().parents[1] / "examples/scaling.polishd").read_text()


def run(source=SOURCE, index=0):
    compiled = compile_source(source)
    assert compiled.ok, compiled.diagnostics
    return simulate(compiled.architecture, compiled.architecture.scenarios[index])


def test_scenarios_and_math():
    for index in range(5):
        assert run(index=index).passed
    record = run().capacity[0]
    assert record["demand_cpu_ms_per_second"] == 2000
    assert record["capacity_cpu_ms_per_second"] == 1400
    assert record["required_instances"] == 2
    assert run(index=1).capacity[0]["capacity_cpu_ms_per_second"] == 5600


def test_more_replicas_fix_reads_but_not_writes():
    source = SOURCE.replace("read_replicas = 1", "read_replicas = 2")
    assert run(source, 2).outcome == "success"
    assert run(source, 4).error == "CAPACITY_EXCEEDED"
    assert run(source, 4).capacity[-1]["resource"] == "PostgresHost:primary"


def test_larger_instance_fixes_primary():
    source = SOURCE.replace('instance_type = "m7i.large"', 'instance_type = "m7i.xlarge"')
    assert run(source, 4).outcome == "success"


def test_shared_pool_aggregates_dependencies():
    source = SOURCE.replace("cpu_ms = 10", "cpu_ms = 4 invokes Worker.work")
    source = source.replace("service API {", "service Worker { hosted_on AppPool operation work { cpu_ms = 4 } } service API {")
    result = run(source)
    assert result.error == "CAPACITY_EXCEEDED"
    assert result.capacity[0]["demand_cpu_ms_per_second"] == 1600


def test_boundary_and_fractional_workload():
    source = SOURCE.replace("rate_rps = 200", "rate_rps = 140")
    assert run(source).outcome == "success"
    assert run(SOURCE.replace("rate_rps = 200", "rate_rps = 140.01")).error == "CAPACITY_EXCEEDED"


@pytest.mark.parametrize("old,new", [
    ('"m7i.large"', '"not-real"'),
    ("provider = aws", "provider = other"),
    ("cpu_utilization = 0.7", "cpu_utilization = 1.1"),
    ("cpu_utilization = 0.7", "cpu_utilization = 0"),
    ("rate_rps = 200", "rate_rps = -1"),
    ("rate_rps = 200", "rate_rps = true"),
    ("cpu_ms = 10", "cpu_ms = -1"),
    ("cpu_ms = 20", "cpu_ms = true"),
    ("scale = minimum", "scale = automatic"),
])
def test_invalid_scaling_declarations(old, new):
    result = compile_source(SOURCE.replace(old, new, 1))
    assert "E_SCALING" in {d.code for d in result.diagnostics}


def test_missing_cost_never_silently_means_free():
    assert run(SOURCE.replace("cpu_ms = 10", "")).error == "SCALING_MODEL_INCOMPLETE"
    assert run(SOURCE.replace("cpu_ms = 20", ""), 2).error == "SCALING_MODEL_INCOMPLETE"


def test_maximum_does_not_invent_unlimited_capacity():
    source = SOURCE.replace("rate_rps = 200", "rate_rps = 1000")
    assert run(source, 1).error == "CAPACITY_EXCEEDED"


def test_scenario_capacity_is_isolated():
    arch = compile_source(SOURCE).architecture
    first = simulate(arch, arch.scenarios[1])
    assert first.capacity == simulate(arch, arch.scenarios[1]).capacity
