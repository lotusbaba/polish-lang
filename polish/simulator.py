"""Deterministic functional simulation; no network requests or deployments."""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from fnmatch import fnmatchcase

from .model import Architecture, Node, Scenario
from .storage import copies
from .errors import render_error
from .scaling import CapacityModel
from .budgets import ResourceBudgets
from .vendors import DynamoCapacity, product_profile
from .platforms import PlatformModel


@dataclass
class SimulationResult:
    scenario: str
    passed: bool = False
    outcome: str = "success"
    error: str | None = None
    trace: list[str] = field(default_factory=list)
    reached: list[str] = field(default_factory=list)
    accessed: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    capacity: list[dict] = field(default_factory=list)
    budgets: list[dict] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


class RequestFailure(Exception):
    def __init__(self, code, message, outcome="error"):
        self.code, self.message, self.outcome = code, message, outcome


def simulate(arch: Architecture, scenario: Scenario) -> SimulationResult:
    """Run a scenario from a successfully checked architecture."""
    result = SimulationResult(scenario.name)
    request = scenario.request
    active: set[str] = set()

    def fail(rule, **values):
        code, message = render_error(arch.errors, rule, **values)
        outcome = arch.errors.get(rule, {}).get("outcome", "error") if code != "E_CONFIG" else "error"
        raise RequestFailure(code, message, outcome)

    capacity_model = CapacityModel(arch, request, result, fail)
    budgets = ResourceBudgets(arch, request, result, fail)
    dynamo_capacity = DynamoCapacity(arch, request, result, fail)
    platforms = PlatformModel(arch, request, result, fail)

    pulled = set()
    asset_loads = {}

    def image_source(service):
        if service.name in pulled:
            return
        pulled.add(service.name)
        for edge in arch.outgoing(service.name, "image_from"):
            registry = arch.nodes[edge.target]
            connection(registry, "https")
            if not edge.properties.get("authorized", False):
                fail("IMAGE_PULL_DENIED", node=registry.name)
            mark(registry)
            result.trace.append(f"{service.name} pulls its image from {registry.name}")
            if "image_pulls_rps" in request:
                asset_budget(registry, request["image_pulls_rps"], "max_pulls_rps")

    def asset_budget(node, demand, key):
        if key not in node.properties:
            fail("SCALING_MODEL_INCOMPLETE", detail=f"{node.name} requires {key}")
        asset_loads[node.name] = asset_loads.get(node.name, 0) + demand
        total = asset_loads[node.name]
        limit = node.properties[key]
        result.budgets.append(dict(resource=node.name, kind=key, demand=total, capacity=limit, overloaded=total > limit))
        result.trace.append(f"{node.name}: {total:g}/{limit:g} requests/s ({key})")
        if total > limit:
            fail("PLATFORM_CAPACITY_EXCEEDED", node=node.name, detail=f"demand {total:g} exceeds declared capacity {limit:g}")

    def authorize(node: Node):
        if node.parent:
            authorize(arch.nodes[node.parent])
        for requirement in node.requirements:
            allowed = request.get("authenticated", False)
            if requirement != "authenticated":
                allowed = allowed and request.get("actor") == requirement
            if not allowed:
                fail('ACCESS_DENIED_2', node_name=node.name, requirement=requirement)

    def mark(node):
        if node.name not in result.reached:
            result.reached.append(node.name)

    def connection(node, protocol):
        profile = product_profile(arch, node)
        if "protocols" in profile and protocol not in profile["protocols"]:
            fail("AWS_PROTOCOL_UNSUPPORTED", node=node.name, protocol=protocol)
        if profile.get("terminate_encrypted") and protocol == "https" and not node.properties.get("tls_termination"):
            fail("AWS_PROTOCOL_UNSUPPORTED", node=node.name, protocol="HTTPS without termination")
        if protocol in {"https", "tls"} and not (node.properties.get("tls") or node.properties.get("tls_termination")):
            fail('TLS_UNSUPPORTED', node_name=node.name)
        result.trace.append(f"{protocol.upper()} accepted by {node.name}")
        platforms.policy(node)

    def select_route(node, path):
        routes = arch.outgoing(node.name, "routes_to")
        matches = [e for e in routes if e.pattern is not None and fnmatchcase(path, e.pattern)]
        if matches:
            def specificity(edge):
                return min([edge.pattern.find(c) for c in "*?[" if c in edge.pattern] or [len(edge.pattern)])
            best = max(map(specificity, matches))
            matches = [e for e in matches if specificity(e) == best]
        else:
            matches = [e for e in routes if e.pattern is None]
        if not matches:
            fail('NO_ROUTE', node_name=node.name, path=path)
        if len(matches) != 1:
            fail('AMBIGUOUS_ROUTE', node_name=node.name)
        return matches[0].target

    def upstream_protocol(node, protocol):
        encrypted = protocol in {"https", "tls"}
        plain = "http" if protocol in {"http", "https"} else "tcp"
        terminated = encrypted and node.properties.get("tls_termination", False)
        upstream = node.properties.get("upstream_protocol", plain if terminated else protocol)
        if (upstream in {"http", "https"}) != (protocol in {"http", "https"}):
            fail('PROTOCOL_MISMATCH', node_name=node.name, protocol=protocol, upstream=upstream)
        if encrypted and upstream == plain and not terminated:
            fail('TLS_TERMINATION_REQUIRED', node_name=node.name, plain_upper=plain.upper())
        if terminated:
            result.trace.append(f"TLS terminated at {node.name}; upstream uses {upstream.upper()}")
        return upstream

    def transport(edge, destination):
        """Each dependency gets its own route traversal, separate from call recursion."""
        hosts = arch.outgoing(destination.name, "hosted_on")
        host = arch.nodes[hosts[0].target] if hosts else destination
        protocol = edge.properties.get("protocol", "http" if edge.kind == "invokes" else "tcp")
        target = arch.nodes[edge.target]
        path = edge.properties.get("path", target.properties.get("path", "/"))
        current = edge.properties.get("via", host.name)
        seen = set()
        previous = edge.source
        while True:
            if current in seen:
                fail('REQUEST_CYCLE_2', edge_source=edge.source, current=current)
            seen.add(current)
            node = arch.nodes[current]
            result.trace.append(f"Connect {previous} → {current} using {protocol.upper()}")
            if node.kind in {"cdn", "gateway"} and protocol in {"tcp", "tls"}:
                fail('PROTOCOL_MISMATCH_2', current=current)
            receiver = arch.nodes[node.parent] if node.kind == "operation" else node
            connection(receiver, protocol)
            mark(node)
            if current in {host.name, destination.name, target.name}:
                # Both the receiving hardware and hosted workload must support TLS.
                for receiver in (host, destination):
                    if receiver.name != current:
                        connection(receiver, protocol)
                        mark(receiver)
                return
            if node.kind not in {"cdn", "gateway", "load_balancer", "cache"}:
                fail('WRONG_DESTINATION', edge_source=edge.source, current=current, destination_name=destination.name, host_name=host.name)
            next_name = select_route(node, path)
            protocol = upstream_protocol(node, protocol)
            previous, current = current, next_name

    def dependencies(node):
        for edge in arch.outgoing(node.name):
            target = arch.nodes[edge.target]
            if edge.kind == "invokes":
                service = target if target.kind == "service" else arch.nodes[target.parent]
                result.trace.append(f"{node.name} invokes {target.name}")
                transport(edge, service)
                visit(target.name, "http", network=False)
            elif edge.kind in {"reads", "writes"}:
                if target.kind == "cache":
                    transport(edge, target)
                    platforms.cache(target)
                    mark(target)
                    if target.name not in result.accessed:
                        result.accessed.append(target.name)
                    cache_result = request.get("cache_result", "miss")
                    result.trace.append(f"Cache {target.name}: {'write' if edge.kind == 'writes' else cache_result}")
                    if edge.kind == "reads" and cache_result == "miss":
                        if target.name in active:
                            fail("REQUEST_CYCLE", name=target.name)
                        active.add(target.name)
                        try:
                            if not arch.outgoing(target.name, "invokes"):
                                fail("SCALING_MODEL_INCOMPLETE", detail=f"Cache miss at {target.name} needs an invokes fallback")
                            dependencies(target)
                        finally:
                            active.remove(target.name)
                    continue
                database = target if target.kind == "database" else arch.nodes[target.parent]
                transport(edge, database)
                p = database.properties
                db_host = arch.nodes[arch.outgoing(database.name, "hosted_on")[0].target]
                dynamo = db_host.properties.get("product") == "dynamodb"
                if edge.kind == "reads":
                    read_from = edge.properties.get("read_from", "primary")
                    consistency = edge.properties.get("consistency", "eventual")
                    if read_from == "replica" and p.get("read_replicas", 0) < 1:
                        fail('NO_READ_REPLICA', database_name=database.name)
                    if read_from == "replica" and consistency == "strong" and p.get("replication") != "synchronous":
                        fail('CONSISTENCY_UNSUPPORTED', database_name=database.name)
                    result.trace.append(f"Read {target.name} from {'managed endpoint' if dynamo else read_from}, consistency={consistency}")
                else:
                    durability = p.get("durability", "memory")
                    required = edge.properties.get("durability", "memory")
                    levels = {"memory": 0, "disk": 1, "replicated": 2}
                    if levels[durability] < levels[required]:
                        fail('DURABILITY_UNSATISFIED', node_name=node.name, required=required, database_name=database.name, durability=durability)
                    count = copies(p)
                    ack = p.get("write_ack", "primary")
                    acknowledgements = count if ack == "all" else count // 2 + 1 if ack == "quorum" else 1
                    result.trace.append(f"Write {target.name}: managed DynamoDB persistence" if dynamo else f"Write {target.name}: durability={durability}, acknowledgements={acknowledgements}/{count}")
                mark(target)
                role = edge.properties.get("read_from", "primary") if edge.kind == "reads" else "primary"
                if dynamo:
                    dynamo_capacity.charge(database, edge, db_host)
                elif product_profile(arch, db_host).get("capacity_model") == "operations":
                    platforms.database(db_host)
                else:
                    capacity_model.charge(database, edge.properties.get("cpu_ms"), role)
                    budgets.connection(edge, database, role)
                for name in (database.name, target.name):
                    if name not in result.accessed:
                        result.accessed.append(name)
                result.trace.append(f"{node.name} {edge.kind} {target.name}; partitions={p.get('partitions', 'unspecified')}")
            elif edge.kind in {"publishes_to", "consumes_from", "submits_to"}:
                connection(target, "tls" if target.kind == "stream" else "https")
                mark(target)
                platforms.asynchronous(target, edge)

    def endpoint(candidates):
        matches = [n for n in candidates if n.properties.get("method") == request.get("method", "GET")
                   and fnmatchcase(request.get("path", "/"), n.properties.get("path", ""))]
        if not matches:
            fail('NO_ENDPOINT')
        exact = [n for n in matches if n.properties["path"] == request.get("path", "/")]
        matches = exact or matches
        if len(matches) != 1:
            fail('AMBIGUOUS_ENDPOINT')
        return matches[0]

    def load_page(node, protocol):
        edges = arch.outgoing(node.name, "loaded_from") or arch.outgoing(node.parent, "loaded_from")
        source = edges[0].target
        result.trace.append(f"{node.name} loaded_from {source}")
        # Loading a renderer is a component request, not API operation dispatch.
        visit(source, protocol, rendering=True)

    def visit(name, protocol, network=True, rendering=False):
        if name in active:
            fail('REQUEST_CYCLE', name=name)
        active.add(name)
        try:
            node = arch.nodes[name]
            authorize(node)
            if network and node.kind not in {"frontend", "page", "operation"}:
                connection(node, protocol)
            mark(node)
            if node.kind in {"frontend", "page"}:
                load_page(node, protocol)
            elif node.kind in {"cdn", "gateway", "load_balancer", "cache"}:
                target = select_route(node, request.get("path", "/"))
                upstream = upstream_protocol(node, protocol)
                result.trace.append(f"{name} routes to {target}")
                visit(target, upstream, rendering=rendering)
            elif node.kind == "compute_pool":
                services = [arch.nodes[e.source] for e in arch.edges if e.kind == "hosted_on"
                            and e.target == name and arch.nodes[e.source].kind == "service"]
                if rendering:
                    if len(services) != 1:
                        fail('AMBIGUOUS_RENDERER')
                    visit(services[0].name, protocol, network=False, rendering=True)
                else:
                    names = {s.name for s in services}
                    operation = endpoint([n for n in arch.nodes.values() if n.kind == "operation" and n.parent in names])
                    visit(operation.name, protocol, network=False)
            elif node.kind == "service":
                host = arch.nodes[arch.outgoing(name, "hosted_on")[0].target]
                image_source(node)
                mark(host)
                result.trace.append(f"{name} runs on {host.name}")
                if network and not rendering:
                    operation = endpoint([n for n in arch.nodes.values() if n.parent == name and n.kind == "operation"])
                    visit(operation.name, protocol, network=False)
                else:
                    capacity_model.charge(node, node.properties.get("cpu_ms"))
                    budgets.service(node, node)
                    dependencies(node)
            elif node.kind == "operation":
                service = arch.nodes[node.parent]
                # Direct network routes to operations inherit their service's TLS support.
                if network:
                    connection(service, protocol)
                image_source(service)
                mark(service)
                host = arch.nodes[arch.outgoing(service.name, "hosted_on")[0].target]
                mark(host)
                result.trace.append(f"{node.name} executes on {host.name}")
                capacity_model.charge(service, node.properties.get("cpu_ms", service.properties.get("cpu_ms")))
                budgets.service(service, node)
                dependencies(service)
                dependencies(node)
            elif node.kind == "artifact_store":
                if node.properties.get("kind") == "container_registry":
                    fail("REGISTRY_NOT_WEB_ORIGIN", node=name)
                if node.properties.get("product") == "s3":
                    if node.properties.get("endpoint") == "website" and request.get("method", "GET") not in {"GET", "HEAD"}:
                        fail("S3_WEBSITE_METHOD", node=name)
                    if "rate_rps" in request:
                        asset_budget(node, request["rate_rps"], "max_requests_rps")
            elif node.kind in {"database", "database_cluster"}:
                fail('PROTOCOL_MISMATCH_3')
        finally:
            active.remove(name)

    def navigate(entry, destination, protocol):
        # Find a permitted path, then execute it; unauthorized branches do not
        # prevent an alternative permitted path from being used.
        queue = deque([(entry, [entry])])
        seen = {entry}
        blocked = False
        while queue:
            name, path = queue.popleft()
            try:
                authorize(arch.nodes[name])
            except RequestFailure:
                blocked = True
                continue
            if name == destination:
                for page in path:
                    result.trace.append(f"Navigate to {page}")
                    visit(page, protocol)
                return
            for edge in arch.outgoing(name, "navigates_to"):
                if edge.target not in seen:
                    seen.add(edge.target)
                    queue.append((edge.target, path + [edge.target]))
        # Only classify denial when a structural path actually exists.
        reachable, queue = {entry}, deque([entry])
        while queue:
            for edge in arch.outgoing(queue.popleft(), "navigates_to"):
                if edge.target not in reachable:
                    reachable.add(edge.target)
                    queue.append(edge.target)
        if blocked and destination in reachable:
            fail('ACCESS_DENIED', destination=destination)
        fail('NO_NAVIGATION_PATH', entry=entry, destination=destination)

    try:
        if "action" in request:
            entry = arch.nodes[request["entry"]]
            visit(entry.name, request.get("protocol", "https"))
            permitted = arch.outgoing(entry.name, "invokes")
            if entry.parent:
                permitted += arch.outgoing(entry.parent, "invokes")
            action = arch.nodes[request["action"]]
            if not any(e.target in {action.name, action.parent} for e in permitted):
                fail('INVOCATION_NOT_ALLOWED', entry_name=entry.name, action_name=action.name)
            result.trace.append(f"{entry.name} invokes {action.name} via {request['via']}")
            # Only observations from the API leg can satisfy the action.
            previous_reached = result.reached
            result.reached = []
            try:
                visit(request["via"], request.get("protocol", "https"))
                if action.name not in result.reached:
                    fail('ACTION_NOT_REACHED', action_name=action.name)
            finally:
                result.reached = list(dict.fromkeys(previous_reached + result.reached))
        elif "destination" in request:
            navigate(request["entry"], request["destination"], request.get("protocol", "https"))
        else:
            visit(request["entry"], request.get("protocol", "https"))
        if "image_pulls_rps" in request and not any(arch.nodes[name].properties.get("kind") == "container_registry" for name in asset_loads):
            fail("SCALING_MODEL_INCOMPLETE", detail="image_pulls_rps requires an executed service with image_from")
        capacity_model.evaluate(other_workload=any(arch.nodes[name].properties.get("product") == "s3" for name in asset_loads))
        budgets.evaluate()
        dynamo_capacity.evaluate()
        platforms.evaluate()
        result.trace.append("Response returned to caller")
    except RequestFailure as exc:
        result.outcome, result.error = exc.outcome, exc.code
        result.trace.append(f"{exc.code}: {exc.message}")
    if result.outcome != scenario.expected["outcome"]:
        result.failures.append(render_error(arch.errors, "ASSERT_OUTCOME", expected=scenario.expected['outcome'], actual=result.outcome)[1])
    if "error" in scenario.expected and result.error != scenario.expected["error"]:
        result.failures.append(render_error(arch.errors, "ASSERT_ERROR", expected=scenario.expected['error'], actual=result.error)[1])
    for kind, target in scenario.assertions:
        actual = result.reached if kind == "reaches" else result.accessed
        if target not in actual:
            result.failures.append(render_error(arch.errors, "ASSERT_OBSERVATION", kind=kind, target=target)[1])
    result.passed = not result.failures
    return result
