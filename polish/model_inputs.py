"""Resolve configured DSL fields into semantic inputs for simulation formulas."""
import math

from .diagnostics import object_context, describe


def validate_model_inputs(config, baseline):
    catalog = config['model_inputs']
    if not isinstance(catalog, dict) or catalog.keys() != baseline.keys():
        raise ValueError('Model input definitions must preserve the supported models')

    def validate(model, bindings, partial=False):
        expected = baseline[model]
        if not isinstance(bindings, dict) or set(bindings) - expected.keys() or (not partial and bindings.keys() != expected.keys()):
            raise ValueError(f'Invalid input contract for {model}')
        for name, binding in bindings.items():
            if not isinstance(binding, dict) or set(binding) != {'scope', 'field', 'default'}:
                raise ValueError(f'Invalid model input {model}.{name}')
            if binding['scope'] != expected[name]['scope'] or not isinstance(binding['field'], str) or not binding['field'].isidentifier():
                raise ValueError(f'Invalid input scope or field for {model}.{name}')
            default = binding['default']
            if default is not None and (type(default) not in (int, float) or not math.isfinite(default) or default < 0):
                raise ValueError(f'Invalid numeric default for {model}.{name}')
            # A missing required value must not silently acquire a guessed default.
            if (default is None) != (expected[name]['default'] is None):
                raise ValueError(f'Cannot change optionality of {model}.{name}')

    for model, bindings in catalog.items():
        validate(model, bindings)
    owners = list(config['components'].values())
    owners += [p for vendor in config['cloud'].values() for p in vendor['products'].values()]
    for owner in owners:
        overrides = owner.get('model_inputs', {})
        if not isinstance(overrides, dict) or set(overrides) - catalog.keys():
            raise ValueError('Unknown model input override')
        for model, bindings in overrides.items():
            validate(model, bindings, partial=True)


class ModelInputs:
    def __init__(self, arch, model, node, request, fail):
        self.arch, self.model, self.node, self.fail = arch, model, node, fail
        context, profile, _ = object_context(arch, node)
        self.bindings = dict(arch.model_inputs[model])
        for owner in (arch.component_definitions.get(context['component'], {}), profile):
            self.bindings.update(owner.get('model_inputs', {}).get(model, {}))
        self.scopes = {'node': node.properties, 'request': request}

    def field(self, name):
        return self.bindings[name]['field']

    def present(self, name):
        binding = self.bindings[name]
        return binding['field'] in self.scopes[binding['scope']]

    def get(self, name):
        binding = self.bindings[name]
        value = self.scopes[binding['scope']].get(binding['field'], binding['default'])
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            self.fail('SCALING_MODEL_INCOMPLETE', detail=describe(
                self.arch, 'MODEL_INPUT_INVALID', self.node,
                field=binding['field'], input_name=f'{self.model}.{name}'))
        return value
