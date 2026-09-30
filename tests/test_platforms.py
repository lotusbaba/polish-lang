from pathlib import Path

import pytest

from polish import compile_source, simulate

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = (ROOT / "examples/platforms.polishd").read_text()
PROCESSING = (ROOT / "examples/aws-processing.polishd").read_text()


def run(source=PLATFORMS, index=0):
    result = compile_source(source)
    assert result.ok, result.diagnostics
    return simulate(result.architecture, result.architecture.scenarios[index])


@pytest.mark.parametrize("source", [PLATFORMS, PROCESSING])
def test_platform_scenarios(source):
    arch = compile_source(source).architecture
    for scenario in arch.scenarios:
        result = simulate(arch, scenario)
        assert result.passed, result.to_dict()


def test_cache_hit_skips_database_work():
    hit = run()
    miss = run(index=1)
    assert "Accounts.Users" not in hit.accessed
    assert "Accounts.Users" in miss.accessed
    assert hit.capacity[0]["demand_cpu_ms_per_second"] == 50
    assert miss.capacity[0]["demand_cpu_ms_per_second"] == 150


@pytest.mark.parametrize("old,new", [
    ("product = redis", "product = memcached"),
    ("product = elasticache_memcached", "product = elasticache_redis"),
    ("provider = aws product = cockroachdb", "provider = self_hosted product = cockroachdb"),
    ("provider = self_hosted product = mongodb", "provider = aws product = mongodb"),
    ("product = signal_sciences", "product = next_gen_waf"),
])
def test_deployment_variants(old, new):
    assert run(PLATFORMS.replace(old, new), 1).passed


def test_managed_redis_on_aws():
    source = PLATFORMS.replace("provider = self_hosted product = redis\n    hosted_on Local", "provider = aws product = elasticache_redis")
    assert run(source).passed


def test_cache_capacity_and_missing_model():
    assert run(PLATFORMS.replace("dataset_mib = 100", "dataset_mib = 600", 1)).error == "CACHE_CAPACITY_EXCEEDED"
    assert run(PLATFORMS.replace("max_ops_rps = 1000", "max_ops_rps = 49", 1)).error == "PLATFORM_CAPACITY_EXCEEDED"
    assert run(PLATFORMS.replace("max_ops_rps = 1000", "", 1)).error == "SCALING_MODEL_INCOMPLETE"


@pytest.mark.parametrize("entry,rate", [("Edge", 1001), ("Waf", 801), ("Kong", 401), ("AwsGateway", 501)])
def test_each_rate_limiter(entry, rate):
    source = PLATFORMS.replace("entry = Edge protocol = https", f"entry = {entry} protocol = http", 1).replace("rate_rps = 50", f"rate_rps = {rate}", 1)
    result = run(source)
    assert result.error == "RATE_LIMITED"
    assert "Redis" not in result.accessed


def test_burst_and_graphql_cost_limits():
    source = PLATFORMS.replace("rate_rps = 50", "rate_rps = 50 burst_requests = 101", 1)
    assert run(source).error == "RATE_LIMITED"
    assert run(PLATFORMS.replace("query_cost = 20", "query_cost = 101", 1)).error == "GRAPHQL_LIMIT_EXCEEDED"


def test_cockroach_overload():
    source = PLATFORMS.replace("nodes = 3 max_ops_rps = 200", "nodes = 3 max_ops_rps = 49")
    assert run(source, 1).error == "PLATFORM_CAPACITY_EXCEEDED"


def test_sqs_backlog_math_and_consumers():
    result = run(PROCESSING, 1)
    assert any(r.get("messages") == 25 and r["resource"] == "Jobs" for r in result.budgets)
    assert run(PROCESSING.replace("consumer_rate_rps = 5", "consumer_rate_rps = 10"), 1).outcome == "success"


def test_emr_worker_math():
    result = run(PROCESSING, 5)
    batch = next(r for r in result.budgets if r["kind"] == "batch_cpu")
    assert batch["demand"] == 3000 and batch["capacity"] == 2800
    assert run(PROCESSING.replace("workers = 2", "workers = 3"), 5).outcome == "success"


@pytest.mark.parametrize("source,old,new", [
    (PROCESSING, "replication_factor = 3", "replication_factor = 4"),
    (PROCESSING, "queue_type = standard", "queue_type = random"),
    (PROCESSING, "workers = 2", "workers = false"),
    (PROCESSING, "framework = spark", "framework = unknown"),
    (PLATFORMS, "max_query_depth = 8", "max_query_depth = -1"),
    (PLATFORMS, "limit_rps = 1000", "limit_rps = 0"),
    (PLATFORMS, "provider = aws product = elasticache_memcached", "provider = aws product = elasticache_memcached hosted_on Local"),
    (PLATFORMS, "kind = document", "kind = relational"),
])
def test_product_validation(source, old, new):
    assert not compile_source(source.replace(old, new, 1)).ok


def test_unsupported_model_inputs_are_explicit():
    assert run(PROCESSING.replace("max_messages_rps = 100", "", 1)).error == "SCALING_MODEL_INCOMPLETE"
    assert run(PROCESSING.replace("worker_vcpus = 2", ""), 4).error == "SCALING_MODEL_INCOMPLETE"


def test_local_service_capacity():
    source = PLATFORMS.replace("hosted_on Apps", "hosted_on Local").replace("routes_to Apps", "routes_to Local")
    assert run(source).passed
