# CockroachDB vendor configuration

Available in Polish 0.14.0 and later. Upgrade with
`python -m pip install --upgrade polish-lang`, or run from source as shown below.

Use `provider = cockroachdb` to select the separate vendor catalog in
[`polish/config/vendors/cockroachdb/databases.json`](../polish/config/vendors/cockroachdb/databases.json).
The older `provider = self_hosted product = cockroachdb` profile remains supported
unchanged. It does not acquire the new vendor-specific database settings.

## Try the example

From the repository root:

```sh
.venv/bin/python -m polish simulate examples/cockroachdb.polishd
.venv/bin/python -m polish simulate examples/cockroachdb.polishd --json
```

The [example](../examples/cockroachdb.polishd) runs five deterministic scenarios:
self-managed strong read, replicated write, self-managed overload, cloud read, and
cloud overload. The two expected capacity failures count as passing tests. No live
CockroachDB cluster, model API, or deployment is involved.

## Profiles

| Provider/product | Description | Required inputs |
| --- | --- | --- |
| `cockroachdb / cockroachdb` | Self-managed distributed SQL | `nodes`; explicit `max_ops_rps` when evaluating rate-based database traffic |
| `cockroachdb / cockroachdb_cloud` | Managed SQL service, without tier assumptions | Explicit `max_ops_rps` when evaluating rate-based database traffic; no user-managed nodes or compute host |

Both profiles default to `engine = cockroachdb`, persistent storage, and TLS.
Only relational logical databases are supported. `cockroachdb_cloud` requires TLS;
the self-managed profile allows explicit `tls = false` for a modeled insecure setup.
Wire connections use `protocol = tls` or, for self-managed only, `tcp`. PostgreSQL wire
compatibility does not mean CockroachDB is PostgreSQL or supports every PostgreSQL
extension. Do not change its engine to `postgres`.

```polish
database_cluster Cluster {
  provider = cockroachdb product = cockroachdb
  nodes = 5 max_ops_rps = 100
}
database Store {
  kind = relational
  hosted_on Cluster
  regions = 3 survival_goal = region replication_factor = 5
  isolation_level = serializable
  max_connections = 100 reserved_connections = 10
  backups = true backup_retention_days = 7
  table Items { id: uuid primary_key }
}
```

Numbers are synthetic inputs, not vendor benchmarks. `max_ops_rps` is one shared
cluster budget: all modeled read/write dependency visits are charged to it. It is
not multiplied by `nodes`; more nodes do not prove higher capacity. Connection
budgets continue to use declared application instance counts, pools, and database
limits. A missing operations budget yields `SCALING_MODEL_INCOMPLETE` when needed.
Generic Postgres CPU/replica estimates (`cpu_ms` on database edges, `read_from`,
`read_replicas`, cluster `replicas`, `instance_type`, and `cpu_utilization`) are
rejected for these profiles. Application operation `cpu_ms` is still supported.

## Database declarations and checks

| Setting | Default | What Polish checks |
| --- | --- | --- |
| `replication` | `synchronous` | Other modes rejected |
| `replication_factor` | `3` | Declared voting replicas per range; positive integer, sufficient for the survival goal, no more than self-managed `nodes` |
| `write_ack` | `quorum` | Other modes rejected; write trace reports majority acknowledgement count |
| `durability` | `replicated` | Write requirements checked using the existing durability model |
| `regions` | `1` | Positive declared count of database regions |
| `survival_goal` | `none` | Polish-specific `none` means no survival claim; `zone` needs at least three voting replicas; `region` needs at least three regions and five voting replicas |
| `isolation_level` | `serializable` | `serializable` and `read_committed` accepted as declarations; transaction anomalies and retries are not simulated |
| `backups`, `backup_retention_days` | No backup policy inferred | Existing policy checks require a positive retention value with enabled backups |

`survival_goal = none` is a modeling choice, not a CockroachDB SQL survival mode.
Survival goals and region counts belong to the logical database, not the cluster.
These checks establish necessary minimum declarations only. They do not check replica
placement across zones/regions, non-voting replicas, leaseholder placement, system
ranges, or actual outage recovery. Five replicas in one failure domain are not safe
just because the counts pass. Single-node development mode is not covered by this
quorum/durability profile. `replication_factor` is an abstraction for voting replicas,
not all copies including non-voters.

Reads default to a strong-consistency declaration and traces identify a distributed
SQL endpoint. This is not SQL execution or a proof of serializability. The generic
connection-budget resource label may still use `primary`; it represents the modeled
SQL connection budget, not a PostgreSQL-style primary/standby topology.

## Other CockroachDB features

The vendor JSON includes a capability inventory. Features are separated by whether
the simulator implements them; unsupported features are not accepted as pretend
working flags in `.polishd` files.

- Multi-region global, regional-by-table, and regional-by-row localities: documented
  product capabilities; row placement and latency are not simulated.
- Follower reads: both strong and stale forms exist; timestamp/closed-timestamp and
  locality behavior are not modeled. `read_from = replica` is not a substitute.
- Changefeeds/CDC: delivery, ordering, sinks, and backpressure are not modeled.
- Online schema changes, vector indexes, SQL query plans, transaction retry behavior,
  backup execution and point-in-time restore: not modeled.
- Cloud editions, licensing, cost, and guaranteed performance/availability: not
  inferred from the product name. The generic cloud profile deliberately does not
  encode changing commercial tiers.

## Customize through configuration

Copy the complete bundled configuration, then edit the vendor JSON or restricted
rules and pass `--config-dir`:

```sh
cp -R polish/config /tmp/polish-cockroach-config
.venv/bin/python -m polish simulate examples/cockroachdb.polishd \
  --config-dir /tmp/polish-cockroach-config
```

`vendors.json` registers the vendor file. Product defaults, numeric types, allowed
protocols, forbidden properties, `database_defaults`, `database_enums`, and
`survival_goals` reside in the catalog. Minimums are validated as positive integers.
Changing an assumption in a private config does not change the actual database's
capabilities. Cross-property validation lives in `rules/cloud_products.rules`;
messages live in `diagnostics.json`. No Python edits are needed to change those
configured rules or limits. Unknown enum values and malformed catalog metadata fail
validation rather than silently being treated as supported features.

## Sources and limits

Catalog verification date: 2026-10-01. Primary sources:

- [Developer basics: PostgreSQL wire compatibility, isolation, retries](https://www.cockroachlabs.com/docs/stable/developer-basics)
- [Multi-region concepts and database survival goals](https://www.cockroachlabs.com/docs/stable/multiregion-overview)
- [Region-survival replica and region minimums](https://www.cockroachlabs.com/blog/build-a-highly-available-multi-region-database/)
- [Replication architecture](https://www.cockroachlabs.com/docs/stable/architecture/replication-layer)
- [Follower reads](https://www.cockroachlabs.com/docs/stable/follower-reads)
- [Change data capture](https://www.cockroachlabs.com/docs/stable/change-data-capture-overview)

Passing a Polish scenario validates the supplied architecture assumptions. It does
not provision CockroachDB, execute SQL, validate a cloud plan entitlement, or measure
production performance.
