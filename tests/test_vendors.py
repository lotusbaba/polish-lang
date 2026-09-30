import json
from pathlib import Path
import shutil

import pytest

from polish import compile_source, simulate
from polish.configuration import load_config

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples/aws-products.polishd").read_text()


def run(source=SOURCE, index=0):
    result = compile_source(source)
    assert result.ok, result.diagnostics
    return simulate(result.architecture, result.architecture.scenarios[index])


def test_examples():
    for index in range(4):
        assert run(index=index).passed
    assert run(index=2).budgets[-1]["demand_units_per_second"] == 60
    assert run(index=3).capacity[-1]["instance_type"] == "db.m7i.large"
    assert run(index=3).capacity[-1]["instances"] == 1  # Standby is not another writer.


def test_eventual_read_factor():
    source = SOURCE.replace("consistency = strong", "consistency = eventual")
    assert run(source, 1).outcome == "success"
    assert run(source, 1).budgets[-1]["demand_units_per_second"] == 50.5


def test_on_demand_requires_explicit_bound():
    source = SOURCE.replace("capacity_mode = provisioned", "capacity_mode = on_demand")
    source = source.replace("read_capacity_units = 100", "max_read_units = 100").replace("write_capacity_units = 50", "max_write_units = 50")
    assert run(source).passed
    assert run(source, 1).error == "DYNAMODB_THROTTLED"
    assert run(source.replace("max_read_units = 100", "")).error == "SCALING_MODEL_INCOMPLETE"


@pytest.mark.parametrize("old,new", [
    ("product = alb", "product = unknown"),
    ("product = alb", "product = rds_postgres"),
    ('instance_type = "db.m7i.large"', 'instance_type = "m7i.large"'),
    ('instance_type = "m7i.large"', 'instance_type = "db.m7i.large"'),
    ("multi_az = true", "multi_az = 2"),
    ("product = dynamodb", 'product = dynamodb instance_type = "m7i.large"'),
    ("partitions = managed", "partitions = 4"),
    ("capacity_mode = provisioned", "capacity_mode = unlimited"),
    ("read_capacity_units = 100", "read_capacity_units = false"),
    ("write_capacity_units = 50", ""),
    ("item_bytes = 4096", "item_bytes = 409601"),
    ("item_bytes = 4096", "item_bytes = 4096 read_from = replica"),
    ("routes_to Rds", 'route "/database" to Rds'),
    ("tls_termination = true", "tls = true"),
])
def test_product_constraints(old, new):
    result = compile_source(SOURCE.replace(old, new, 1))
    assert "E_VENDOR" in {d.code for d in result.diagnostics}


def test_alb_cannot_carry_database_tls():
    source = SOURCE.replace("product = nlb", "product = alb tls_termination = true")
    assert run(source, 3).error == "AWS_PROTOCOL_UNSUPPORTED"


def test_dynamo_requires_https():
    source = SOURCE.replace("item_bytes = 4096", "item_bytes = 4096 protocol = tcp")
    assert "E_CONNECTION" in {d.code for d in compile_source(source).diagnostics} or "E_CONNECTION_3" in {d.code for d in compile_source(source).diagnostics}


def test_catalog_folder_and_custom_instance(tmp_path):
    config = tmp_path / "config"
    shutil.copytree(ROOT / "polish/config", config)
    path = config / "vendors/aws/ec2.json"
    data = json.loads(path.read_text())
    data["instances"]["test.instance"] = {"vcpus": 16, "memory_gib": 64}
    path.write_text(json.dumps(data))
    assert "test.instance" in load_config(config)["aws"]["instances"]
    path.unlink()
    assert compile_source("architecture A {}", config_dir=config).diagnostics[0].code == "E_CONFIG"


def test_rds_standby_does_not_create_read_replica():
    source = SOURCE.replace("writes Orders.Purchases", "reads Orders.Purchases").replace("protocol = tls cpu_ms = 2", "protocol = tls cpu_ms = 2 read_from = replica")
    assert run(source, 3).error == "NO_READ_REPLICA"
