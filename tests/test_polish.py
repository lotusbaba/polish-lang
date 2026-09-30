import json
from pathlib import Path

import pytest

from polish import compile_source, simulate
from polish.cli import main

ROOT = Path(__file__).resolve().parents[1]
SHOP = (ROOT / "examples/shop.polishd").read_text()


def compiled(source=SHOP):
    result = compile_source(source)
    assert result.ok, [d.to_dict() for d in result.diagnostics]
    return result.architecture


def codes(source):
    return {d.code for d in compile_source(source).diagnostics}


def test_shop_scenarios():
    arch = compiled()
    assert len(arch.scenarios) == 9
    for scenario in arch.scenarios:
        result = simulate(arch, scenario)
        assert result.passed, result.to_dict()


@pytest.mark.parametrize("old,new,code", [
    ("loaded_from WebAssets", "loaded_from WebAssets loaded_from AppBalancer", "E_LOAD_COUNT"),
    ("Product invokes Catalog.get_product", "Catalog invokes Storefront", "E_RELATION_TYPE"),
    ("reads Products.Items", "reads Missing", "E_REFERENCE"),
    ("instances = 2..10", "instances = 10..2", "E_SCALE"),
    ("partitions = managed", "partitions = 0", "E_SCALE"),
    ("    replicas = 2", "    replicas = 1", "E_CAPACITY"),
    ("kind = relational", "kind = key_value", "E_DATA_MODEL"),
    ("rendering = static", "rendering = server", "E_LOAD_TYPE"),
    ("hosted_on AppPool", "hosted_on DatabaseCluster", "E_HOST_TYPE"),
    ("requires role Admin", "requires role CustomerX", "E_ROLE"),
    ("tls_termination = true", "tls_termination = 1", "E_PROPERTY_TYPE"),
    ("bot_mitigation = true", "bot_mitigaton = true", "E_PROPERTY"),
    ("price: decimal", "price: money", "E_FIELD_TYPE"),
    ("role Customer", "role Customer role Customer", "E_DUPLICATE_NAME"),
    ("instances = 2..10", "instances = 2 instances = 3", "E_DUPLICATE_PROPERTY"),
])
def test_static_errors(old, new, code):
    assert code in codes(SHOP.replace(old, new, 1))


def test_invalid_loading_example():
    assert "E_LOAD_COUNT" in codes((ROOT / "examples/invalid-loading.polishd").read_text())


def test_syntax_has_location():
    result = compile_source("architecture Shop {\n service }")
    assert result.diagnostics[0].code == "E_SYNTAX"
    assert result.diagnostics[0].line == 2


def test_tls_failure_is_not_success():
    arch = compiled(SHOP.replace("tls_termination = true", "tls = false"))
    result = simulate(arch, arch.scenarios[0])
    assert not result.passed
    assert result.error == "TLS_UNSUPPORTED"


def test_upstream_https_requires_support_at_next_hop():
    arch = compiled(SHOP.replace("tls_termination = true", "tls_termination = true upstream_protocol = https"))
    result = simulate(arch, arch.scenarios[0])
    assert result.error == "TLS_UNSUPPORTED"
    assert "ApiGateway" in result.trace[-1]


def test_cannot_downgrade_without_termination():
    arch = compiled(SHOP.replace("tls_termination = true", "tls = true upstream_protocol = http"))
    assert simulate(arch, arch.scenarios[0]).error == "TLS_TERMINATION_REQUIRED"


def test_routing_cycle():
    arch = compiled(SHOP.replace("routes_to AppPool", "routes_to ApiGateway"))
    assert simulate(arch, arch.scenarios[0]).error == "REQUEST_CYCLE"


def test_service_cycle():
    arch = compiled(SHOP.replace("reads Products.Items", "reads Products.Items invokes Orders.place_order"))
    assert simulate(arch, arch.scenarios[1]).error == "REQUEST_CYCLE"


def test_role_does_not_imply_authentication():
    arch = compiled(SHOP.replace("authenticated = true", "authenticated = false"))
    assert simulate(arch, arch.scenarios[5]).outcome == "denied"


def test_no_endpoint():
    arch = compiled(SHOP.replace('path = "/api/products/123"', 'path = "/api/unknown"'))
    assert simulate(arch, arch.scenarios[0]).error == "NO_ENDPOINT"


def test_assertion_failure():
    arch = compiled(SHOP.replace("accesses Products.Items", "accesses Products.Purchases"))
    result = simulate(arch, arch.scenarios[0])
    assert result.outcome == "success"
    assert not result.passed


def test_ambiguous_routes():
    arch = compiled(SHOP.replace('route "/api/*" to ApiGateway', 'route "/api/*" to ApiGateway route "/api/?*" to AppBalancer'))
    assert simulate(arch, arch.scenarios[0]).error == "AMBIGUOUS_ROUTE"


def test_server_rendering():
    arch = compiled('''architecture SSR {
      frontend Site { rendering = server loaded_from LB page Home }
      load_balancer LB { tls_termination = true routes_to Pool }
      compute_pool Pool { instances = 1..2 }
      service ReactServer { hosted_on Pool }
    }
    scenario "Render" {
      request { entry = Site.Home protocol = https }
      expect { outcome = success reaches ReactServer }
    }''')
    assert simulate(arch, arch.scenarios[0]).passed


def test_json_cli(capsys):
    assert main(["simulate", str(ROOT / "examples/shop.polishd"), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] and len(payload["results"]) == 9


def test_cli_errors(capsys):
    assert main(["check", str(ROOT / "examples/invalid-loading.polishd"), "--json"]) == 1
    assert not json.loads(capsys.readouterr().out)["ok"]
    assert main(["check", "no-such-file.polishd", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["diagnostics"][0]["code"] == "E_FILE"


def test_missing_scenario(capsys):
    assert main(["simulate", str(ROOT / "examples/shop.polishd"), "--scenario", "missing", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["diagnostics"][0]["code"] == "E_NO_SCENARIOS"


def test_frontend_action_requires_declared_invocation():
    arch = compiled(SHOP.replace("Product invokes Catalog.get_product", "Cart invokes Catalog.get_product"))
    result = simulate(arch, arch.scenarios[-1])
    assert result.error == "INVOCATION_NOT_ALLOWED"


def test_frontend_action_must_reach_target():
    arch = compiled(SHOP.replace(
        'route "/api/*" to ApiGateway', 'route "/api/*" to WebAssets'))
    assert simulate(arch, arch.scenarios[-1]).error == "ACTION_NOT_REACHED"


def test_primary_key_is_preserved():
    table = compiled().nodes["Products.Items"]
    assert table.fields["id"].primary_key
    assert not table.fields["price"].primary_key


def test_keyword_prefix_is_valid_identifier():
    arch = compiled(SHOP.replace("price: decimal", "document_price: decimal"))
    assert "document_price" in arch.nodes["Products.Items"].fields


def test_navigation_can_use_an_alternative_authorized_path():
    source = SHOP.replace("Landing navigates_to Product", "Landing navigates_to AdminPanel Landing navigates_to Product")
    source = source.replace("Landing navigates_to AdminPanel\n", "AdminPanel navigates_to Checkout\n")
    arch = compiled(source)
    assert simulate(arch, arch.scenarios[3]).passed


@pytest.mark.parametrize("replacement", ["true", "-1", "2.5", "unknown"])
def test_invalid_scale_types(replacement):
    assert "E_SCALE" in codes(SHOP.replace("instances = 2..10", f"instances = {replacement}"))
