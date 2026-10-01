"""Cross-resource contracts for managed topics, streams, and Cloud SQL."""
from .scaling import number


def validate_cloud_products(arch, error):
    def invalid(node, message):
        error('E_VENDOR', line=node.line, detail=f'{node.name}: {message}')
    for node in arch.nodes.values():
        p = node.properties
        provider, product = p.get('provider'), p.get('product')
        if provider == 'aws' and product == 'kinesis':
            if p.get('capacity_mode') == 'provisioned':
                if 'shards' not in p:
                    invalid(node, 'provisioned Kinesis requires shards')
                if 'max_messages_rps' in p:
                    invalid(node, 'provisioned Kinesis uses messages_per_shard_rps, not max_messages_rps')
            elif any(k in p for k in ('shards', 'messages_per_shard_rps')):
                invalid(node, 'on-demand Kinesis manages shards; declare max_messages_rps for simulation')
        if provider == 'gcp' and product == 'pubsub_subscription':
            incoming = arch.incoming(node.name, 'delivers_to')
            if len(incoming) != 1 or arch.nodes[incoming[0].source].properties.get('product') != 'pubsub_topic':
                invalid(node, 'a Pub/Sub subscription requires exactly one Pub/Sub topic delivers_to relationship')
        if node.kind == 'database':
            hosts = arch.outgoing(node.name, 'hosted_on')
            if len(hosts) == 1:
                host = arch.nodes[hosts[0].target]
                if host.properties.get('provider') == 'gcp' and host.properties.get('product') == 'cloudsql_postgres':
                    if number(p.get('read_replicas', 0)) and p.get('read_replicas', 0) > 0:
                        p.setdefault('replication', 'asynchronous')
                        if p['replication'] != 'asynchronous':
                            invalid(node, 'Cloud SQL read replicas are asynchronous')
    for edge in arch.edges:
        src, dst = arch.nodes[edge.source], arch.nodes[edge.target]
        sp, dp = src.properties, dst.properties
        if edge.kind == 'delivers_to':
            pair = (sp.get('provider'), sp.get('product'), dp.get('provider'), dp.get('product'))
            if pair not in {('aws', 'sns', 'aws', 'sqs'), ('gcp', 'pubsub_topic', 'gcp', 'pubsub_subscription')}:
                invalid(src, 'supported deliveries are SNS to SQS and Pub/Sub topic to Pub/Sub subscription')
            if sp.get('product') == 'sns' and sp.get('topic_type') == 'standard' and dp.get('queue_type') == 'fifo':
                invalid(src, 'standard SNS topics cannot deliver to FIFO SQS queues')
        if edge.kind == 'publishes_to' and dp.get('provider') == 'gcp' and dp.get('product') == 'pubsub_subscription':
            invalid(dst, 'publish to the Pub/Sub topic, not its subscription')
