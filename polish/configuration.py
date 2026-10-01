"""Load declarative language definitions; never execute configuration text."""
import json
import re
from importlib.resources import files
from pathlib import Path
from .errors import validate_errors


class ConfigurationError(ValueError):
    pass


def load_config(directory=None):
    root = Path(directory) if directory is not None else files("polish").joinpath("config")
    result = {"components": {}}
    names = ("databases", "services", "hosts", "frontend", "network", "language", "relationships", "errors", "vendors", "diagnostics", "model_inputs", "rules", "vendor_schema")
    try:
        for name in names:
            data = json.loads(root.joinpath(name + ".json").read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError(f"{name}.json must contain an object")
            for key, val in data.items():
                if key == "components":
                    for kind, definition in val.items():
                        if kind in result[key]:
                            raise ValueError(f"Duplicate component {kind}")
                        result[key][kind] = definition
                elif key in result:
                    raise ValueError(f"Duplicate configuration section {key}")
                else:
                    result[key] = val
        validate_errors(result["errors"])
        baseline = json.loads(files("polish").joinpath("config/errors.json").read_text())["errors"]
        for rule, definition in baseline.items():
            if rule not in result["errors"]:
                raise ValueError(f"Missing error definition {rule}")
            configured = result["errors"][rule]
            if configured["parameters"] != definition["parameters"] or configured["phase"] != definition["phase"]:
                raise ValueError(f"Error definition {rule} changes its parameter or phase contract")
        from .vendors import load_vendors
        result["cloud"] = load_vendors(root, result["vendors"], result["vendor_schema"])
        from .diagnostics import validate_diagnostics
        detail_baseline = json.loads(files("polish").joinpath("config/diagnostics.json").read_text())["diagnostic_details"]
        validate_diagnostics(result, detail_baseline)
        from .model_inputs import validate_model_inputs
        input_baseline = json.loads(files("polish").joinpath("config/model_inputs.json").read_text())["model_inputs"]
        validate_model_inputs(result, input_baseline)
        from .rule_engine import load_rules
        result["rules"] = load_rules(root, result["rules"])
        result["aws"] = result["cloud"]["aws"]
        aws = result["aws"]
        if aws["provider"] != "aws" or not isinstance(aws["instances"], dict):
            raise ValueError("Invalid AWS instance catalog")
        for profile in aws["instances"].values():
            for key in ("vcpus", "memory_gib"):
                if type(profile[key]) is not int or profile[key] <= 0:
                    raise ValueError("AWS vcpus and memory_gib must be positive integers")
        components = result["components"]
        def strings(value):
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError("Expected a list of strings")
        for kind, definition in components.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", kind):
                raise ValueError(f"Invalid component keyword {kind!r}")
            for key in ("properties", "parents"):
                strings(definition[key])
            if set(definition["parents"]) - components.keys():
                raise ValueError(f"Unknown parent of {kind}")
        for key in ("network", "booleans", "methods", "field_types", "nonnegative_integers", "positive_integers", "artifact_kinds", "connection_entries", "request_properties"):
            strings(result[key])
        for key in ("database_kinds", "storage_enums", "enums", "loading", "connection_options", "connection_protocols", "connection_enums"):
            if not isinstance(result[key], dict):
                raise ValueError(f"{key} must be an object")
            for values in result[key].values():
                strings(values)
        for key in ("hosting", "relationships"):
            if not isinstance(result[key], dict):
                raise ValueError(f"{key} must be an object")
        for source, target in result["hosting"].items():
            if source not in components or target not in components:
                raise ValueError("Unknown hosting component")
        for name, rule in result["relationships"].items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name):
                raise ValueError(f"Invalid relationship keyword {name!r}")
            for key in ("sources", "targets"):
                strings(rule[key])
                if set(rule[key]) - components.keys():
                    raise ValueError(f"Unknown component in {name}")
        return result
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ConfigurationError(f"Invalid language config in {root}: {exc}") from exc
