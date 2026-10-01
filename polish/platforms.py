"""Declared-capacity models; no vendor SDK calls or hidden throughput defaults."""
from .vendors import product_profile


class PlatformModel:
    def __init__(self, arch, request, result, fail):
        self.arch, self.request, self.result, self.fail = arch, request, result, fail
        self.hops, self.loads = {}, {}

    def policy(self, node):
        p = node.properties
        self.hops[node.name] = self.hops.get(node.name, 0) + 1
        calls = self.hops[node.name]
        if "limit_rps" in p and "rate_rps" in self.request:
            demand = self.request["rate_rps"] * calls
            self.result.trace.append(f"Rate policy {node.name}: {demand:g}/{p['limit_rps']:g} requests/s")
            if demand > p["limit_rps"]:
                self.fail("RATE_LIMITED", node=node.name, demand=demand, limit=p["limit_rps"])
        if "burst_limit" in p and self.request.get("burst_requests", 0) * calls > p["burst_limit"]:
            self.fail("RATE_LIMITED", node=node.name, demand=self.request["burst_requests"] * calls, limit=p["burst_limit"])
        for request_key, limit_key in (("query_depth", "max_query_depth"), ("query_cost", "max_query_cost")):
            if limit_key in p:
                if request_key not in self.request:
                    self.fail("SCALING_MODEL_INCOMPLETE", detail=f"{node.name} requires scenario {request_key}")
                if self.request[request_key] > p[limit_key]:
                    self.fail("GRAPHQL_LIMIT_EXCEEDED", node=node.name)

    def add(self, node, kind, demand):
        key = (node.name, kind)
        self.loads[key] = self.loads.get(key, 0) + demand

    def cache(self, node):
        p = node.properties
        if "capacity_mib" not in p and "dataset_mib" not in p and "rate_rps" not in self.request:
            return
        if "capacity_mib" not in p:
            self.fail("SCALING_MODEL_INCOMPLETE", detail=f"{node.name} needs a cache product profile and capacity_mib")
        if p.get("dataset_mib", 0) > p["capacity_mib"]:
            self.fail("CACHE_CAPACITY_EXCEEDED", node=node.name)
        if "rate_rps" in self.request:
            if "max_ops_rps" not in p:
                self.fail("SCALING_MODEL_INCOMPLETE", detail=f"Cache {node.name} requires max_ops_rps")
            self.add(node, "cache_ops", self.request["rate_rps"])

    def database(self, host):
        if "rate_rps" in self.request:
            if "max_ops_rps" not in host.properties:
                self.fail("SCALING_MODEL_INCOMPLETE", detail=f"{host.name} requires measured cluster max_ops_rps")
            self.add(host, "database_ops", self.request["rate_rps"])

    def message_limit(self, node):
        p = node.properties
        if p.get("provider") == "aws" and p.get("product") == "kinesis" and p.get("capacity_mode") == "provisioned":
            if "messages_per_shard_rps" not in p:
                self.fail("SCALING_MODEL_INCOMPLETE", detail=f"{node.name} requires measured messages_per_shard_rps")
            return p["shards"] * p["messages_per_shard_rps"]
        if "max_messages_rps" not in p:
            self.fail("SCALING_MODEL_INCOMPLETE", detail=f"{node.name} requires max_messages_rps")
        return p["max_messages_rps"]

    def asynchronous(self, node, edge):
        self.result.trace.append(f"{edge.source} {edge.kind} {node.name}")
        if "rate_rps" not in self.request:
            return
        p = node.properties
        if edge.kind == "submits_to":
            if "worker_vcpus" not in p or "job_cpu_ms" not in edge.properties:
                self.fail("SCALING_MODEL_INCOMPLETE", detail=f"{node.name} requires worker_vcpus and submits_to job_cpu_ms")
            self.add(node, "batch_cpu", self.request["rate_rps"] * edge.properties["job_cpu_ms"])
        else:
            self.message_limit(node)
            self.add(node, "publish" if edge.kind == "publishes_to" else "consume", self.request["rate_rps"])

    def evaluate(self):
        failures = []
        messaging = set()
        for (name, kind), demand in self.loads.items():
            node = self.arch.nodes[name]
            p = node.properties
            if kind in {"cache_ops", "database_ops"}:
                limit = p["max_ops_rps"]
            elif kind == "batch_cpu":
                limit = p["workers"] * p["worker_vcpus"] * 1000 * p.get("cpu_utilization", 0.7)
            else:
                if node.kind != "topic":
                    messaging.add(name)
                limit = self.message_limit(node)
            record = {"resource": name, "kind": kind, "demand": demand, "capacity": limit, "overloaded": demand > limit}
            self.result.budgets.append(record)
            self.result.trace.append(f"{kind} {name}: demand={demand:g}; capacity={limit:g}")
            if demand > limit:
                failures.append((name, f"{kind} demand {demand:g} exceeds declared capacity {limit:g}"))
        for name in sorted(messaging):
            p = self.arch.nodes[name].properties
            arrivals = self.loads.get((name, "publish"), 0)
            consumed = min(self.message_limit(self.arch.nodes[name]), self.loads.get((name, "consume"), 0) + p.get("consumer_rate_rps", 0))
            backlog = max(0, arrivals - consumed) * self.request.get("window_seconds", 1)
            self.result.budgets.append({"resource": name, "kind": "backlog", "messages": backlog})
            self.result.trace.append(f"Backlog {name}: max(0, {arrivals:g} - {consumed:g}) × {self.request.get('window_seconds', 1):g}s = {backlog:g} messages")
            if "max_backlog" in p and backlog > p["max_backlog"]:
                failures.append((name, f"backlog {backlog:g} exceeds {p['max_backlog']} messages"))
        if failures:
            name, detail = failures[0]
            self.fail("PLATFORM_CAPACITY_EXCEEDED", node=name, detail=detail)
