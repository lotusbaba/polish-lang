"""Steady-state CPU demand, with explicitly declared workload costs."""
import math


def number(value, positive=False):
    try:
        return type(value) in (int, float) and math.isfinite(value) and (value > 0 if positive else value >= 0)
    except OverflowError:
        return False


def hardware(arch, host):
    if host.properties.get("provider") == "self_hosted":
        return {k: host.properties.get(k) for k in ("vcpus", "memory_gib")}
    return arch.aws["instances"].get(host.properties.get("instance_type"), {})


def validate_scaling(arch, error):
    for node in arch.nodes.values():
        p = node.properties
        from .vendors import product_profile
        profile = product_profile(arch, node)
        if p.get("provider") == "self_hosted" and node.kind == "compute_pool":
            for key in ("vcpus", "memory_gib"):
                if not number(p.get(key), True):
                    error("E_SCALING", line=node.line, detail=f"Self-hosted compute requires positive {key}")
            if "instance_type" in p:
                error("E_SCALING", line=node.line, detail="Self-hosted compute uses vcpus and memory_gib, not AWS instance_type")
            if not number(p.get("cpu_utilization", 0.7), True) or p.get("cpu_utilization", 0.7) > 1:
                error("E_SCALING", line=node.line, detail="cpu_utilization must be in (0, 1]")
        for key in ("base_memory_mib", "memory_per_request_mib", "duration_ms"):
            if key in p and not number(p[key]):
                error("E_SCALING", line=node.line, detail=f"{node.name}.{key} must be finite and nonnegative")
        for key in ("connection_pool_size", "max_connections", "reserved_connections"):
            if key in p and (type(p[key]) is not int or p[key] < (0 if key == "reserved_connections" else 1)):
                error("E_SCALING", line=node.line, detail=f"Invalid {key} on {node.name}")
        if "reserved_connections" in p and ("max_connections" not in p or
                type(p["reserved_connections"]) is int and type(p.get("max_connections")) is int and p["reserved_connections"] >= p["max_connections"]):
            error("E_SCALING", line=node.line, detail="reserved_connections must be less than max_connections")
        if "memory_utilization" in p and (not number(p["memory_utilization"], True) or p["memory_utilization"] > 1):
            error("E_SCALING", line=node.line, detail="memory_utilization must be in (0, 1]")
        if "cpu_ms" in p and not number(p["cpu_ms"]):
            error("E_SCALING", line=node.line, detail=f"{node.name}.cpu_ms must be finite and nonnegative")
        if node.kind in {"compute_pool", "database_cluster"} and p.get("provider") != "self_hosted" and not profile.get("capacity_model") and p.get("product") != "dynamodb" and any(k in p for k in ("provider", "instance_type", "cpu_utilization")):
            if p.get("provider") != "aws" or p.get("instance_type") not in arch.aws["instances"]:
                error("E_SCALING", line=node.line, detail=f"{node.name} needs provider = aws and a supported instance_type")
            utilization = p.get("cpu_utilization", 0.7)
            if not number(utilization, True) or utilization > 1:
                error("E_SCALING", line=node.line, detail="cpu_utilization must be in (0, 1]")
    for edge in arch.edges:
        if "job_cpu_ms" in edge.properties and not number(edge.properties["job_cpu_ms"], True):
            error("E_SCALING", line=edge.line, detail="job_cpu_ms must be finite and positive")
        if "cpu_ms" in edge.properties and not number(edge.properties["cpu_ms"]):
            error("E_SCALING", line=edge.line, detail="Connection cpu_ms must be finite and nonnegative")
    for scenario in arch.scenarios:
        p = scenario.request
        if p.get("cache_result", "miss") not in {"hit", "miss", "error"}:
            error("E_SCALING", line=scenario.line, detail="cache_result must be hit, miss, or error")
        for key in ("burst_requests", "query_depth", "query_cost", "window_seconds"):
            if key in p and not number(p[key], key == "window_seconds"):
                error("E_SCALING", line=scenario.line, detail=f"Invalid {key}")
        if "rate_rps" in p and not number(p["rate_rps"], True):
            error("E_SCALING", line=scenario.line, detail="rate_rps must be finite and positive")
        if "image_pulls_rps" in p and not number(p["image_pulls_rps"], True):
            error("E_SCALING", line=scenario.line, detail="image_pulls_rps must be finite and positive")
        if p.get("scale", "minimum") not in {"minimum", "maximum"} or ("scale" in p and "rate_rps" not in p):
            error("E_SCALING", line=scenario.line, detail="scale requires rate_rps and must be minimum or maximum")


class CapacityModel:
    def __init__(self, arch, request, result, fail):
        self.arch, self.request, self.result, self.fail = arch, request, result, fail
        self.resources = {}

    def charge(self, workload, cost, role=None):
        if "rate_rps" not in self.request:
            return
        host = self.arch.nodes[self.arch.outgoing(workload.name, "hosted_on")[0].target]
        p = host.properties
        if cost is None or not hardware(self.arch, host).get("vcpus"):
            self.fail("SCALING_MODEL_INCOMPLETE", detail=f"{workload.name} needs cpu_ms and an AWS host profile")
        if role:
            if workload.properties.get("kind") != "relational" or p.get("engine") != "postgres":
                self.fail("SCALING_MODEL_INCOMPLETE", detail="Database CPU scaling currently supports relational stores on engine = postgres")
            databases = [e for e in self.arch.edges if e.kind == "hosted_on" and e.target == host.name]
            if len(databases) != 1:
                self.fail("SCALING_MODEL_INCOMPLETE", detail="Postgres scaling requires a dedicated database_cluster per database")
            count = workload.properties.get("read_replicas", 0) if role == "replica" else 1
        else:
            count = p.get("instances")
            if count is None:
                self.fail("SCALING_MODEL_INCOMPLETE", detail=f"{host.name} requires instances")
            if isinstance(count, tuple):
                count = count[1] if self.request.get("scale") == "maximum" else count[0]
        key = host.name + (":" + role if role else "")
        profile = hardware(self.arch, host)
        per_instance = profile["vcpus"] * 1000 * p.get("cpu_utilization", 0.7)
        record = self.resources.setdefault(key, {
            "resource": key, "instance_type": p.get("instance_type", "self_hosted"), "instances": count,
            "vcpus_per_instance": profile["vcpus"], "cpu_utilization": p.get("cpu_utilization", 0.7),
            "demand_cpu_ms_per_second": 0, "capacity_cpu_ms_per_second": count * per_instance,
            "per_instance_cpu_ms_per_second": per_instance,
            "horizontal_scaling": role != "primary",
        })
        record["demand_cpu_ms_per_second"] += self.request["rate_rps"] * cost
        if not number(record["demand_cpu_ms_per_second"]):
            self.fail("SCALING_MODEL_INCOMPLETE", detail="CPU demand exceeds the supported numeric range")

    def evaluate(self, other_workload=False):
        if "rate_rps" in self.request and not self.resources and not other_workload:
            self.fail("SCALING_MODEL_INCOMPLETE", detail="No service or PostgreSQL workload was executed")
        overloaded = []
        for record in self.resources.values():
            demand = record["demand_cpu_ms_per_second"]
            capacity = record["capacity_cpu_ms_per_second"]
            required = math.ceil(demand / record["per_instance_cpu_ms_per_second"])
            record["required_instances"] = required
            record["overloaded"] = demand > capacity and not math.isclose(demand, capacity, rel_tol=1e-12)
            self.result.capacity.append(record)
            self.result.trace.append(f"CPU {record['resource']}: demand={demand:g} CPU-ms/s; capacity={capacity:g}; instances={record['instances']}; required={required}")
            if record["overloaded"]:
                overloaded.append(record)
        if overloaded:
            record = overloaded[0]
            if not record["horizontal_scaling"]:
                self.fail("PRIMARY_CAPACITY_EXCEEDED", resource=record["resource"], demand=record["demand_cpu_ms_per_second"], capacity=record["capacity_cpu_ms_per_second"])
            self.fail("CAPACITY_EXCEEDED", resource=record["resource"], demand=record["demand_cpu_ms_per_second"], capacity=record["capacity_cpu_ms_per_second"], required=record["required_instances"])
