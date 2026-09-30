from pathlib import Path

import pytest

from polish import compile_source, simulate

SOURCE = (Path(__file__).resolve().parents[1] / "examples/storage.polishd").read_text()


def compile_ok(source=SOURCE):
    result = compile_source(source)
    assert result.ok, [d.to_dict() for d in result.diagnostics]
    return result.architecture


def run(source=SOURCE, index=0):
    arch = compile_ok(source)
    return simulate(arch, arch.scenarios[index])


def test_storage_example():
    arch = compile_ok()
    for scenario in arch.scenarios:
        result = simulate(arch, scenario)
        assert result.passed, result.to_dict()
    result = run()
    assert {"ProductStorage", "OrderStorage", "SessionStorage", "InternalLB"} <= set(result.reached)
    assert any("acknowledgements=3/3" in step for step in result.trace)


def test_fix_tls_makes_same_request_succeed():
    result = run(index=1)
    assert result.error == "TLS_UNSUPPORTED"
    fixed = SOURCE.replace("service Legacy {", "service Legacy { tls = true")
    result = run(fixed, 1)
    assert result.outcome == "success"
    assert "Legacy.lookup" in result.reached


@pytest.mark.parametrize("old,new,error", [
    ("load_balancer InternalLB { tls = true", "load_balancer InternalLB { tls = false", "TLS_UNSUPPORTED"),
    ("routes_to Apps", "routes_to OrderStorage", "WRONG_DESTINATION"),
    ("routes_to Apps", "routes_to InternalLB", "REQUEST_CYCLE"),
    ("tls = true routes_to Apps", "tls = true upstream_protocol = http routes_to Apps", "TLS_TERMINATION_REQUIRED"),
    ("tls = true routes_to Apps", "tls = true upstream_protocol = tls routes_to Apps", "PROTOCOL_MISMATCH"),
    ('route "/products" to ProductStorage', 'route "/elsewhere" to ProductStorage', "WRONG_DESTINATION"),
    ("tls = true storage = persistent replicas = 2", "tls = false storage = persistent replicas = 2", "TLS_UNSUPPORTED"),
])
def test_connections(old, new, error):
    result = run(SOURCE.replace(old, new, 1))
    assert result.error == error
    if error:
        assert "Products.Items" not in result.accessed


def test_tls_termination_and_direct_operation_route():
    source = SOURCE.replace("tls = true routes_to Apps", "tls_termination = true routes_to Inventory.reserve")
    assert run(source).passed


def test_no_replica():
    assert run(SOURCE.replace("read_replicas = 2", "read_replicas = 0", 1)).error == "NO_READ_REPLICA"


def test_eventual_reads_allow_async_replica():
    source = SOURCE.replace("consistency = strong", "consistency = eventual")
    assert run(source, 2).outcome == "success"


@pytest.mark.parametrize("old,new,code", [
    ("kind = key_value", "kind = document", "E_DATA_MODEL"),
    ("kind = document", "kind = relational", "E_DATA_MODEL"),
    ("kind = relational", "kind = key_value", "E_DATA_MODEL"),
    ("replication_factor = 3", "replication_factor = 2", "E_REPLICATION"),
    ("replication_factor = 3", "replication_factor = true", "E_SCALE"),
    ("replication = synchronous", "replication = asynchronous", "E_DURABILITY"),
    ("write_ack = quorum", "write_ack = primary", "E_DURABILITY"),
    ("storage = persistent", "storage = ephemeral", "E_DURABILITY"),
    ("failure_domains = 3", "failure_domains = 4", "E_REPLICATION"),
    ("copies = 3", "copies = 2", "E_CAPACITY"),
    ("partitions = 8 copies", "partitions = 4 copies", "E_CAPACITY"),
    ("backups = true", "backups = false", "E_BACKUP"),
    ("backup_retention_days = 14", "", "E_BACKUP"),
    ("backup_retention_days = 14", "backup_retention_days = -1", "E_SCALE"),
    ("key_type = string", "key_type = float", "E_STORAGE_VALUE"),
    ("value_type = json", "", "E_KEYSPACE"),
    ("protocol = https", "protocol = tls", "E_CONNECTION"),
    ("protocol = tls", "protocol = https", "E_CONNECTION"),
    ("via = InternalLB", "via = Missing", "E_REFERENCE"),
    ("read_from = replica", "read_from = any", "E_CONNECTION"),
    ("durability = disk", "durability = impossible", "E_CONNECTION"),
    ("via = InternalLB", "via = InternalLB protocol = https protocol = http", "E_DUPLICATE_PROPERTY"),
])
def test_compile_constraints(old, new, code):
    result = compile_source(SOURCE.replace(old, new, 1))
    assert code in {d.code for d in result.diagnostics}


def test_host_failure_domains_capacity():
    source = SOURCE.replace("copies = 3 failure_domains = 3", "copies = 3 failure_domains = 2", 1)
    assert "E_CAPACITY" in {d.code for d in compile_source(source).diagnostics}


def test_connection_options_are_preserved():
    arch = compile_ok()
    edge = arch.outgoing("Checkout.place_order", "invokes")[0]
    assert edge.properties == {"via": "InternalLB", "protocol": "https"}


def test_database_host_route_is_not_successful_http_endpoint():
    source = SOURCE.replace("entry = Checkout protocol = http method = POST path = \"/checkout\"", "entry = DataLB protocol = http")
    assert run(source).error == "PROTOCOL_MISMATCH"


def test_dependency_uses_own_path_not_callers_path():
    source = SOURCE.replace("routes_to Apps", 'route "/reserve" to Apps')
    source = source.replace("operation reserve {", 'operation reserve { method = PUT path = "/reserve"')
    assert run(source).passed


def test_database_tls_is_checked_as_well_as_host_tls():
    source = SOURCE.replace("kind = relational\n    tls = true", "kind = relational\n    tls = false")
    result = run(source)
    assert result.error == "TLS_UNSUPPORTED"
    assert "Products" in result.trace[-1]
    assert not result.accessed


def test_dependency_missing_route():
    source = SOURCE.replace("routes_to Apps", 'route "/not-reserve" to Apps')
    assert run(source).error == "NO_ROUTE"
