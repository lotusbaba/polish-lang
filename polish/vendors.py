"""Adapters for configured vendors rules."""
from .rule_engine import ConfiguredModel, run
import json
import re

def product_profile(arch, node):
    return arch.cloud.get(node.properties.get("provider"), {}).get("products", {}).get(node.properties.get("product"), {})

def load_vendors(root, manifest, schema):
    expected = {'list_fields', 'required_lists', 'boolean_fields', 'numeric_fields', 'enums', 'property_types'}
    if not isinstance(schema, dict) or set(schema) != expected:
        raise ValueError('Invalid vendor metadata schema')
    for field in ('list_fields', 'required_lists', 'boolean_fields', 'property_types'):
        if not isinstance(schema[field], list) or not all(isinstance(v, str) and v.isidentifier() for v in schema[field]):
            raise ValueError(f'Invalid vendor schema {field}')
    if not isinstance(schema['numeric_fields'], dict) or any(v not in {'positive_integer', 'fraction'} for v in schema['numeric_fields'].values()):
        raise ValueError('Invalid vendor numeric constraints')
    if not isinstance(schema['enums'], dict) or any(not isinstance(v, list) or not all(isinstance(x, str) for x in v) for v in schema['enums'].values()):
        raise ValueError('Invalid vendor enum constraints')
    catalogs = {}
    for vendor, filenames in manifest.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", vendor) or not isinstance(filenames, list):
            raise ValueError("Invalid vendor manifest")
        catalog = {"provider": vendor, "instances": {}, "products": {}}
        for filename in filenames:
            if not isinstance(filename, str) or not re.fullmatch(r"[a-z0-9_]+\.json", filename):
                raise ValueError("Vendor files must be JSON filenames without path segments")
            data = json.loads(root.joinpath("vendors", vendor, filename).read_text())
            for name, profile in data.get("instances", {}).items():
                if name in catalog["instances"]:
                    raise ValueError(f"Duplicate instance profile {name}")
                catalog["instances"][name] = {**profile, "family": data["instance_family"]}
            for name, product in data.get("products", {}).items():
                if name in catalog["products"] or not isinstance(product, dict):
                    raise ValueError(f"Invalid or duplicate product {name}")
                if not isinstance(product["component"], str) or not isinstance(product["defaults"], dict):
                    raise ValueError(f"Invalid product schema {name}")
                for field in schema['required_lists']:
                    if field not in product:
                        raise ValueError(f"Missing {field} for {name}")
                for field in schema['list_fields']:
                    if field in product and (not isinstance(product[field], list) or not all(isinstance(v, str) for v in product[field])):
                        raise ValueError(f"Invalid {field} for {name}")
                types = product.get("property_types", {})
                if not isinstance(types, dict) or any(value not in schema['property_types'] for value in types.values()):
                    raise ValueError(f"Invalid property_types for {name}")
                for field, allowed in schema['enums'].items():
                    if field in product and product[field] not in allowed:
                        raise ValueError(f"Invalid {field} for {name}")
                enums = product.get("enums", {})
                if not isinstance(enums, dict) or any(not isinstance(values, list) or not all(isinstance(v, str) for v in values) for values in enums.values()):
                    raise ValueError(f"Invalid enums for {name}")
                for field in schema['boolean_fields']:
                    if field in product and type(product[field]) is not bool:
                        raise ValueError(f"{field} must be boolean for {name}")
                from .scaling import number
                for field, constraint in schema['numeric_fields'].items():
                    if field not in product:
                        continue
                    value = product[field]
                    valid = number(value, True)
                    if constraint == 'positive_integer':
                        valid = valid and type(value) is int
                    elif constraint == 'fraction':
                        valid = valid and value <= 1
                    else:
                        raise ValueError(f"Unknown vendor numeric constraint {constraint}")
                    if not valid:
                        raise ValueError(f"Invalid {field} for {name}")
                catalog["products"][name] = product
        catalogs[vendor] = catalog
    return catalogs


def prepare_products(arch, error, config):
    return run(arch, "vendors", "prepare_products", arch, error, config)


class DynamoCapacity(ConfiguredModel):
    rule_module = "vendors"
