import json
import shutil
from pathlib import Path

import pytest

from polish import compile_source
from polish.cli import main
from polish import simulate


@pytest.fixture
def config_dir(tmp_path):
    source = Path(__file__).resolve().parents[1] / "polish/config"
    target = tmp_path / "config"
    shutil.copytree(source, target)
    return target


def edit(directory, filename, change):
    path = directory / filename
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


def test_database_kinds_and_children_are_configurable(config_dir):
    source = '''architecture A {
      database Records { kind = custom_relational hosted_on Disk table Items }
      database_cluster Disk
    }'''
    assert not compile_source(source, config_dir=config_dir).ok
    edit(config_dir, "databases.json", lambda d: d["database_kinds"].update(custom_relational=["table"]))
    assert compile_source(source, config_dir=config_dir).ok
    edit(config_dir, "databases.json", lambda d: d["database_kinds"].update(custom_relational=[]))
    assert "E_DATA_MODEL" in {d.code for d in compile_source(source, config_dir=config_dir).diagnostics}


def test_service_property_can_be_enabled_without_code(config_dir):
    source = "architecture A { service API { owner = platform hosted_on Pool } compute_pool Pool }"
    assert not compile_source(source, config_dir=config_dir).ok
    edit(config_dir, "services.json", lambda d: d["components"]["service"]["properties"].append("owner"))
    assert compile_source(source, config_dir=config_dir).ok
    assert not compile_source(source).ok  # A custom directory never mutates bundled defaults.


def test_allowed_relationships_are_configurable(config_dir):
    source = "architecture A { service API { hosted_on Pool } compute_pool Pool }"
    assert compile_source(source, config_dir=config_dir).ok
    edit(config_dir, "relationships.json", lambda d: d["relationships"]["hosted_on"]["sources"].remove("service"))
    assert "E_RELATION_TYPE" in {d.code for d in compile_source(source, config_dir=config_dir).diagnostics}


@pytest.mark.parametrize("content", ["{broken", "[]", '{"components": {"service": {}}}'])
def test_bad_config_is_a_diagnostic(config_dir, content):
    (config_dir / "services.json").write_text(content)
    result = compile_source("architecture A {}", config_dir=config_dir)
    assert result.diagnostics[0].code == "E_CONFIG"


def test_missing_file_is_a_diagnostic(config_dir):
    (config_dir / "databases.json").unlink()
    assert compile_source("architecture A {}", config_dir=config_dir).diagnostics[0].code == "E_CONFIG"


def test_cli_config_directory(config_dir, tmp_path, capsys):
    source = tmp_path / "test.polishd"
    source.write_text("architecture A {}")
    assert main(["check", str(source), "--config-dir", str(config_dir), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"]


def test_custom_compiler_error(config_dir):
    edit(config_dir, "errors.json", lambda d: d["errors"]["E_PROPERTY_TYPE"].update(
        code="INVALID_BOOLEAN", message="Use true or false for {key}."))
    result = compile_source("architecture A { compute_pool Pool { tls = True } }", config_dir=config_dir)
    assert result.diagnostics[0].code == "INVALID_BOOLEAN"
    assert result.diagnostics[0].message == "Use true or false for tls."
    assert result.diagnostics[0].line == 1


def test_simulation_uses_compiled_catalog_snapshot(config_dir):
    edit(config_dir, "errors.json", lambda d: d["errors"]["TLS_UNSUPPORTED"].update(
        code="ENCRYPTION_REQUIRED", message="Enable TLS on {node_name}."))
    result = compile_source('''architecture A { compute_pool Pool }
      scenario "TLS failure" {
        request { entry = Pool protocol = https }
        expect { outcome = error error = ENCRYPTION_REQUIRED }
      }''', config_dir=config_dir)
    assert result.ok
    (config_dir / "errors.json").unlink()
    simulation = simulate(result.architecture, result.architecture.scenarios[0])
    assert simulation.passed
    assert simulation.trace[-1] == "ENCRYPTION_REQUIRED: Enable TLS on Pool."


@pytest.mark.parametrize("change", [
    lambda d: d["errors"].pop("TLS_UNSUPPORTED"),
    lambda d: d["errors"]["TLS_UNSUPPORTED"].update(message="{unknown}"),
    lambda d: d["errors"]["TLS_UNSUPPORTED"].update(message="{node_name.__class__}"),
    lambda d: d["errors"]["TLS_UNSUPPORTED"].update(outcome="success"),
    lambda d: d["errors"]["TLS_UNSUPPORTED"].update(parameters=[]),
    lambda d: d["errors"]["TLS_UNSUPPORTED"].update(phase="compile"),
])
def test_invalid_error_definitions_fail_at_load(config_dir, change):
    edit(config_dir, "errors.json", change)
    assert compile_source("architecture A {}", config_dir=config_dir).diagnostics[0].code == "E_CONFIG"


def test_cli_errors_use_catalog(config_dir, tmp_path, capsys):
    edit(config_dir, "errors.json", lambda d: d["errors"]["E_FILE"].update(code="INPUT_UNREADABLE"))
    assert main(["check", str(tmp_path / "absent.polishd"), "--config-dir", str(config_dir), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["diagnostics"][0]["code"] == "INPUT_UNREADABLE"
