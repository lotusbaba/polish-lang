"""Polish storage contracts, deliberately independent of vendor defaults."""


def copies(properties):
    return properties.get("replication_factor", 1 + properties.get("read_replicas", 0))


def validate_storage(arch, error, config):
    enums = config["storage_enums"]
    for node in arch.nodes.values():
        p = node.properties
        invalid = False
        for key, val in p.items():
            if key in enums and val not in enums[key]:
                error('E_STORAGE_VALUE', line=node.line, key=key, node_name=node.name, val=val)
                invalid = True
            if key in config["positive_integers"] and (type(val) is not int or val < 1):
                error('E_SCALE_4', line=node.line, key=key)
                invalid = True
        if node.kind == "keyspace" and not {"key_type", "value_type"} <= p.keys():
            error('E_KEYSPACE', line=node.line, node_name=node.name)
        if node.kind != "database" or invalid or type(p.get("read_replicas", 0)) is not int:
            continue
        count = copies(p)
        if p.get("read_replicas", 0) >= count:
            error('E_REPLICATION', line=node.line)
        if p.get("failure_domains", 1) > count:
            error('E_REPLICATION_2', line=node.line)
        if p.get("durability") == "replicated" and (count < 2 or p.get("replication") != "synchronous" or p.get("write_ack") not in {"quorum", "all"}):
            error('E_DURABILITY', line=node.line)
        if p.get("write_ack") in {"quorum", "all"} and (count < 2 or p.get("replication") != "synchronous"):
            error('E_REPLICATION_3', line=node.line)
        if "backup_retention_days" in p and p.get("backups") is not True:
            error('E_BACKUP', line=node.line)
        if p.get("backups") is True and "backup_retention_days" not in p:
            error('E_BACKUP_2', line=node.line)
        hosts = arch.outgoing(node.name, "hosted_on")
        if len(hosts) == 1:
            hp = arch.nodes[hosts[0].target].properties
            if p.get("durability") in {"disk", "replicated"} and hp.get("storage") != "persistent":
                error('E_DURABILITY_2', line=node.line, node_name=node.name)
            for key, required in (("copies", count), ("failure_domains", p.get("failure_domains", 1))):
                available = hp.get(key)
                if type(available) is int and required > available:
                    error('E_CAPACITY_3', line=node.line, node_name=node.name, required=required, key=key, available=available)

    for edge in arch.edges:
        p = edge.properties
        allowed = set(config["connection_options"].get(edge.kind, []))
        for key in p.keys() - allowed:
            error('E_CONNECTION', line=edge.line, key=key, edge_kind=edge.kind)
        if p and arch.nodes[edge.source].kind in {"page", "frontend"}:
            error('E_CONNECTION_2', line=edge.line)
        protocols = config["connection_protocols"].get(edge.kind, [])
        target = arch.nodes[edge.target]
        database = target if target.kind == "database" else arch.nodes.get(target.parent)
        hosts = arch.outgoing(database.name, "hosted_on") if database else []
        if hosts and arch.nodes[hosts[0].target].properties.get("product") == "dynamodb":
            protocols = arch.aws["products"]["dynamodb"]["protocols"]
        from .vendors import product_profile
        if hosts and product_profile(arch, arch.nodes[hosts[0].target]).get("protocols"):
            protocols = product_profile(arch, arch.nodes[hosts[0].target])["protocols"]
        if target.kind == "artifact_store":
            protocols = product_profile(arch, target).get("protocols", ["http", "https"])
        if "protocol" in p and p["protocol"] not in protocols:
            error('E_CONNECTION_3', line=edge.line, edge_kind=edge.kind, sorted_protocols=sorted(protocols))
        via = arch.nodes.get(p.get("via"))
        if via and via.kind not in config["connection_entries"]:
            error('E_CONNECTION_4', line=edge.line, via_kind=via.kind)
        for key, values in config["connection_enums"].items():
            if key in p and p[key] not in values:
                error('E_CONNECTION_6', line=edge.line, key=key, p_key=p[key])
        if "path" in p and (not isinstance(p["path"], str) or not p["path"].startswith("/")):
            error('E_CONNECTION_5', line=edge.line)
