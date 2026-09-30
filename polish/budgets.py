"""Optional memory feasibility and worst-case connection pool budgets."""
import math
from .scaling import number, hardware


class ResourceBudgets:
    def __init__(self, arch, request, result, fail):
        self.arch, self.request, self.result, self.fail = arch, request, result, fail
        self.memory = {}
        self.connections = {}
        self.pools = set()

    def host(self, service):
        return self.arch.nodes[self.arch.outgoing(service.name, "hosted_on")[0].target]

    def count(self, host):
        count = host.properties.get("instances")
        if count is None:
            self.fail("SCALING_MODEL_INCOMPLETE", detail=f"{host.name} needs instances for resource budgets")
        if isinstance(count, tuple):
            return count[1] if self.request.get("scale") == "maximum" else count[0]
        return count

    def service(self, service, operation):
        if "rate_rps" not in self.request:
            return
        host = self.host(service)
        residents = [self.arch.nodes[e.source] for e in self.arch.edges
                     if e.kind == "hosted_on" and e.target == host.name]
        enabled = "memory_utilization" in host.properties or any(
            "base_memory_mib" in n.properties for n in residents) or any(
                k in operation.properties or k in service.properties for k in ("duration_ms", "memory_per_request_mib"))
        if not enabled:
            return
        if host.name not in self.memory:
            if any("base_memory_mib" not in n.properties for n in residents):
                self.fail("SCALING_MODEL_INCOMPLETE", detail=f"All residents of {host.name} need base_memory_mib; unsupported residents must use a separate pool")
            count = self.count(host)
            profile = hardware(self.arch, host)
            if not profile.get("memory_gib"):
                self.fail("SCALING_MODEL_INCOMPLETE", detail=f"{host.name} needs an AWS profile for memory modeling")
            base = count * sum(n.properties["base_memory_mib"] for n in residents)
            self.memory[host.name] = {
                "resource": host.name, "kind": "memory", "instances": count,
                "base_memory_mib": base, "inflight_memory_mib": 0,
                "capacity_mib": count * profile["memory_gib"] * 1024 * host.properties.get("memory_utilization", 0.8),
            }
        cost = operation.properties.get("memory_per_request_mib", service.properties.get("memory_per_request_mib"))
        duration = operation.properties.get("duration_ms", service.properties.get("duration_ms"))
        if cost is None or duration is None:
            self.fail("SCALING_MODEL_INCOMPLETE", detail=f"{operation.name} needs memory_per_request_mib and duration_ms")
        record = self.memory[host.name]
        single = record["base_memory_mib"] / record["instances"] + cost
        limit = record["capacity_mib"] / record["instances"]
        if single > limit:
            self.fail("MEMORY_CAPACITY_EXCEEDED", resource=host.name + ":single-instance", demand=single, capacity=limit)
        self.memory[host.name]["inflight_memory_mib"] += self.request["rate_rps"] * duration / 1000 * cost
        if not number(self.memory[host.name]["inflight_memory_mib"]):
            self.fail("SCALING_MODEL_INCOMPLETE", detail="Memory demand exceeds the supported numeric range")

    def connection(self, edge, database, role):
        if "rate_rps" not in self.request:
            return
        source = self.arch.nodes[edge.source]
        service = source if source.kind == "service" else self.arch.nodes[source.parent]
        size = service.properties.get("connection_pool_size")
        limit = database.properties.get("max_connections")
        if size is None and limit is None:
            return
        if size is None or limit is None:
            self.fail("SCALING_MODEL_INCOMPLETE", detail=f"{service.name} → {database.name} needs connection_pool_size and max_connections")
        key = (database.name, role, service.name)
        if key in self.pools:
            return
        self.pools.add(key)
        replicas = database.properties.get("read_replicas", 0) if role == "replica" else 1
        record = self.connections.setdefault((database.name, role), {
            "resource": database.name + ":" + role, "kind": "connections",
            "configured_pool_connections": 0, "database_instances": replicas,
            "available_per_instance": limit - database.properties.get("reserved_connections", 0),
        })
        record["configured_pool_connections"] += self.count(self.host(service)) * size

    def evaluate(self):
        failures = []
        for record in self.memory.values():
            demand = record["base_memory_mib"] + record["inflight_memory_mib"]
            capacity = record["capacity_mib"]
            record.update(demand_mib=demand, overloaded=demand > capacity)
            self.result.budgets.append(record)
            self.result.trace.append(f"Memory {record['resource']}: base + inflight = {demand:g} MiB; capacity={capacity:g} MiB")
            if demand > capacity:
                failures.append(("MEMORY_CAPACITY_EXCEEDED", record["resource"], demand, capacity))
        for record in self.connections.values():
            demand = math.ceil(record["configured_pool_connections"] / record["database_instances"])
            capacity = record["available_per_instance"]
            record.update(potential_per_instance=demand, overloaded=demand > capacity)
            self.result.budgets.append(record)
            self.result.trace.append(f"Connection budget {record['resource']}: potential={demand}/instance; available={capacity}/instance")
            if demand > capacity:
                failures.append(("CONNECTION_BUDGET_EXCEEDED", record["resource"], demand, capacity))
        if failures:
            code, resource, demand, capacity = failures[0]
            self.fail(code, resource=resource, demand=demand, capacity=capacity)
