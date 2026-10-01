"""Configuration-owned diagnostic details and component/product inheritance."""
from string import Formatter

CONTEXT = {'name', 'component', 'provider', 'product', 'model', 'missing_fields'}


class DiagnosticDetail(str):
    def __new__(cls, text, context):
        value = super().__new__(cls, text)
        value.context = context
        return value


def template_fields(message):
    fields = set()
    if not isinstance(message, str):
        raise ValueError('Diagnostic message must be a string')
    for _, key, spec, conversion in Formatter().parse(message):
        if key is not None:
            if not key.isidentifier() or spec or conversion:
                raise ValueError(f'Unsafe diagnostic placeholder: {key}')
            fields.add(key)
    return fields


def validate_diagnostics(config, baseline):
    catalog = config['diagnostic_details']
    if not isinstance(catalog, dict) or set(baseline) - catalog.keys():
        raise ValueError('Missing required diagnostic detail definitions')
    for rule, definition in catalog.items():
        if not isinstance(definition, dict) or set(definition) - {'message','parameters','model','inputs'}:
            raise ValueError(f'Invalid diagnostic definition: {rule}')
        params = definition.get('parameters')
        if not isinstance(params, list) or any(not isinstance(p,str) or not p.isidentifier() for p in params) or len(set(params)) != len(params):
            raise ValueError(f'Invalid diagnostic parameters: {rule}')
        if rule in baseline and params != baseline[rule]['parameters']:
            raise ValueError(f'Diagnostic parameter contract changed: {rule}')
        if template_fields(definition.get('message')) - (set(params) | CONTEXT):
            raise ValueError(f'Unknown diagnostic placeholder: {rule}')
        if 'model' in definition and not isinstance(definition['model'],str):
            raise ValueError(f'Invalid model label: {rule}')
        inputs = definition.get('inputs', {})
        if not isinstance(inputs, dict):
            raise ValueError(f'Invalid diagnostic inputs: {rule}')
        expected_inputs = baseline.get(rule, {}).get('inputs', {})
        if rule in baseline and set(inputs) != set(expected_inputs):
            raise ValueError(f'Diagnostic input contract changed: {rule}')
        for name, binding in inputs.items():
            if not isinstance(name,str) or not isinstance(binding,dict) or set(binding) != {'scope','field','label'} or binding['scope'] not in {'node','edge','request','host','service'} or any(not isinstance(binding[k],str) or not binding[k] for k in ('field','label')):
                raise ValueError(f'Invalid input binding: {rule}')
            if name in expected_inputs and binding['scope'] != expected_inputs[name]['scope']:
                raise ValueError(f'Diagnostic input scope changed: {rule}')
    def overrides(owner):
        overrides = owner.get('diagnostics', {})
        if not isinstance(overrides, dict):
            raise ValueError('diagnostics overrides must be an object')
        for rule, override in overrides.items():
            base = catalog.get(rule, config['errors'].get(rule))
            if base is None or not isinstance(override, dict) or set(override) - {'message','inputs'} or not override:
                raise ValueError(f'Invalid diagnostic override: {rule}')
            if 'message' in override and template_fields(override['message']) - (set(base['parameters']) | CONTEXT):
                raise ValueError(f'Unknown override placeholder: {rule}')
            if 'inputs' in override:
                bindings=override['inputs']
                if rule not in catalog or not isinstance(bindings,dict) or set(bindings)-base.get('inputs',{}).keys():
                    raise ValueError(f'Invalid override input contract: {rule}')
                for name, binding in bindings.items():
                    if not isinstance(binding,dict) or set(binding) != {'scope','field','label'} or binding['scope'] not in {'node','edge','request','host','service'} or any(not isinstance(binding[k],str) or not binding[k] for k in ('field','label')):
                        raise ValueError(f'Invalid override input binding: {rule}')
                    if binding['scope'] != base['inputs'][name]['scope']:
                        raise ValueError(f'Diagnostic override input scope changed: {rule}')
    for component in config['components'].values():
        overrides(component)
    for vendor in config['cloud'].values():
        for product in vendor['products'].values():
            overrides(product)


def object_context(arch, node):
    if isinstance(node, str):
        node = arch.nodes.get(node)
    if node is None:
        return dict(name=arch.name, component='architecture', provider='unspecified', product='unspecified'), {}, 'generic'
    owner = node
    if not owner.properties.get('product') and owner.parent:
        owner = arch.nodes[owner.parent]
    if not owner.properties.get('product'):
        hosts = arch.outgoing(owner.name, 'hosted_on')
        if len(hosts) == 1:
            owner = arch.nodes[hosts[0].target]
    provider = owner.properties.get('provider', 'unspecified')
    product = owner.properties.get('product', 'unspecified')
    profile = arch.cloud.get(provider, {}).get('products', {}).get(product, {})
    return dict(name=node.name, component=node.kind, provider=provider, product=product), profile, owner.name


def describe(arch, rule, node=None, *, _scopes=None, **values):
    context, profile, owner = object_context(arch, node)
    try:
        definition, origin = resolve_definition(arch, rule, node)
        scopes = {'node': node.properties if hasattr(node,'properties') else {}}
        scopes.update(_scopes or {})
        missing = [binding['label'] for binding in definition.get('inputs', {}).values()
                   if binding['field'] not in scopes.get(binding['scope'], {}) or scopes[binding['scope']][binding['field']] is None]
        context.update(model=definition.get('model',rule.lower()),missing_fields=', '.join(missing))
        message = definition['message'].format(**context, **values)
        return DiagnosticDetail(message, dict(rule=rule, object=context['name'], component=context['component'],
            provider=context['provider'], product=context['product'], model=context['model'],
            definition_owner=owner, template_origin=origin, missing_fields=missing, parameters=values))
    except (KeyError, ValueError, TypeError) as exc:
        # Configuration is normally validated before compilation; keep direct API
        # users from turning malformed templates into an unhandled exception.
        return DiagnosticDetail(f'Cannot render diagnostic {rule}: {exc}', {'rule':rule,'configuration_error':True})


def require_inputs(arch, rule, node, fail, **scopes):
    """Resolve a named model's input bindings without embedding field names in prose."""
    scopes = {'node':node.properties, **scopes}
    definition, _ = resolve_definition(arch, rule, node)
    inputs = {name: scopes.get(binding['scope'], {}).get(binding['field'])
              for name,binding in definition['inputs'].items()}
    if any(value is None for value in inputs.values()):
        fail('SCALING_MODEL_INCOMPLETE', detail=describe(arch,rule,node,_scopes=scopes))
    return inputs


def error_override(arch, rule, values, subject=None):
    """Resolve full error templates using the same product/component precedence."""
    detail_context = getattr(values.get('detail'), 'context', {})
    if subject is None:
        subject = detail_context.get('object')
    if subject is None:
        for key in ('node_name','node','resource','database_name','host_name','name','target','current'):
            candidate=values.get(key)
            if isinstance(candidate,str) and candidate.split(':')[0] in arch.nodes:
                subject=candidate.split(':')[0]
                break
    context,profile,_=object_context(arch,subject)
    context.update(model=detail_context.get('model',''),missing_fields=', '.join(detail_context.get('missing_fields',[])))
    component=arch.component_definitions.get(context['component'],{})
    selected=profile.get('diagnostics',{}).get(rule,component.get('diagnostics',{}).get(rule))
    return selected,context


def diagnostic_context(arch, rule, values, subject=None):
    detail = getattr(values.get('detail'), 'context', None)
    if detail is not None:
        return {**detail, 'error_rule':rule}
    if arch is None:
        return None
    if subject is None:
        for key in ('node_name','node','resource','database_name','host_name','name','target','current'):
            candidate=values.get(key)
            if isinstance(candidate,str) and candidate.split(':')[0] in arch.nodes:
                subject=candidate.split(':')[0]
                break
    context,profile,owner=object_context(arch,subject)
    origin='product' if rule in profile.get('diagnostics',{}) else 'component' if rule in arch.component_definitions.get(context['component'],{}).get('diagnostics',{}) else 'generic'
    return dict(error_rule=rule, object=context['name'], component=context['component'],
                provider=context['provider'], product=context['product'], definition_owner=owner,
                template_origin=origin, parameters=values)


def resolve_definition(arch, rule, node):
    context, profile, _ = object_context(arch, node)
    definition = dict(arch.diagnostic_details[rule])
    definition['inputs'] = dict(definition.get('inputs', {}))
    origin = 'generic'
    for level, owner in (('component', arch.component_definitions.get(context['component'], {})), ('product', profile)):
        override = owner.get('diagnostics', {}).get(rule, {})
        if 'message' in override:
            definition['message'] = override['message']
            origin = level
        definition['inputs'].update(override.get('inputs', {}))
    return definition, origin
