"""Declared local-resource and long-lived broadcast-reader budgets."""
import math
from .scaling import number
from .vendors import product_profile


def validate_runtime(arch, error):
    for node in arch.nodes.values():
        p = node.properties
        for key in ('max_requests_rps','capacity_mib','max_ops_rps','max_client_connections','max_stream_connections'):
            if key in p and not number(p[key], True):
                error('E_VENDOR', line=node.line, detail=f'{node.name}.{key} must be finite and positive')
        for key in ('used_mib','reserved_stream_connections'):
            if key in p and not number(p[key]):
                error('E_VENDOR', line=node.line, detail=f'{node.name}.{key} must be finite and nonnegative')
        if 'reserved_stream_connections' in p:
            limit, reserve = p.get('max_stream_connections'), p['reserved_stream_connections']
            if not number(limit, True) or number(reserve) and reserve >= limit:
                error('E_VENDOR', line=node.line, detail='Stream reserve must be below the declared connection capacity')
        if node.kind == 'volume' and p.get('storage') not in {'persistent','ephemeral'}:
            error('E_VENDOR', line=node.line, detail='Volumes require storage = persistent or ephemeral')
        if node.kind == 'stream' and product_profile(arch,node).get('stream_mode') == 'broadcast':
            hosts=arch.outgoing(node.name,'hosted_on')
            if len(hosts)==1 and arch.nodes[hosts[0].target].properties.get('instances') not in (1,(1,1)):
                error('E_VENDOR',line=node.line,detail='Redis broadcast streams currently require a singleton compute host')
    for edge in arch.edges:
        target=arch.nodes[edge.target]
        if 'connections_per_client' in edge.properties:
            value=edge.properties['connections_per_client']
            if product_profile(arch,target).get('stream_mode')!='broadcast' or type(value) is not int or value < 1:
                error('E_VENDOR',line=edge.line,detail='connections_per_client requires a broadcast stream and a positive integer')
        if edge.kind=='mounts' and 'access' not in edge.properties:
            error('E_VENDOR',line=edge.line,detail='mounts requires access = read_only or read_write')
        if edge.kind in {'reads','writes'} and target.kind in {'volume','artifact_store'}:
            allowed=set() if target.kind=='volume' else {'via','protocol','path'}
            if set(edge.properties)-allowed:
                error('E_VENDOR',line=edge.line,detail='Unsupported volume/object-store access option')
            if target.properties.get('kind')=='container_registry':
                error('E_VENDOR',line=edge.line,detail='Use image_from for container registries')
            if target.kind=='volume':
                source=arch.nodes[edge.source]
                service=source if source.kind=='service' else arch.nodes.get(source.parent)
                mounts=arch.outgoing(service.name,'mounts') if service else []
                if not any(m.target==target.name for m in mounts):
                    error('E_VENDOR',line=edge.line,detail=f'{edge.source} requires a service mount for {target.name}')
    for scenario in arch.scenarios:
        for key in ('concurrent_connections','consumer_lag_seconds'):
            if key in scenario.request and not number(scenario.request[key], key=='concurrent_connections'):
                error('E_SCALING',line=scenario.line,detail=f'{key} must be finite and {"positive" if key=="concurrent_connections" else "nonnegative"}')
        if 'concurrent_connections' in scenario.request and type(scenario.request['concurrent_connections']) is not int:
            error('E_SCALING',line=scenario.line,detail='concurrent_connections must be an integer')
        if 'consumer_lag_seconds' in scenario.request and 'concurrent_connections' not in scenario.request:
            error('E_SCALING',line=scenario.line,detail='consumer_lag_seconds requires concurrent_connections')


class RuntimeResources:
    def __init__(self, arch, request, result, fail):
        self.arch,self.request,self.result,self.fail=arch,request,result,fail
        self.loads={}
        self.broadcasts=0

    def budget(self,node,kind,demand,limit):
        if not number(demand) or not number(limit):
            self.fail('SCALING_MODEL_INCOMPLETE',detail=f'{node.name} budget exceeds the supported numeric range')
        key=(node.name,kind)
        record=self.loads.get(key)
        if record is None:
            record=dict(resource=node.name,kind=kind,demand=0,capacity=limit,overloaded=False)
            self.loads[key]=record
            self.result.budgets.append(record)
        record['demand']+=demand
        if not number(record['demand']):
            self.fail('SCALING_MODEL_INCOMPLETE',detail=f'{node.name} aggregate demand exceeds the supported numeric range')
        record['overloaded']=record['demand']>limit

    def proxy(self,node):
        clients=self.request.get('concurrent_connections')
        if clients is None:return
        p=node.properties
        if p.get('supports_streaming') is False or p.get('response_buffering') is True:
            self.fail('STREAMING_UNSUPPORTED',node=node.name)
        if 'max_client_connections' in p:
            # Count each proxy once per scenario path, even if revisited.
            key=(node.name,'proxy_connections')
            if key not in self.loads:self.budget(node,'proxy_connections',clients,p['max_client_connections'])

    def broadcast(self,node,edge):
        p=node.properties
        hosts=self.arch.outgoing(node.name,'hosted_on')
        host=self.arch.nodes[hosts[0].target]
        if host.name not in self.result.reached:self.result.reached.append(host.name)
        if edge.kind=='consumes_from':
            self.broadcasts+=1
            if 'concurrent_connections' not in self.request:
                if 'rate_rps' in self.request:
                    self.fail('SCALING_MODEL_INCOMPLETE',detail='Broadcast readers require concurrent_connections, not HTTP request rate alone')
                return
            clients=self.request['concurrent_connections']
            if 'max_stream_connections' not in host.properties:
                self.fail('SCALING_MODEL_INCOMPLETE',detail=f'{host.name} needs max_stream_connections')
            self.budget(host,'stream_connections',clients*edge.properties.get('connections_per_client',1),host.properties['max_stream_connections']-host.properties.get('reserved_stream_connections',0))
            if 'consumer_lag_seconds' in self.request:
                if 'source_rate_rps' not in p:
                    self.fail('SCALING_MODEL_INCOMPLETE',detail=f'{node.name} needs source_rate_rps to calculate retained history')
                demand=self.request['consumer_lag_seconds']*p['source_rate_rps']
                if not number(demand):
                    self.fail('SCALING_MODEL_INCOMPLETE',detail=f'{node.name} retention demand exceeds the supported numeric range')
                demand=math.ceil(demand)
                if (node.name,'retention_messages') not in self.loads:
                    self.budget(node,'retention_messages',demand,p['retention_messages'])
            if 'source_rate_rps' in p:
                if 'max_messages_rps' not in p:
                    self.fail('SCALING_MODEL_INCOMPLETE',detail=f'{node.name} needs max_messages_rps for broadcast delivery')
                self.budget(node,'broadcast_deliveries',clients*p['source_rate_rps'],p['max_messages_rps'])
        elif 'rate_rps' in self.request:
            if 'max_messages_rps' not in p:
                self.fail('SCALING_MODEL_INCOMPLETE',detail=f'{node.name} needs max_messages_rps')
            self.budget(node,'stream_publish',self.request['rate_rps'],p['max_messages_rps'])
        self.result.trace.append(f'{edge.source} {edge.kind} {node.name}: independent broadcast cursors; reads do not remove messages')

    def access(self,node,edge):
        p=node.properties
        if node.kind=='artifact_store' and p.get('endpoint')=='website' and edge.kind=='writes':
            self.fail('S3_WEBSITE_METHOD',node=node.name)
        if node.kind=='volume':
            source=self.arch.nodes[edge.source]
            service=source if source.kind=='service' else self.arch.nodes[source.parent]
            mount=next(m for m in self.arch.outgoing(service.name,'mounts') if m.target==node.name)
            if edge.kind=='writes' and mount.properties['access']=='read_only':
                self.fail('VOLUME_READ_ONLY',node=node.name)
            if 'used_mib' in p:
                if 'capacity_mib' not in p:self.fail('SCALING_MODEL_INCOMPLETE',detail=f'{node.name} needs capacity_mib')
                key=(node.name,'volume_storage')
                if key not in self.loads:self.budget(node,'volume_storage',p['used_mib'],p['capacity_mib'])
        if 'rate_rps' in self.request:
            key='max_ops_rps' if node.kind=='volume' else 'max_requests_rps'
            if key not in p:self.fail('SCALING_MODEL_INCOMPLETE',detail=f'{node.name} needs {key}')
            self.budget(node,'resource_operations',self.request['rate_rps'],p[key])

    def evaluate(self):
        if 'concurrent_connections' in self.request and not self.broadcasts:
            self.fail('SCALING_MODEL_INCOMPLETE',detail='Concurrent-connection scenarios require a broadcast consumer')
        for record in self.loads.values():
            self.result.trace.append(f"{record['kind']} {record['resource']}: demand={record['demand']:g}; capacity={record['capacity']:g}")
            if record['overloaded']:
                code= {'stream_connections':'STREAM_CONNECTIONS_EXCEEDED','proxy_connections':'STREAM_CONNECTIONS_EXCEEDED','retention_messages':'STREAM_RETENTION_EXCEEDED'}.get(record['kind'])
                if code:self.fail(code,node=record['resource'],demand=record['demand'],capacity=record['capacity'])
                self.fail('PLATFORM_CAPACITY_EXCEEDED',node=record['resource'],detail=f"{record['kind']} exceeds declared capacity")
