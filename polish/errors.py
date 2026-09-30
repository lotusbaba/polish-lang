"""Render diagnostic definitions without evaluating configuration expressions."""
from string import Formatter


def validate_errors(catalog):
    if not isinstance(catalog, dict) or not catalog:
        raise ValueError("errors must be a nonempty object")
    for rule, entry in catalog.items():
        if not isinstance(entry, dict):
            raise ValueError(f"Error definition {rule} must be an object")
        if not isinstance(entry.get("code"), str) or not entry["code"]:
            raise ValueError(f"Error definition {rule} requires a code")
        if entry.get("phase") not in {"compile", "simulation", "cli", "assertion"}:
            raise ValueError(f"Invalid error phase for {rule}")
        if entry["phase"] == "simulation" and entry.get("outcome") not in {"error", "denied"}:
            raise ValueError(f"Invalid failure outcome for {rule}")
        params = entry.get("parameters")
        if not isinstance(params, list) or not all(isinstance(p, str) and p.isidentifier() for p in params):
            raise ValueError(f"Invalid error parameters for {rule}")
        if not isinstance(entry.get("message"), str):
            raise ValueError(f"Error definition {rule} requires a message")
        for _, field, spec, conversion in Formatter().parse(entry["message"]):
            if field is not None and (field not in params or spec or conversion not in {None, "s", "r"}):
                raise ValueError(f"Unsupported template placeholder in {rule}: {field}")


def render_error(catalog, rule, **values):
    try:
        entry = catalog[rule]
        return entry["code"], entry["message"].format(**values)
    except (KeyError, ValueError, TypeError) as exc:
        # This must work even when the bundled catalog itself has been edited.
        return "E_CONFIG", f"Cannot render error definition {rule}: {exc}"
