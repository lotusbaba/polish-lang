"""Vendor catalogs and explicit AWS product contracts."""
import json
import math
import re


def product_profile(arch, node):
    return arch.cloud.get(node.properties.get("provider"), {}).get("products", {}).get(node.properties.get("product"), {})


def load_vendors(root, manifest):
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
                for field in ("required", "properties"):
                    if not isinstance(product[field], list) or not all(isinstance(s, str) for s in product[field]):
                        raise ValueError(f"Invalid {field} for {name}")
                for field in ("protocols", "forbidden", "database_forbidden", "database_kinds"):
                    if field in product and (not isinstance(product[field], list) or not all(isinstance(s, str) for s in product[field])):
                        raise ValueError(f"Invalid {field} for {name}")
                types = product.get("property_types", {})
                if not isinstance(types, dict) or any(value not in {"positive", "nonnegative", "positive_integer", "nonnegative_integer"} for value in types.values()):
                    raise ValueError(f"Invalid property_types for {name}")
                if "request_protocol" in product and product["request_protocol"] not in {"https", "tls"}:
                    raise ValueError(f"Invalid request_protocol for {name}")
                enums = product.get("enums", {})
                if not isinstance(enums, dict) or any(not isinstance(values, list) or not all(isinstance(v, str) for v in values) for values in enums.values()):
                    raise ValueError(f"Invalid enums for {name}")
                for field in ("managed", "host_required", "path_routing", "terminate_encrypted"):
                    if field in product and type(product[field]) is not bool:
                        raise ValueError(f"{field} must be boolean for {name}")
                for field in ("read_chunk_bytes", "write_chunk_bytes", "max_item_bytes"):
                    if field in product and (type(product[field]) is not int or product[field] < 1):
                        raise ValueError(f"Invalid {field} for {name}")
                if "eventual_read_factor" in product and (type(product["eventual_read_factor"]) not in (int, float) or not 0 < product["eventual_read_factor"] <= 1):
                    raise ValueError("Invalid eventual_read_factor")
                catalog["products"][name] = product
        catalogs[vendor] = catalog
    return catalogs


def prepare_products(arch, error, config):
    products = arch.aws["products"]
    for node in arch.nodes.values():
        p = node.properties
        name = p.get("product")
        if name is None:
            if node.kind in {"queue", "stream", "batch_cluster", "topic"}:
                error("E_VENDOR", line=node.line, detail=f"{node.kind} requires a product profile")
            profile = arch.aws["instances"].get(p.get("instance_type"))
            if profile and profile["family"] != "ec2":
                error("E_VENDOR", line=node.line, detail="RDS instance classes require product = rds_postgres")
            continue
        product = arch.cloud.get(p.get("provider"), {}).get("products", {}).get(name)
        if not product or node.kind != product["component"]:
            error("E_VENDOR", line=node.line, detail=f"Unsupported product/provider/component combination on {node.name}")
            continue
        if name == "s3" and p.get("endpoint") == "website":
            p.setdefault("tls", False)
            if p.get("tls") is not False:
                error("E_VENDOR", line=node.line, detail="S3 website endpoints do not support TLS")
        # Product properties are accepted only for their matching profile.
        for key, value in product["defaults"].items():
            if key in {"engine", "storage"} and key in p and p[key] != value:
                error("E_VENDOR", line=node.line, detail=f"{name} requires {key} = {value}")
            p.setdefault(key, value)
        for key in product["required"]:
            if key not in p:
                error("E_VENDOR", line=node.line, detail=f"{node.name} requires {key}")
        from .scaling import number
        for key, kind in product.get("property_types", {}).items():
            if key in p and (not number(p[key], kind.startswith("positive")) or kind.endswith("integer") and type(p[key]) is not int):
                error("E_VENDOR", line=node.line, detail=f"{node.name}.{key} must be {kind}")
        for key, values in product.get("enums", {}).items():
            if key in p and p[key] not in values:
                error("E_VENDOR", line=node.line, detail=f"Invalid {key} on {node.name}")
        if product.get("host_required") and len(arch.outgoing(node.name, "hosted_on")) != 1:
            error("E_VENDOR", line=node.line, detail=f"{node.name} requires a compute host")
        if product.get("managed") and arch.outgoing(node.name, "hosted_on"):
            error("E_VENDOR", line=node.line, detail=f"{node.name} is managed; omit hosted_on")
        if p.get("rate_limiting") is False and any(k in p for k in ("limit_rps", "burst_limit")):
            error("E_VENDOR", line=node.line, detail="Rate limit settings conflict with rate_limiting = false")
        if name == "msk" and type(p.get("replication_factor")) is int and type(p.get("brokers")) is int and p["replication_factor"] > p["brokers"]:
            error("E_VENDOR", line=node.line, detail="MSK replication_factor cannot exceed brokers")
        if name == "emr" and number(p.get("cpu_utilization", 0.7)) and p.get("cpu_utilization", 0.7) > 1:
            error("E_VENDOR", line=node.line, detail="EMR cpu_utilization must not exceed 1")
        for key in product.get("forbidden", []):
            if key in p:
                error("E_VENDOR", line=node.line, detail=f"{name} manages {key}; do not configure it")
        if "instance_family" in product:
            profile = arch.aws["instances"].get(p.get("instance_type"))
            if not profile or profile["family"] != product["instance_family"]:
                error("E_VENDOR", line=node.line, detail=f"{name} requires an {product['instance_family']} instance profile")
        if "multi_az" in p and type(p["multi_az"]) is not bool:
            error("E_VENDOR", line=node.line, detail="multi_az must be boolean")
        if product.get("path_routing") is False and any(e.pattern is not None for e in arch.outgoing(node.name, "routes_to")):
            error("E_VENDOR", line=node.line, detail="NLB cannot inspect HTTP paths; use routes_to")
        if product.get("terminate_encrypted") and p.get("tls") and not p.get("tls_termination"):
            error("E_VENDOR", line=node.line, detail="ALB HTTPS requires tls_termination = true")
        if name == "dynamodb":
            mode = p.get("capacity_mode")
            if mode not in {"provisioned", "on_demand"}:
                error("E_VENDOR", line=node.line, detail="DynamoDB capacity_mode must be provisioned or on_demand")
            for key in ("read_capacity_units", "write_capacity_units", "max_read_units", "max_write_units"):
                if key in p and (type(p[key]) is not int or p[key] < 1):
                    error("E_VENDOR", line=node.line, detail=f"{key} must be a positive integer")
                if key in p and ((mode == "provisioned") != (key in {"read_capacity_units", "write_capacity_units"})):
                    error("E_VENDOR", line=node.line, detail=f"{key} conflicts with {mode} capacity")
            if mode == "provisioned" and not {"read_capacity_units", "write_capacity_units"} <= p.keys():
                error("E_VENDOR", line=node.line, detail="Provisioned DynamoDB requires read_capacity_units and write_capacity_units")

    from .cloud_products import validate_cloud_products
    validate_cloud_products(arch, error)

    for node in arch.nodes.values():
        images = arch.outgoing(node.name, "image_from")
        if len(images) > 1:
            error("E_VENDOR", line=node.line, detail="A service can have only one image source")
    for edge in arch.edges:
        target = arch.nodes[edge.target]
        registry = target.properties.get("kind") == "container_registry"
        if edge.kind == "image_from":
            if not registry:
                error("E_VENDOR", line=edge.line, detail="image_from requires a container registry")
            if "authorized" in edge.properties and type(edge.properties["authorized"]) is not bool:
                error("E_VENDOR", line=edge.line, detail="image_from authorized must be boolean")
        elif registry and edge.kind in {"loaded_from", "routes_to"}:
            error("E_VENDOR", line=edge.line, detail="Container registries cannot serve frontend assets")

    for node in arch.nodes.values():
        if node.kind != "database":
            continue
        hosts = arch.outgoing(node.name, "hosted_on")
        if len(hosts) != 1:
            continue
        host = arch.nodes[hosts[0].target]
        product = product_profile(arch, host)
        if not product:
            continue
        if node.properties.get("kind") not in product.get("database_kinds", []):
            error("E_VENDOR", line=node.line, detail=f"{node.name} has an incompatible database kind")
        if product.get("capacity_model") == "operations":
            node.properties.setdefault("tls", host.properties.get("tls", False))
            node.properties.setdefault("durability", "disk")
        for key in product.get("database_forbidden", []):
            if key in node.properties:
                error("E_VENDOR", line=node.line, detail=f"Managed DynamoDB does not expose {key}")
        if host.properties.get("product") == "rds_postgres":
            node.properties.setdefault("tls", True)
            node.properties.setdefault("durability", "disk")
            if type(node.properties.get("read_replicas", 0)) is int and node.properties.get("read_replicas", 0) > 0:
                node.properties.setdefault("replication", "asynchronous")
                if node.properties["replication"] != "asynchronous":
                    error("E_VENDOR", line=node.line, detail="RDS read replicas use asynchronous replication; Multi-AZ standby is modeled separately")
        if host.properties.get("product") == "dynamodb":
            node.properties.setdefault("tls", True)
            node.properties.setdefault("durability", "disk")
            if "partitions" in node.properties and node.properties["partitions"] != "managed":
                error("E_VENDOR", line=node.line, detail="DynamoDB partitions must be managed")
            if len([e for e in arch.edges if e.kind == "hosted_on" and e.target == host.name]) != 1:
                error("E_VENDOR", line=node.line, detail="Use one DynamoDB product host per logical database/table capacity budget")

    for edge in arch.edges:
        if edge.kind not in {"reads", "writes"}:
            continue
        target = arch.nodes[edge.target]
        if target.kind == "cache":
            if set(edge.properties) - {"via", "protocol", "path", "pool_size", "pool_scope", "pool_distribution", "on_miss", "on_error"}:
                error("E_VENDOR", line=edge.line, detail="Unsupported cache edge option; use transport, client pool, or miss/error policy settings")
            continue
        db = target if target.kind == "database" else arch.nodes.get(target.parent)
        hosts = arch.outgoing(db.name, "hosted_on") if db else []
        dynamo = bool(hosts and arch.nodes[hosts[0].target].properties.get("product") == "dynamodb")
        if dynamo:
            edge.properties.setdefault("protocol", "https")
            if any(k in edge.properties for k in ("read_from", "cpu_ms")):
                error("E_VENDOR", line=edge.line, detail="DynamoDB uses item_bytes and managed reads, not cpu_ms or read_from")
            size = edge.properties.get("item_bytes")
            if size is not None and (type(size) is not int or not 1 <= size <= products["dynamodb"]["max_item_bytes"]):
                error("E_VENDOR", line=edge.line, detail="DynamoDB item_bytes must be between 1 and the configured item-size limit")
        elif "item_bytes" in edge.properties:
            error("E_VENDOR", line=edge.line, detail="item_bytes capacity modeling requires DynamoDB")


class DynamoCapacity:
    def __init__(self, arch, request, result, fail):
        self.arch, self.request, self.result, self.fail = arch, request, result, fail
        self.demand = {}

    def charge(self, database, edge, host):
        if "rate_rps" not in self.request:
            return
        size = edge.properties.get("item_bytes")
        if size is None:
            self.fail("SCALING_MODEL_INCOMPLETE", detail="DynamoDB calls require item_bytes for capacity simulation")
        product = self.arch.aws["products"]["dynamodb"]
        read = edge.kind == "reads"
        units = math.ceil(size / product["read_chunk_bytes" if read else "write_chunk_bytes"])
        if read and edge.properties.get("consistency", "eventual") == "eventual":
            units *= product["eventual_read_factor"]
        key = (database.name, edge.kind)
        record = self.demand.setdefault(key, {"resource": database.name + ":" + edge.kind, "kind": "dynamodb", "demand_units_per_second": 0})
        capacity_key = ("read_capacity_units" if read else "write_capacity_units") if host.properties["capacity_mode"] == "provisioned" else ("max_read_units" if read else "max_write_units")
        if capacity_key not in host.properties:
            self.fail("SCALING_MODEL_INCOMPLETE", detail=f"DynamoDB on-demand simulation requires an explicit {capacity_key}; it is not unlimited")
        record["capacity_units_per_second"] = host.properties[capacity_key]
        record["demand_units_per_second"] += units * self.request["rate_rps"]

    def evaluate(self):
        failures = []
        for record in self.demand.values():
            demand, capacity = record["demand_units_per_second"], record["capacity_units_per_second"]
            record["overloaded"] = demand > capacity
            self.result.budgets.append(record)
            self.result.trace.append(f"DynamoDB {record['resource']}: demand={demand:g} units/s; capacity={capacity:g}")
            if demand > capacity:
                failures.append(record)
        if failures:
            record = failures[0]
            self.fail("DYNAMODB_THROTTLED", resource=record["resource"], demand=record["demand_units_per_second"], capacity=record["capacity_units_per_second"])
