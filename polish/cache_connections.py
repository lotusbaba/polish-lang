"""Worst-case reusable client pools, not predictions of live socket counts."""
import math

POOL = {'pool_size', 'pool_scope', 'pool_distribution'}
OPTIONS = POOL | {'on_miss', 'on_error'}


def validate_cache_connections(arch, error):
    for node in arch.nodes.values():
        p = node.properties
        for key in ('workers_per_instance', 'nodes', 'max_connections_per_instance', 'reserved_connections_per_instance'):
            if key in p and (type(p[key]) is not int or p[key] < (0 if key.startswith('reserved') else 1)):
                error('E_VENDOR', line=node.line, detail=f'{node.name}.{key} has an invalid integer value')
        limit, reserve = p.get('max_connections_per_instance'), p.get('reserved_connections_per_instance', 0)
        if 'reserved_connections_per_instance' in p and (limit is None or type(limit) is int and type(reserve) is int and reserve >= limit):
            error('E_VENDOR', line=node.line, detail='Cache reserved connections must be less than its declared limit')
        hosts = arch.outgoing(node.name, 'hosted_on')
        if node.kind == 'cache' and hosts and 'nodes' in p:
            error('E_VENDOR', line=node.line, detail='Hosted cache node count comes from host instances; omit nodes')
    shared = {}
    for edge in arch.edges:
        p = edge.properties
        target = arch.nodes[edge.target]
        if target.kind != 'cache' or edge.kind not in {'reads', 'writes'}:
            if OPTIONS & p.keys():
                error('E_VENDOR', line=edge.line, detail='Cache pool and failure settings require a reads/writes cache edge')
            continue
        if POOL & p.keys():
            if not POOL <= p.keys():
                error('E_VENDOR', line=edge.line, detail='Cache pools require pool_size, pool_scope, and pool_distribution')
            if 'pool_size' in p and (type(p['pool_size']) is not int or p['pool_size'] < 1):
                error('E_VENDOR', line=edge.line, detail='pool_size must be a positive integer')
        source = arch.nodes[edge.source]
        service = source if source.kind == 'service' else arch.nodes.get(source.parent)
        if service:
            key = (service.name, target.name)
            settings = tuple(p.get(k) for k in sorted(POOL))
            if key in shared and settings != shared[key]:
                error('E_VENDOR', line=edge.line, detail='Edges from one service to one cache must declare the same shared pool settings')
            shared[key] = settings
            if p.get('pool_scope') == 'worker' and 'workers_per_instance' not in service.properties:
                error('E_VENDOR', line=edge.line, detail='Worker-scoped pools require service workers_per_instance')
        if (p.get('on_miss') == 'fallback' or p.get('on_error') == 'fallback') and not arch.outgoing(target.name, 'invokes'):
            error('E_VENDOR', line=edge.line, detail='Cache fallback policy requires cache invokes relationships')


class CacheConnections:
    def __init__(self, arch, result, budgets, fail):
        self.arch, self.result, self.budgets, self.fail = arch, result, budgets, fail
        self.seen, self.records = set(), {}

    def charge(self, edge, cache):
        p, cp = edge.properties, cache.properties
        if not POOL & p.keys() and 'max_connections_per_instance' not in cp:
            return
        if not POOL <= p.keys() or 'max_connections_per_instance' not in cp:
            self.fail('SCALING_MODEL_INCOMPLETE', detail=f'{cache.name} requires a cache limit and complete client pool settings')
        source = self.arch.nodes[edge.source]
        service = source if source.kind == 'service' else self.arch.nodes[source.parent]
        key = (service.name, cache.name)
        if key in self.seen:
            return
        self.seen.add(key)
        hosts = self.arch.outgoing(cache.name, 'hosted_on')
        count = self.budgets.count(self.arch.nodes[hosts[0].target]) if hosts else cp.get('nodes')
        if count is None:
            self.fail('SCALING_MODEL_INCOMPLETE', detail=f'Managed cache {cache.name} requires nodes')
        instances = self.budgets.count(self.budgets.host(service))
        workers = service.properties['workers_per_instance'] if p['pool_scope'] == 'worker' else 1
        total = instances * workers * p['pool_size']
        demand = total if p['pool_distribution'] == 'per_node' else math.ceil(total / count)
        record = self.records.get(cache.name)
        if record is None:
            record = dict(resource=cache.name, kind='cache_connections', nodes=count, potential_per_instance=0,
                          available_per_instance=cp['max_connections_per_instance'] - cp.get('reserved_connections_per_instance', 0),
                          clients=[], overloaded=False)
            self.records[cache.name] = record
            self.result.budgets.append(record)
        record['potential_per_instance'] += demand
        record['clients'].append(dict(service=service.name, instances=instances, workers=workers,
                                      pool_size=p['pool_size'], distribution=p['pool_distribution'], demand_per_node=demand))
        record['overloaded'] = record['potential_per_instance'] > record['available_per_instance']
        self.result.trace.append(f"Cache connections {cache.name}: potential={record['potential_per_instance']}/node; available={record['available_per_instance']}/node; distribution={p['pool_distribution']}")

    def evaluate(self):
        for name, record in self.records.items():
            if record['overloaded']:
                self.fail('CACHE_CONNECTION_BUDGET_EXCEEDED', node=name, demand=record['potential_per_instance'], capacity=record['available_per_instance'])
