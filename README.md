<p align="center">
  <img src="https://raw.githubusercontent.com/lotusbaba/polish-lang/main/assets/polish-logo.png" alt="Polish logo: a shining navy leather boot with a gold sparkle" width="160" height="160">
</p>

# Polish

Polish is a declarative architecture language for people and LLMs. Describe
pages, services, data stores, and infrastructure in a `.polishd` file, check
their relationships, then run request scenarios against the resulting graph.

This repository contains version 0.11: a Python compiler and
deterministic functional simulator. It does not provision infrastructure or
send real network requests.

## Problems Polish helps solve

### Reuse an architecture when the use case changes

You have been vibe coding an ecommerce app with an LLM and refining its
architecture along the way. Now you want to reuse that design for a ticketing
site: keep the account and checkout flows, replace the catalog with events,
and change the storage and access rules.

A `.polishd` file gives you and the LLM an explicit starting point. Copy the
specification, change the components and relationships, then rerun the scenarios.
The compiler checks the declared relationships; simulations show whether requests
still reach the intended services and data stores under the new assumptions.
Keep scenarios for behavior that must survive the change, such as preventing a
regular user from reaching an admin page.

Polish currently checks specifications you or an LLM write. It does not extract
architecture from application code, generate the application, or verify that a
deployment matches the specification.

### Catch an infrastructure choice that conflicts with the design

You add path-based routing for `/api/*`, then discover that your load balancer is
an AWS Network Load Balancer. Polish rejects path rules on its NLB profile at
compile time:

```polish
load_balancer Ingress {
  provider = aws
  product = nlb
  route "/api/*" to Apps
}
```

With `Apps` declared in the surrounding architecture, this produces `E_VENDOR`:
`NLB cannot inspect HTTP paths; use routes_to`. One solution is to change the
component to `product = alb`, configuring TLS termination if it accepts HTTPS.
Another is to keep the NLB forwarding traffic and put the path rules in an HTTP
router behind it. AWS documents the listener capabilities for
[ALBs](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-listeners.html)
and [NLBs](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/load-balancer-listeners.html).

### Test capacity assumptions before provisioning

Declare a workload and compare it with your capacity assumptions: request rate,
CPU cost, instance counts, connection limits, database throughput, or queue
consumer rate. Scenarios can expose a service that needs more instances, a queue
whose backlog grows, or a data store whose declared request budget is exceeded.
Change the design and rerun the same workload to compare outcomes. See
[scaling](examples/scaling.polishd), [resource budgets](examples/budgets.polishd),
and [AWS processing](examples/aws-processing.polishd) for examples.

These are deterministic models of declared assumptions, not cloud benchmarks.
A passing scenario means the modeled request satisfies the modeled constraints;
it does not establish production performance or reliability.

## Run it

Requires Python 3.11 or newer.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

polish check examples/shop.polishd
polish simulate examples/shop.polishd
polish simulate examples/shop.polishd --scenario "Product page calls the API end to end"
polish simulate examples/shop.polishd --json
pytest -q
```

After installing, without activating the virtual environment, use `.venv/bin/polish` and `.venv/bin/python -m pytest`.
`python -m polish` is also supported.

If editable installation is unavailable in your environment, use a regular
installation (`python -m pip install .`). When editing the compiler, use `.venv/bin/python -m polish` from the project
root to run the current source, or reinstall with `python -m pip install .` to
refresh the installed `polish` command.

Commands return exit code `0` when checks or scenario expectations pass, and `1`
for compilation errors, failed expectations, unreadable input, or no matching
scenarios. An expected denial or expected TLS error is a passing scenario.
`--json` emits a single JSON object with `ok`, `diagnostics`, and `results`.

## Example

```polish
architecture CatalogSite {
  artifact_store Assets { kind = object_store tls = true }

  frontend Site {
    rendering = static
    loaded_from Assets
    page Product
    Product invokes Catalog.get_product
  }

  cdn Edge {
    tls_termination = true
    route "/api/*" to Balancer
  }
  load_balancer Balancer { routes_to Servers }
  compute_pool Servers { instances = 2..10 }

  service Catalog {
    hosted_on Servers
    operation get_product {
      method = GET
      path = "/api/products/*"
      reads Products.Items
    }
  }

  database Products {
    kind = relational
    read_replicas = 2
    hosted_on Storage
    table Items { id: uuid primary_key name: string }
  }
  database_cluster Storage { engine = postgres replicas = 2 }
}

scenario "Load product details" {
  request {
    entry = Site.Product
    action = Catalog.get_product
    via = Edge
    protocol = https
    method = GET
    path = "/api/products/123"
  }
  expect {
    outcome = success
    reaches Catalog.get_product
    accesses Products.Items
  }
}
```

This scenario loads the page from its artifact store, checks that the page may
invoke the operation, and traverses the API route to the database access. Page
loading alone does not automatically invoke every service the page is allowed
to use. `action` selects an operation and `via` selects its network entry point.

See [the ecommerce example](examples/shop.polishd) for navigation, checkout,
authorization, key-value storage, and nine executable scenarios.
[The invalid example](examples/invalid-loading.polishd) demonstrates the
compiler rejecting two loading sources.

## Language rules

Each file contains one `architecture Name { ... }` followed by zero or more
`scenario "Name" { ... }` blocks. Identifiers are case-sensitive. Properties use
`=`, relationships use named verbs, and comments start with `//` or `#`.
Values include quoted strings, bare symbols, numbers, booleans, and inclusive
integer ranges such as `2..10`. General variables, expressions, imports, and
user-defined types are not implemented yet.

Component kinds are built in. Nesting means containment: a page belongs to a
frontend; it does not inherit from the frontend as a class. Pages inherit their
frontend's rendering settings, loading source, and access restrictions.

| Component | Allowed children | Selected properties |
| --- | --- | --- |
| `frontend` | `page` | `rendering = static` or `server` |
| `service` | `operation` | `tls` |
| `database` | `table` for relational; `document` or `collection` for document storage; `keyspace` for KV | `kind`, `read_replicas`, `partitions`, durability and replication settings |
| `artifact_store` | none | `kind = object_store` or `artifactory`, `tls` |
| `cdn` | none | `tls`, `tls_termination`, `upstream_protocol`, `request_collapsing`, `shielding`, `rate_limiting`, `bot_mitigation` |
| `gateway` | none | `tls`, `tls_termination`, `upstream_protocol`, `rate_limiting` |
| `load_balancer` | none | `tls`, `tls_termination`, `upstream_protocol` |
| `cache` | none | `tls`, `tls_termination`, `upstream_protocol`, `ttl` |
| `compute_pool` | none | `instances = 2..10`, `tls` |
| `database_cluster` | none | `engine`, `replicas`, `partitions`, `copies`, `failure_domains`, `storage`, `tls` |
| `keyspace` | none | `key_type`, `value_type` |
| `role` | none | none |

An operation can declare `method` and `path` together to expose an HTTP endpoint.
Methods are GET, POST, PUT, PATCH, DELETE, HEAD, and OPTIONS. A service without
HTTP operations can serve as a page renderer or a logical service dependency.
Fields support `uuid`, `string`, `decimal`, `integer`, `boolean`, `datetime`, and
`json`, with optional `primary_key` metadata. No records or queries are executed.

### Relationships

```polish
Product navigates_to Cart
Product invokes Catalog.get_product
```

Inside a component, omit the source to refer to that component:

```polish
service Catalog {
  hosted_on Servers
  operation get_product { reads Products.Items }
}
```

References resolve from the enclosing scope outward, then at the architecture
root. Use qualified names such as `Site.Product` or `Catalog.get_product` in
scenarios.

| Relationship | Source → target |
| --- | --- |
| `navigates_to` | page → page |
| `invokes` | frontend, page, service, operation → service, operation |
| `reads`, `writes` | service, operation → database, table, document, collection, keyspace, cache |
| `loaded_from` | frontend, page → artifact store for static rendering; service or load balancer for server rendering |
| `hosted_on` | service, self-hosted cache, self-hosted gateway → compute pool; database → database cluster |
| `routes_to` | CDN, gateway, load balancer, cache → network component, compute pool, service, operation, artifact store, database, database cluster |

Each frontend needs exactly one loading source. A page can override its inherited
source with exactly one local source. Two local sources are an error, even if
they point to the same component. Each service, database, and self-hosted cache needs exactly
one compatible host. Managed cache profiles omit the host. Services cannot invoke frontends; returning a response is
implicit and does not require an invocation edge.

`route "/api/*" to Gateway` is a path-specific routing edge. Patterns use shell
wildcards (`*`, `?`, `[abc]`), with `*` also matching slashes. The route with the
longest literal prefix wins; equally specific matches fail as ambiguous.
`routes_to Target` is a fallback used when no explicit route matches.

At a compute pool, the simulator selects an operation from its hosted services
using the request method and path. An exact endpoint match wins over wildcard
matches; multiple remaining matches fail. Explicit routing to an operation
selects that operation directly.

### Access and TLS

```polish
page Checkout { requires authenticated }
page AdminPanel { requires role Admin }
```

Requirements also work on frontends, services, and operations. Parent
requirements apply to children. Role requirements need both
`authenticated = true` and the matching `actor`. Requests are anonymous by
default; specifying a role alone does not authenticate a request. All declared
requirements must be satisfied. Only one actor role is supported per request.

Network components accept HTTP by default. HTTPS requires `tls = true` or
`tls_termination = true`. A forwarding component that terminates TLS defaults to
HTTP upstream; `upstream_protocol = https` starts another TLS connection. Without
termination, HTTPS is preserved; changing it to HTTP causes
`TLS_TERMINATION_REQUIRED`. TLS support must be declared on every modeled
receiver of an HTTPS network hop.

### Scenarios

A request can describe one of three flows:

- A network request: `entry = Edge`, `method = GET`, `path = "/api/products/123"`.
- Navigation: `entry = Site.Landing`, `destination = Site.Checkout`.
- A frontend action: `entry = Site.Product`, `action = Catalog.get_product`,
  `via = Edge`, plus the HTTP method and path.

`protocol` defaults to `https`, `method` to `GET`, and `path` to `/`. Navigation
finds an authorized path through declared links and loads the pages along it.
Actions and navigation are separate scenarios in this version.

Expectations require `outcome = success`, `denied`, or `error`. Optionally specify
an exact error code with `error = TLS_UNSUPPORTED`. Add `reaches Component` and
`accesses Database.Table` assertions to check the trace. Assertions also apply to
expected-error scenarios: they are not skipped on failure.

### Service and database connections

Service and operation dependencies traverse a connection path before execution.
Use an optional block on `invokes`, `reads`, or `writes`:

```polish
invokes Inventory.reserve { via = InternalLB protocol = https }
reads Products.Items {
  via = DataLB
  protocol = tls
  path = "/products"
  read_from = replica
  consistency = strong
}
writes Orders.Purchases { protocol = tls durability = replicated }
```

`via` starts the connection at a gateway, load balancer, or another permitted
network entry. Routing must reach the target workload or its declared host;
reaching a different host fails with `WRONG_DESTINATION`. Calls without `via`
connect directly to the target's host. The compiler still accepts existing bare
relationships: service calls default to `http`, database calls to `tcp`.

Service calls accept `http` or `https`; database calls accept `tcp` or `tls`
(encrypted TCP). Both the receiving host and workload must declare TLS support
when encryption reaches them. Forwarders can terminate TLS and optionally
re-encrypt upstream. Protocol families cannot change from HTTP to TCP or vice
versa. CDN and gateway nodes only support HTTP-family connections.

Connection `path` selects an infrastructure route. For service calls it defaults
to the target operation's declared path, otherwise `/`. For database connections
it is a logical routing label, not a SQL query or an actual TCP URL. Dependency
routing targets the named operation, rather than redispatching the caller's HTTP
method/path against the callee. A service target executes its service-level
requirements and dependencies; name an operation to execute that operation.

Each connection has its own routing-cycle check, so two services can legitimately
share a pool. Recursive execution still fails with `REQUEST_CYCLE`. Calls retain
the scenario identity. Service-level dependencies run before operation-level
ones, in declaration order, and an error stops subsequent execution. Data access
is only recorded after transport and read/write requirements pass.

See [storage.polishd](examples/storage.polishd) for a checkout that calls inventory
through an internal load balancer, then uses all three database kinds. It also
contains expected failures for missing TLS, inconsistent replica reads, and
insufficient write durability:

```sh
python -m polish simulate examples/storage.polishd
```

### Scale, storage, and durability

These properties define Polish contracts, not vendor-specific guarantees.

| Database kind | Data children | Partitioning |
| --- | --- | --- |
| `relational` | `table` | Optional count or `managed` |
| `document` | `document`, `collection` | Optional count or `managed` |
| `key_value` | `keyspace` | Optional count or `managed` |

A keyspace requires `key_type` (`string`, `integer`, `uuid`) and `value_type`
(`string`, `integer`, `decimal`, `boolean`, `json`, `bytes`). The compiler rejects
children incompatible with their database kind. A database may also be accessed
directly without declaring children.

```polish
database Sessions {
  kind = key_value
  partitions = managed
  replication_factor = 3
  read_replicas = 2
  replication = asynchronous
  durability = disk
  hosted_on SessionStorage
  keyspace Tokens { key_type = string value_type = json }
}
database_cluster SessionStorage {
  storage = persistent
  replicas = 2
  copies = 3
  partitions = managed
}
```

- `instances` accepts a positive count or ordered positive range. `partitions`
  accepts a positive count or `managed`; omission means unspecified.
- `replication_factor` counts all copies **per partition**, including the primary.
  It defaults to `1 + read_replicas`. `read_replicas` defaults to zero and cannot
  exceed the total copies minus the primary. Copies need not all serve reads.
- Host `replicas` is read-replica capacity; host `copies` is total copy capacity per
  partition. Explicit host counts bound the database's requested counts. Managed
  partition counts are opaque and are not compared numerically.
- Database `failure_domains` declares the number of distinct placement domains.
  It cannot exceed the copy count or an explicitly declared host domain capacity.
  It does not promise survival under a particular failure; placement and quorum
  availability are not simulated.
- `durability = memory` (the default) promises no persistent write acknowledgement.
  `disk` requires `storage = persistent` on the host.
- `durability = replicated` requires persistent host storage, at least two copies,
  `replication = synchronous`, and `write_ack = quorum` or `all`. Quorum means
  `floor(copies / 2) + 1` acknowledgements; all means every copy. `primary` is the
  default acknowledgement policy. Quorum/all policies require synchronous
  replication and at least two copies even when durability is not `replicated`.
- `backups = true` requires a positive `backup_retention_days`. Retention without
  enabled backups is an error. This validates a backup policy; it does not execute
  backup or restore jobs or guarantee an RPO/RTO.

A write may require a minimum durability using `writes Store { durability = disk }`.
The simulator checks the ordering `memory < disk < replicated` and traces the
number of configured acknowledgements. Copies alone do not imply persistent
storage, synchronous replication, or successful backup.

Reads default to `read_from = primary` and `consistency = eventual`. A replica read
requires at least one declared read replica. In this simplified contract,
`consistency = strong` on a replica requires synchronous replication; a primary
read is accepted. This models a declared capability, not a proof of database
linearizability, transactions, isolation, or actual replication lag.

Missing host capacity means unspecified, not zero. Checks apply per database;
shared-host resource reservations and aggregate capacity are not modeled yet.

## AWS CPU capacity scenarios

Run `.venv/bin/python -m polish simulate examples/scaling.polishd` to see both
service and PostgreSQL overloads, with explicit arithmetic in the trace.
Add `--json` for structured per-resource `capacity` results.

The initial AWS catalog in `polish/config/vendors/aws/ec2.json` supports EC2 `m7i.large`,
`m7i.xlarge`, and `m7i.2xlarge`. vCPU and memory figures are sourced from
[AWS's instance specifications](https://docs.aws.amazon.com/ec2/latest/instancetypes/gp.html).
These EC2 profiles support services and self-managed PostgreSQL. RDS uses separate
`db.*` profiles described below.
There is no AWS account access, deployment, pricing, or region-availability lookup.
Memory specifications also support the optional service memory model below.

```polish
compute_pool AppPool {
  provider = aws
  instance_type = "m7i.large"
  instances = 1..4
  cpu_utilization = 0.7
}
```

Declare `cpu_ms = 10` on an operation, or on its service as a fallback. An operation
cost replaces its service fallback; it does not add to it. This represents total
vCPU-milliseconds per invocation, not elapsed request latency. Explicit zero is
allowed; an omitted cost is not silently assumed to be zero.

In the scenario request, set `rate_rps = 200` and optionally `scale = minimum` or
`maximum`. The default is minimum: a `1..4` pool uses one running instance.
Maximum evaluates the same workload with four instances, without simulating
autoscaling decisions or startup delays. Fixed instance counts are unchanged.

```text
demand   = requests_per_second × CPU_ms_per_request
capacity = running_instances × vCPUs_per_instance × 1000 × cpu_utilization
required_instances = ceil(demand / capacity_per_instance)
```

For the example: `200 × 10 = 2000 CPU-ms/s`, while one 2-vCPU instance at a 70%
budget provides `1 × 2 × 1000 × 0.7 = 1400 CPU-ms/s`. One fails; two suffice under
the model; four pass. `cpu_utilization` defaults to 0.7, a configurable modeling
assumption rather than an AWS guarantee. CPU budgets apply per pool.

Every executed operation and service dependency is charged once per execution
per root request. Costs aggregate across services sharing a compute pool and
across multiple reads/writes using the same database role. Scenarios run in
isolation; multiple traffic classes, probabilistic branches, retries, variable
fan-out, and background traffic are not combined automatically.

For PostgreSQL, declare an AWS profile on the `database_cluster`, use
`engine = postgres`, and put `cpu_ms` on each `reads`/`writes` relationship:

```polish
reads Products.Items { read_from = replica cpu_ms = 20 }
writes Products.Items { cpu_ms = 20 }
```

Replica reads share `read_replicas` running instances. The host's `replicas`
property is a capacity ceiling, not a count of active readers. Primary reads and
writes share exactly one primary. Increasing replicas can fix read overload but
cannot fix primary overload; use a larger instance or reduce workload/cost.
Primary `required_instances` is a CPU-equivalent count only, accompanied by
`horizontal_scaling = false`. This version requires a dedicated host cluster per
database for workload simulation; DB partitioning does not multiply capacity.

Without `rate_rps`, existing scenarios remain functional checks. With it, every
executed service/database workload needs an AWS host profile and explicit CPU
cost; incomplete models fail with `SCALING_MODEL_INCOMPLETE`. Database CPU scaling
currently supports PostgreSQL relational stores only. Static-only traffic is not
modeled. Overload returns `CAPACITY_EXCEEDED`; expected overloads can be passing
tests. Capacity is evaluated after functional traversal, before returning success.

The calculation assumes perfectly balanced, parallelizable CPU work and constant
costs. Measure those costs for your application and chosen machine profile.
vCPU count alone is not a performance benchmark. Disk IOPS, locks, network bandwidth, replication overhead, and tail latency are not
calculated; memory and connection budgets are optional models described below; include known CPU overhead in the declared cost. This is a
steady-state feasibility model, not a throughput guarantee or a latency simulator.
Arithmetic formulas are implemented by the simulator; `.polishd` currently supplies
their numeric inputs rather than supporting general expressions.

## Memory and database connection budgets

Run `python -m polish simulate examples/budgets.polishd` for five scenarios covering
CPU overload, connection-pool oversubscription, memory overload, and scale-out.
JSON output includes a `budgets` array alongside CPU `capacity` records.

These checks opt in through declarations and run on scenarios with `rate_rps`.
Existing CPU-only and functional scenarios retain their previous behavior.

```polish
service Checkout {
  hosted_on Apps
  base_memory_mib = 512
  connection_pool_size = 20
  operation buy {
    method = POST path = "/buy"
    cpu_ms = 10
    duration_ms = 100
    memory_per_request_mib = 2
    writes Products.Items { cpu_ms = 0.1 }
  }
}
```

Memory uses the following steady-state estimates:

```text
concurrent requests = rate_rps × duration_ms / 1000
inflight memory     = concurrent requests × memory_per_request_mib
base memory         = instances × sum(resident services' base_memory_mib)
available memory    = instances × instance_memory_GiB × 1024 × memory_utilization
```

Set `memory_utilization` on the compute pool (default 0.8 when the model is enabled)
to reserve headroom for the operating system and other overhead. The model counts
base memory for every resident service, including services not called in the
scenario, assuming one process of every resident service on every pool instance.
Every resident must declare `base_memory_mib` when memory modeling is enabled;
place unsupported resident types such as caches in separate pools.

Operations declare `duration_ms` and `memory_per_request_mib`, or inherit those
values from their service. Duration is elapsed time, separate from `cpu_ms`.
Explicit zeros are allowed. Inflight costs sum across executed operations; each
invocation's memory footprint must also fit a single instance alongside resident
base memory. Missing required inputs fail explicitly. The model assumes balanced
traffic and fixed costs, and does not predict peak concurrency, garbage collection,
allocator fragmentation, leaks, or PostgreSQL's internal memory usage.

For connections, set database `max_connections` and optional
`reserved_connections` (default zero). Set each calling service's
`connection_pool_size`, interpreted as a maximum per application instance **per
database role**. A role is the primary or the read-replica group.

```text
potential connections = sum(calling service instances × connection_pool_size)
available per DB instance = max_connections − reserved_connections
```

Repeated reads/writes from the same service to the same role reuse the same pool
budget. Different services add to the budget. Replica pools are assumed perfectly
balanced across readers: each reader needs `ceil(total / read_replicas)` slots.
The primary has one budget shared by primary reads and writes. Only services whose
database edges execute in the scenario contribute pools; background or idle pools
outside that path must be accounted for through reserved connections or separate
scenarios. This does not model PgBouncer or multiplexing.

The example has 200 database slots, with 20 reserved. Four application instances
with pools of 20 need at most 80 slots. Twenty instances need 400 slots and fail
with `CONNECTION_BUDGET_EXCEEDED`, even though their CPU budget now suffices.
Reducing the pool size to 9 brings that budget to 180 and passes the model.

This is a conservative **potential oversubscription** check, not observed runtime
connection exhaustion: it assumes configured pools could fill. Actual open
connections, checkout wait time, and connection-hold duration are not simulated.
CPU feasibility is evaluated first, followed by memory and connection budgets;
a CPU failure can therefore stop the scenario before budget results are emitted.

## Cloud vendor catalogs and AWS products

Vendor-specific definitions live under their own folders, separate from the core
language definitions:

```text
polish/config/
  vendors.json
  vendors/
    aws/
      ec2.json       # EC2 instance hardware
      rds.json       # RDS PostgreSQL and db.* instance classes
      dynamodb.json  # Managed storage and capacity-unit rules
      elb.json       # ALB/NLB protocol and routing capabilities
```

`vendors.json` lists the product files to load for each vendor. Custom config
directories must include that manifest and its referenced files. The old root
`aws.json` has been removed; migrate custom copies to the new layout. Catalogs
ship with the Python package. AWS, self-hosted software, Kong, and Fastly profiles
are implemented; adding another folder alone does not implement its behavior. Instance sizes, product properties,
defaults, restrictions, and capacity-unit constants are data; execution remains
in the simulator. Product profiles apply when both `provider = aws` and a matching
`product` are declared.

Run the complete example:

```sh
python -m polish simulate examples/aws-products.polishd
```

### RDS PostgreSQL

```polish
database_cluster Rds {
  provider = aws
  product = rds_postgres
  instance_type = "db.m7i.large"
  multi_az = true
}
```

Supported classes initially include `db.m7i.large` and `db.m7i.xlarge`, sourced from
[AWS RDS hardware specifications](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.DBInstanceClass.Summary.html).
EC2 names cannot be substituted for RDS classes, and RDS classes cannot host
compute pools. Engine defaults to `postgres`, storage to `persistent`, and TLS
support to true. Hosted relational databases default to TLS support and disk
durability. Explicit TLS settings still determine whether a modeled request passes.
Existing PostgreSQL CPU and connection-budget checks apply.

`multi_az` represents the **Multi-AZ DB instance deployment**, not an Aurora or
Multi-AZ DB cluster. Its standby adds no modeled read or write capacity. AWS
documents this distinction in its
[Multi-AZ DB instance guide](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.MultiAZSingleStandby.html).
Declare `read_replicas` separately on the database. These default to asynchronous
replication; this profile rejects synchronous read-replica declarations. Failover,
standby replication cost, availability guarantees, engine versions, region support,
and service quotas are not simulated.

### DynamoDB

```polish
database Sessions {
  kind = key_value
  partitions = managed
  hosted_on DynamoTable
  keyspace Tokens { key_type = string value_type = json }
}
database_cluster DynamoTable {
  provider = aws
  product = dynamodb
  capacity_mode = provisioned
  read_capacity_units = 100
  write_capacity_units = 50
}
```

Here `database_cluster` represents a managed product endpoint, not a machine you
provision. One such host and its logical database represent **one DynamoDB table's
capacity budget**. Nested keyspaces/collections are schema views within that table,
not independently provisioned AWS tables. Use separate database/host pairs for
separate DynamoDB tables. Both key-value and document database kinds are supported.
Partitions are managed; instance types, CPU budgets, connection limits, and
user-managed replicas are rejected. Transport defaults to HTTPS. This version
models managed persistence at Polish's `disk` durability level; it does not model
DynamoDB replication internals or global tables.

```polish
reads Sessions.Tokens { item_bytes = 4096 consistency = strong }
writes Sessions.Tokens { item_bytes = 1500 }
```

Each edge models one nontransactional item operation per root request. Supply the
complete item size in bytes, including attribute-name overhead. Size must be
between 1 and 409600 bytes. For a scenario with `rate_rps`:

```text
strong read units/sec   = rate_rps × ceil(item_bytes / 4096)
eventual read units/sec = strong_read_units × 0.5
write units/sec         = rate_rps × ceil(item_bytes / 1024)
```

These constants follow [AWS DynamoDB capacity rules](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Constraints.html).
Thus 30 writes/sec of 1500-byte items consume 60 write units/sec and exceed a
50-unit provisioned budget. Demand aggregates across repeated accesses to the
same database; read and write budgets are separate. `item_bytes` is required for
workload simulation; `cpu_ms` and `read_from` are not valid on DynamoDB edges.

For `capacity_mode = on_demand`, omit provisioned capacities and optionally declare
`max_read_units` and `max_write_units`. A rate-based scenario requires an explicit
maximum for whichever operation it exercises. This bounds the simulation; it does
not assume immediate or unlimited scaling. Burst capacity, ramp-up, hot partitions,
indexes, transactions, scans, conditional-write behavior, retries, and billing are
not modeled. Throttling is a deterministic aggregate-budget failure, not a precise
prediction of which individual AWS requests would throttle.

### AWS load balancers

```polish
load_balancer PublicALB {
  provider = aws
  product = alb
  tls_termination = true
  route "/api/*" to Apps
}
load_balancer InternalNLB {
  provider = aws
  product = nlb
  tls = true
  routes_to Rds
}
```

ALB accepts modeled HTTP/HTTPS traffic and path routes. HTTPS must terminate at
the ALB. NLB rejects path routes and forwards using `routes_to`; TCP/TLS and HTTP
payloads over those transports are supported by this simplified model. `tls = true`
without termination represents encrypted passthrough. It does not mean NLB
inspects HTTP paths. UDP, QUIC, listener ports, target groups, health checks,
certificates, LCUs, and load-balancer capacity limits are not modeled yet. See
[AWS ALB listeners](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-listeners.html)
and [AWS NLB listeners](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/load-balancer-listeners.html).

No AWS resources are created or queried while compiling or simulating.

## Caches, GraphQL, gateways, databases, and AWS processing

Both validation and executable models are provided for the products below. Models
are deliberately explicit: declared throughput limits are workload measurements
or planning assumptions, never invented vendor benchmarks.

| Component and product | Deployment | Executable checks |
| --- | --- | --- |
| `cache`, `redis` / `memcached` | `provider = self_hosted`, required compute host | Hit/miss paths, fallback calls, dataset size, operations/sec |
| `cache`, `elasticache_redis` / `elasticache_memcached` | `provider = aws`, no compute host | Same cache model with managed hosting |
| `database_cluster`, `cockroachdb` | `provider = aws` or `self_hosted` | Relational schema, transport, declared cluster operations/sec |
| `database_cluster`, `mongodb` | `provider = aws` or `self_hosted` | Document schema, transport, declared cluster operations/sec |
| `gateway`, `graphql` | `provider = self_hosted`, required compute host | HTTP routing, declared query-depth/cost limits |
| `gateway`, `gateway` | `provider = kong`, required compute host | HTTP routing, request-rate and burst budgets |
| `gateway`, `api_gateway` | `provider = aws` | HTTP routing, request-rate and burst budgets |
| `cdn`, `cdn` | `provider = fastly` | Edge routing, request-rate and burst budgets |
| `gateway`, `signal_sciences` or `next_gen_waf` | `provider = fastly` | Inline policy routing, request-rate and burst budgets |
| `stream`, `msk` | `provider = aws` | Broker/replication constraints, message throughput, backlog growth |
| `queue`, `sqs` | `provider = aws` | Standard/FIFO configuration, message throughput, backlog growth |
| `batch_cluster`, `emr` | `provider = aws` | Spark/Hadoop configuration, worker CPU capacity |

AWS CockroachDB/MongoDB profiles describe software deployed on AWS, **not AWS-native
managed database products**. They do not imply Atlas or CockroachDB Cloud support.
Signal Sciences is exposed under Fastly, consistent with
[Fastly's Next-Gen WAF documentation](https://www.fastly.com/documentation/guides/next-gen-waf/setup-and-configuration/agent-management/getting-started-with-the-agent/).
The WAF node abstracts an inline policy check; attack detection and vendor agent
deployment are not reproduced. GraphQL is an HTTP layer, not a cloud vendor.

```sh
python -m polish simulate examples/platforms.polishd
python -m polish simulate examples/aws-processing.polishd
```

### Caches and self-hosted hardware

```polish
compute_pool Local {
  provider = self_hosted
  vcpus = 4
  memory_gib = 16
  instances = 2
}
cache Redis {
  provider = self_hosted
  product = redis
  hosted_on Local
  capacity_mib = 512
  dataset_mib = 100
  max_ops_rps = 1000
  invokes API.backend
}
```

Services access caches with `reads Redis` or `writes Redis`. Use scenario
`cache_result = hit` to serve a hit, or `miss` (the default) to execute the cache's
`invokes` fallback. A miss without a fallback fails explicitly. Hits skip fallback
CPU and database work; repeated cache accesses add operations to the cache budget.
Writes record access but do not mutate persistent state across scenarios. Cache
edges accept transport settings, not database consistency or durability options.

`capacity_mib` is the usable logical cache allocation, not raw instance RAM.
`dataset_mib` defaults to zero (an undeclared dataset), and exceeding the allocation
fails before fallback. A rate-based scenario requires `max_ops_rps`. These are
aggregate cache budgets, not inferred Redis/Memcached performance. Eviction,
TTL expiry, serialized keys, warming, cluster hashing, persistence, and inferred
hit ratios are not modeled. The selected hit/miss applies to all cache reads in
that scenario. Redis and Memcached share this intentionally generic cache model.
For ElastiCache, select the AWS product and omit `hosted_on`; capacity is still an
explicit assumption. See [AWS's engine comparison](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/SelectEngine.html).

Self-hosted compute pools use explicit `vcpus` and `memory_gib`, not an AWS instance
name. Existing service CPU and memory math applies. Database and cache declared
capacity budgets remain separate; the model does not infer their throughput from
CPU specifications or aggregate cache allocation against colocated service RAM.

### MongoDB and CockroachDB

```polish
database_cluster Documents {
  provider = self_hosted
  product = mongodb
  nodes = 3
  max_ops_rps = 100
}
```

Attach a document database to MongoDB, or a relational database to CockroachDB.
`nodes` validates deployment size. `max_ops_rps` is a measured **whole-cluster**
budget; increasing nodes does not automatically multiply it. Each executed read
or write adds the scenario rate to the host's demand, including accesses from
different logical databases. Exceeding the budget fails. Wire-level query plans,
distributed transactions, MongoDB write concern, CockroachDB range placement,
replication protocols, elections, and hot shards are not simulated. Generic Polish
storage declarations do not prove those vendor-specific semantics. Their concepts
are described in [CockroachDB deployment documentation](https://www.cockroachlabs.com/docs/stable/deploy-cockroachdb-on-premises)
and [MongoDB replication documentation](https://www.mongodb.com/docs/manual/replication/).

### Gateways, GraphQL, and rate limits

Set `limit_rps` for steady-state offered load and `burst_limit` for an instantaneous
burst. Scenarios supply `rate_rps` and optional `burst_requests`. Repeated traversal
of a policy node adds offered load. Exceeding either limit returns `RATE_LIMITED`
with outcome `denied` before the downstream request runs. At the limit, traffic
passes. No requests are partially admitted: an overloaded workload fails as a whole.

For GraphQL, set `max_query_depth` and/or `max_query_cost`; the scenario must supply
the corresponding `query_depth`/`query_cost`. Excess returns
`GRAPHQL_LIMIT_EXCEEDED`. The layer forwards to a service operation; that operation
declares resolver-like dependencies using `invokes` and data access edges. Polish
does not parse GraphQL documents, infer cost, validate a GraphQL schema, implement
federation, or infer N+1 fan-out. Gateway host requirements validate topology; the
gateway's own CPU overhead is not automatically charged to its host.

These common policy checks are not exact replicas of vendor algorithms. In
particular [AWS API Gateway uses token buckets and best-effort throttling](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-throttling.html),
while [Kong offers multiple rate-limiting plugins](https://developer.konghq.com/gateway/rate-limiting/).
This iteration checks steady-rate and burst budgets independently; token refill,
time windows, distributed counters, per-identity quotas, and vendor licensing or
plan availability are outside the model.

### MSK, SQS, and EMR workload math

Services and operations may `publishes_to` / `consumes_from` a stream or queue,
or `submits_to` a batch cluster. Each edge represents one message/job per root
request. SQS/EMR use modeled HTTPS connections; MSK uses TLS. Missing throughput
inputs in a rate-based scenario fail rather than imply infinite capacity.

```polish
stream Events {
  provider = aws product = msk
  brokers = 3 partitions = 6 replication_factor = 3
  max_messages_rps = 100
  consumer_rate_rps = 80
  max_backlog = 1000
}
queue Jobs {
  provider = aws product = sqs queue_type = standard
  max_messages_rps = 100
  consumer_rate_rps = 5
  max_backlog = 20
}
batch_cluster Analytics {
  provider = aws product = emr framework = spark
  workers = 2 worker_vcpus = 2 cpu_utilization = 0.7
}
```

MSK replication cannot exceed broker count. MSK/SQS `max_messages_rps` is a declared
aggregate throughput budget applied separately to publishing and consuming; it
is not an AWS service quota. `consumer_rate_rps` declares background drain; explicit
`consumes_from` edges add modeled consumption demand. Effective drain is capped at
`max_messages_rps`. Starting with an empty backlog:

```text
backlog = max(0, publish_rate − effective_consume_rate) × window_seconds
```

The scenario's `window_seconds` defaults to 1. `max_backlog` is optional; if supplied,
exceeding it fails. A consume-only scenario tests capacity without requiring stored
messages. Partitions do not automatically multiply cluster throughput. Visibility
timeout, retention, queue type, and replication count are configuration metadata;
delivery order, duplicates, retries, actual consumer groups, retention expiry, and
Kafka offsets are not simulated. SQS standard queues can deliver duplicates and
out-of-order messages; see [AWS SQS documentation](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/standard-queues.html).

An EMR submission supplies `submits_to Analytics { job_cpu_ms = 1000 }`:

```text
batch demand   = rate_rps × job_cpu_ms
batch capacity = workers × worker_vcpus × 1000 × cpu_utilization
```

At 3 jobs/sec, that is 3000 CPU-ms/sec. Two 2-vCPU workers at 70% offer 2800, so the
scenario fails; three workers pass. Default utilization is 0.7. This assumes
perfect parallelism and excludes driver overhead, Spark stages, shuffle, storage,
memory, startup, and job scheduling. It is a workload feasibility model for an
[EMR-style processing cluster](https://docs.aws.amazon.com/emr/latest/ManagementGuide/),
not execution of a Spark/Hadoop job. All scenario state and budgets reset between runs.

## Boundaries of this version

The simulator proves declared functional paths under the model's assumptions.
It does not prove production availability, security, or performance. In
particular:

- CPU capacity is modeled for explicitly declared workloads. Autoscaling timing,
  latency and physical failures are not simulated; queue backlog uses the aggregate model below. Storage configuration
  capacity is checked per database, not aggregated across tenants.
- CDN protection features, cache TTL, database engines, schema fields, and primary
  keys are typed or recorded metadata. Bot detection, caching, request collapsing,
  query semantics and version compatibility are not simulated. Explicit rate-policy
  budgets and cache hit/miss paths are simulated as described below.
- TLS checks cover request routing, page loading, service calls, and database
  connections. Certificates, TLS versions, real wire handshakes, and network ACLs
  are not modeled.
- Responses return implicitly. HTTP status codes, payloads, retries, queues,
  sessions, transactions, conditional branches, and reverse response paths are not modeled.
- Server rendering through a compute pool requires exactly one renderer service
  in that pool. Hybrid rendering can override settings per page, but streaming and
  hydration behavior are not modeled.
- There is no deployment backend, LLM integration, custom class inheritance, or
  persistent compiled artifact yet. JSON diagnostics provide an integration surface
  for an LLM to inspect and repair a specification.

## Implementation

Language definitions live in **`polish/config/`** and ship with the installed package:

| File | Definitions |
| --- | --- |
| `databases.json` | Database kinds and allowed children, database properties, storage enums, positive integer settings |
| `services.json` | Service and operation properties and containment |
| `hosts.json` | Host properties and permitted hosting pairs |
| `frontend.json` | Frontend/page properties, rendering sources, artifact store kinds |
| `network.json` | CDN, gateway, load balancer, and cache properties |
| `relationships.json` | Allowed source/target types, connection options and protocols |
| `language.json` | Boolean properties, field types, HTTP methods, shared enums and integer settings |
| `vendors.json` | Vendor catalog manifest |
| `vendor_schema.json` | Vendor profile metadata validation contracts |
| `vendors/aws/*.json` | AWS EC2, RDS, DynamoDB, and ELB profiles, capabilities, and source metadata |
| `errors.json` | Compiler, storage, simulator, and CLI error codes, message templates, and failure outcomes |
| `diagnostics.json` | Model diagnostic details, required input bindings, and display labels |
| `model_inputs.json` | Model field mappings and optional numeric defaults |
| `rules.json`, `rules/*.rules` | Executable model formulas, conditions, defaults, and validation rules |

For example, `databases.json` declares the storage kinds and their child types:

```json
"database_kinds": {
  "relational": ["table"],
  "document": ["document", "collection"],
  "key_value": ["keyspace"]
}
```

The compiler loads definitions on each compilation. The grammar's component and
relationship keywords come from these files too. To use a separate configuration,
copy the whole directory and pass it explicitly:

```sh
cp -R polish/config /tmp/my-polish-config
python -m polish check examples/shop.polishd --config-dir /tmp/my-polish-config
python -m polish simulate examples/storage.polishd --config-dir /tmp/my-polish-config
```

The Python API accepts `compile_source(source, config_dir=...)`. Custom directories
replace the bundled configuration completely; there is no implicit working-directory
override. Missing files and malformed definitions produce `E_CONFIG` diagnostics.
Run `python -m polish` from the repository to use edited source configuration, or
reinstall the package to refresh the installed CLI's bundled copy.

JSON configuration defines vocabulary, permitted declarations, and input bindings.
The files in `config/rules/` define executable model behavior: formulas, conditions,
defaults, routing, TLS, durability, replication, and capacity checks. Python parses
architecture definitions, supplies graph/data operations, and interprets the rules.
Adding a property alone still does not invent behavior: define how the relevant
rule consumes it. New host capabilities require interpreter support.

Error definitions use stable rule identifiers. For example:

```json
"TLS_UNSUPPORTED": {
  "code": "TLS_UNSUPPORTED",
  "phase": "simulation",
  "message": "'{node_name}' does not accept TLS.",
  "parameters": ["node_name"],
  "outcome": "error"
}
```

Edit `code` or `message` to customize diagnostics. Simulation `outcome` may be
`error` or `denied`; failures cannot be changed into successes. Rule identifiers
with suffixes such as `_2` distinguish different checks that share an emitted
error code. Keep identifiers, parameter contracts, and phases unchanged. Templates
support named placeholders and `!r`/`!s`, without attribute access, indexing,
format specifications, or executable expressions. A message may omit parameters.

The simulator retains the catalog from compilation, including custom
`--config-dir` definitions. If you change an emitted error code, update any
scenario `expect { error = ... }` assertions that reference it. Existing scenario
definitions remain in `.polishd` files. The conditions that detect errors still
live in `config/rules/`; the error catalog describes their diagnostics, while
rule files define executable checks. `E_CONFIG` is a built-in fallback so a broken or
missing error catalog can itself be reported.

Diagnostic wording can also belong to the component or vendor product that defines
an object. Resolution is **product override → component override → shared catalog**.
For example, the EMR definition in `vendors/aws/messaging.json` contains:

```json
"diagnostics": {
  "BATCH_CPU_INPUTS": {
    "message": "{component} '{name}' using {provider}/{product} requires {missing_fields} for {model}."
  }
}
```

When only worker CPU capacity is missing, this produces:

```text
Incomplete scaling model: batch_cluster 'Analytics' using aws/emr requires worker_vcpus for batch_cpu.
```

The same override can live on `components.batch_cluster` in `hosts.json` as a
class default. Overrides may target detail rules in `diagnostics.json` or complete
error rules such as `TLS_UNSUPPORTED` in `errors.json`. They change wording, not
error codes, failure outcomes, or the conditions that trigger checks. Object-level
errors resolve the object's product, or its parent/host product when applicable;
architecture-level errors use the shared catalog.

Detail templates and overrides accept plain named placeholders from their
parameter contract plus `name`, `component`, `provider`, `product`, `model`, and
`missing_fields`. Unlike the original error catalog, these templates do not allow
format conversions. Invalid templates and input contracts produce `E_CONFIG`.
Custom config directories must include `diagnostics.json`.

Named input contracts describe fields and labels separately from message text.
Component and product overrides can replace an `inputs` binding by semantic name,
while retaining its scope. Platform capacity calculations consume these bindings
for batch CPU, cache memory, and cache/database/message throughput. Renamed fields
must also be declared in the component/product property schema. This is not a
general mechanism for renaming every language field. For other models or changes
to their algorithms, edit the corresponding configured rule file.

Compiler diagnostics and simulation JSON include structured context identifying
the object, component, provider/product, rule, and template origin. Missing-input
diagnostics additionally list the specific missing fields. Planner findings retain
simulation diagnostics and classify incomplete models by stable rule identifiers,
so changing a public error code does not change their classification.

Platform calculations resolve numeric DSL fields through configuration too. For
example, `model_inputs.json` maps the backlog model's semantic input
`background_consumption` to `consumer_rate_rps`, and the traffic model's `rate` to
`rate_rps`. Rate and burst limits, GraphQL depth/cost, cache dataset size, batch
worker counts/utilization, partition counts, and backlog windows/limits use the
same mechanism. Required capacity bindings remain in `diagnostics.json` so their
calculations and missing-input messages share one definition.

A component or product can override a mapping using `model_inputs`. For example,
an SQS product with a declared, nonnegative `drain_rps` property could define:

```json
"model_inputs": {
  "message_backlog": {
    "background_consumption": {
      "scope": "node",
      "field": "drain_rps",
      "default": 0
    }
  }
}
```

The simulator then uses `drain_rps` in the actual backlog calculation:
`max(0, arrivals - min(capacity, consumers + background_consumption)) × window`.
Precedence is product → component → shared model inputs. Missing optional fields
use the configured default; missing required numeric inputs produce a diagnostic.
Model names, semantic input names, scopes, and optionality are fixed contracts;
unknown models, invalid mappings, and invalid numeric defaults produce `E_CONFIG`.
Custom config directories must include `model_inputs.json`.

Input bindings are consumed by `rules/platforms.rules`; model names such as
`rate_policy` and `traffic`, and formula variables such as `demand`, live there too.
CPU, connection, storage, broadcast, and vendor models have their own rule files.
Declare new scenario fields in `language.json` under `request_properties`, and add
validation and execution rules that consume them.

### Executable model rules

`rules.json` maps stable engine hook modules to files in `config/rules/`:

| Rule file | Owns |
| --- | --- |
| `platforms.rules` | Rate/burst limits, GraphQL checks, cache and message throughput, backlog, batch capacity |
| `scaling.rules` | CPU demand, hardware selection, instance counts, scaling validation |
| `budgets.rules` | Memory feasibility and database connection pools |
| `cache_connections.rules` | Client pool distribution and per-node cache connection budgets |
| `runtime_resources.rules` | Broadcast connections, retention, local volumes, external service budgets |
| `vendors.rules`, `cloud_products.rules` | Vendor contracts, defaults, cross-product constraints, DynamoDB capacity |
| `storage.rules` | Durability, replicas, storage and connection contracts |
| `simulator.rules` | Request traversal, routing, TLS, authorization, dependency invocation, assertions |
| `compiler_contracts.rules` | Architecture and scenario semantic validation |

For example, the backlog formula is configured in `platforms.rules`:

```python
consumed = min(self.message_limit(node), self.loads.get((name, "consume"), 0) + inputs.get("background_consumption"))
backlog = max(0, arrivals - consumed) * window
```

Changing this formula in a custom configuration changes the simulated result;
no edit to `platforms.py` is required. That Python module is now a lifecycle adapter.
The same applies to the other model adapters. Existing `.polishd` files keep their
syntax and behavior with the bundled rules.

Rule files use a restricted Python-shaped syntax, interpreted by
`rule_engine.py` rather than imported or passed to Python `eval`/`exec`. Supported
operations include arithmetic, comparisons, conditionals, loops, local functions,
collections, and registered graph/model/diagnostic operations. Imports, private
attribute access, arbitrary host calls, and unsupported syntax are rejected.
Execution has an instruction budget and guards against oversized numeric and
sequence operations. Rule failures produce `E_CONFIG`; a broken rule does not count
as a passing scenario even when an error was expected.

Keep exported hook names and signatures unchanged. Model names, field bindings,
formulas, thresholds, and conditions inside those hooks belong to configuration.
Custom config directories must include `rules.json` and every referenced rule file;
these are also included in the wheel. A compiled architecture retains its rules,
so simulation and planning use the same configuration that was validated.

- `polish/grammar.lark`: formal syntax, parsed using [Lark](https://lark-parser.readthedocs.io/en/stable/).
- `polish/model.py`: typed component graph, fields, diagnostics, scenarios.
- `polish/compiler.py`: parsing, name resolution, type and constraint validation.
- `polish/simulator.py`: request traversal and scenario assertions.
- `polish/storage.py`: database contracts and connection-option validation.
- `polish/cli.py`: terminal and JSON output.
- `tests/test_polish.py`: positive and negative integration tests.

### CloudFront, Cloudflare, S3, and ECR

See [delivery.polishd](examples/delivery.polishd) for ten executable success and
failure scenarios. Profiles live in `config/vendors/aws/delivery.json` and
`config/vendors/cloudflare/edge.json` inside the `polish` package.

```polish
artifact_store Assets {
  provider = aws product = s3
  max_requests_rps = 100
}
cdn Edge {
  provider = aws product = cloudfront
  limit_rps = 200
  routes_to Assets
}
frontend Shop { rendering = static loaded_from Edge }
```

Use `provider = cloudflare product = cdn` for Cloudflare. Both profiles default
to TLS termination with HTTPS upstream and support routing, TLS checks,
`limit_rps`, and `burst_limit`. These limits are explicitly declared scenario
policies, not vendor quotas. Every simulated request reaches the origin;
CDN cache hits, shielding, geographic latency, and cache eviction are not modeled.

S3 defaults to a REST endpoint. With `endpoint = website`, TLS defaults to false,
HTTPS fails, and only GET/HEAD requests succeed. Configure a CDN's
`upstream_protocol = http` to reach that endpoint. See the
[AWS endpoint comparison](https://docs.aws.amazon.com/AmazonS3/latest/userguide/WebsiteEndpoints.html).
A rate scenario reaching S3 requires `max_requests_rps`:

```text
origin demand = scenario rate_rps × visits to that origin
failure when origin demand > declared max_requests_rps
```

This is an aggregate request budget, not an estimate of S3's actual performance.
Object existence, bucket permissions, CloudFront OAC, and S3 durability are not
simulated in this iteration.

[ECR](https://docs.aws.amazon.com/AmazonECR/latest/userguide/what-is-ecr.html)
is a container image registry, so use a service's `image_from` relationship:

```polish
artifact_store Images { provider = aws product = ecr max_pulls_rps = 5 }
service Api {
  hosted_on Apps
  image_from Images { authorized = true }
  operation get { method = GET path = "/" cpu_ms = 1 }
}
```

A service may have one image source. Registries cannot be used as frontend
origins. The simulator checks HTTPS and workload authorization once per executed
service per scenario. `authorized` defaults to false and represents the declared
pull permission; it is independent of the request user's authentication.
An optional scenario `image_pulls_rps` models startup pull traffic, independently
of HTTP `rate_rps`. It requires `max_pulls_rps`; demands from distinct executed
services using the same registry are summed. These checks represent a deployment
precondition, not a fresh pull per application request. IAM evaluation, image
layers/tags, network transfer time, and registry storage capacity are not modeled.

## License

Polish is licensed under the [MIT License](LICENSE).

## Change-impact analysis and suggestions

Compare an existing architecture with a proposed design:

```sh
polish plan examples/plan-before.polishd --proposed examples/plan-after.polishd
polish plan examples/plan-before.polishd --proposed examples/plan-after.polishd --json
```

The example raises service instances from 2 to 8 without changing the connection
pool. Polish identifies an introduced failure: `8 × 50 = 400` potential database
connections against a budget of 300. It suggests comparing smaller per-instance
pools with connection pooling. Changing the pool size to 30 gives 240 potential
connections; rerun the plan to verify that proposal against the scenarios.
The proposed example intentionally fails its success requirement, so this command
returns exit code 1.

The planner compares component properties, requirements, fields, and relationships
(including edge options). It follows relationships in both directions, containment,
and explicit `via` references across both versions. Impact paths are conservative:
sharing a connected graph means a component may be affected, not that it must change.
Line-number changes do not count as architecture changes; renames appear as removal
and addition. JSON output includes the diff and a path to each potentially affected
component.

Both the baseline and proposed scenarios are evaluated against both designs.
Identical scenarios are deduplicated; altered workloads or expectations are retained
as separate requirements. Deleting or weakening a baseline scenario therefore does
not erase the original requirement. `--scenario NAME` explicitly narrows evaluation.
Results are classified as `introduced`, `persists`, `resolved`, or `unchanged_pass`
according to whether the scenario's expectations pass. An expected denial or error
can be a passing requirement. `persists` means the requirement fails in both designs;
the underlying error may have changed, so inspect the before/after evidence.

Findings distinguish required fixes from missing information and include suggestions
from `polish/config/decisions.json`. Custom config directories must include this file
when running `plan`. Ordinary `check` and `simulate` do not require decision rules.
Compiler-invalid designs receive diagnostics and advice without being simulated.
Cross-design scenarios with incompatible references receive explicit diagnostics.

This first planning iteration proposes advice, not edited candidate architectures.
Suggestions are not automatically applied, ranked, or proven to resolve a failure;
edit a proposal and rerun the plan to test it. There is no optimization objective,
cost model, or migration execution engine yet. Simulations stop at the first runtime
failure, and unobserved affected components are reported as needing information.
Exit code 0 means all selected requirements pass on the proposed design, not that
all possible impacts have been tested. Hard constraints currently come from the
compiler rules and scenario expectations.

## Cache connections and failure behavior

The radio catalog example models an API depending on Memcached and demonstrates
connection overload, a cache miss fallback, and a cache error propagating to the API:

```sh
polish simulate examples/radio-catalog.polishd
```

Describe a reusable client pool on each cache `reads` or `writes` relationship:

```polish
reads StationCache {
  pool_size = 50
  pool_scope = worker
  pool_distribution = per_node
  on_miss = fallback
  on_error = fail
}
```

Worker-scoped pools require `workers_per_instance` on the source service.
`pool_scope = instance` instead models one pool per service instance. All edges
from a service to the same cache describe one shared pool and must agree on the
three pool properties. Repeated reads/writes count that pool once; distinct services
add to the same cache budget. These checks run even without `rate_rps` because pool
limits do not depend on request throughput.

The cache declares `max_connections_per_instance` and optional
`reserved_connections_per_instance` (default 0). Self-hosted caches get node counts
from their host's `instances`; managed ElastiCache products require `nodes` for this
model. Scenario `scale` selects minimum/maximum host counts as for other budgets.
A supplied client pool requires a server limit, and a server limit requires client
pool settings; missing values produce an incomplete-model error.

For 20 API instances and 8 workers per instance:

| Distribution | Calculation | Potential connections per cache node |
|---|---|---|
| `per_node` | 20 × 8 × 50 | 8,000, regardless of cache node count |
| `cluster_even`, 3 cache nodes | ceil(20 × 8 × 50 / 3) | 2,667 |

`cluster_even` explicitly assumes an even distribution. It is not automatic
sharding inference and does not model hotspots or skew. When multiple services
share the cache, their rounded per-node budgets are added conservatively.

These numbers describe worst-case configured pool capacity, not observed sockets.
The budget reports `CACHE_CONNECTION_BUDGET_EXCEEDED` if demand exceeds the limit
minus reserved connections. It does not infer a timeout or predict when pools fill.
Client creation per request, socket lifetime, retries, and deadline propagation are
not modeled in this iteration. Redis and Memcached use the same declared pool math;
Redis-specific primary/replica connection distribution is not inferred.

The scenario's `cache_result` is `hit`, `miss` (default), or `error` and applies to
all cache accesses in that scenario. Reads honor hit/miss; writes ignore hit/miss.
Both reads and writes honor error. Client policies are:

- `on_miss = fallback` (default): execute the cache's existing `invokes` fallback.
- `on_miss = fail`: report `CACHE_MISS`.
- `on_error = fail` (default): report `CACHE_UNAVAILABLE`, propagating to the caller.
- `on_error = fallback`: execute the cache's `invokes` fallback, including its
  downstream workload checks. An explicit fallback policy requires that relationship.

This error injection models a failed cache access; it does not bypass TLS or other
architecture validation. Connection-budget overload remains a budget failure even
if error fallback is configured. Multiple `invokes` fallbacks all execute, following
the existing dependency semantics.

For planning, keep a success expectation for the catalog request and compare API
instance counts. `polish plan` will identify a new cache-budget violation and suggest
reviewing pool size, workers, and connection distribution. The radio example instead
expects the overloaded scenario to fail, so all four demonstration scenarios pass.
Fastly edge-cache traffic reduction and an automatic before/after cache migration
model are not included yet.

## Additional AWS and GCP products

```sh
polish simulate examples/multicloud.polishd
```

The ten scenarios cover successful requests and overloaded resources. New profiles
live in `polish/config/vendors/aws/events.json` and `polish/config/vendors/gcp/`.

| Provider / product | Component | Validation and simulation |
|---|---|---|
| `aws / sns` | `topic` | Standard/FIFO topic selection, SNS-to-SQS relationships, topic throughput and destination budgets |
| `aws / kinesis` | `stream` | Provisioned/on-demand contracts, declared shard capacity, consumption and backlog budgets |
| `gcp / pubsub_topic` | `topic` | Topic publishing, fan-out to subscriptions, declared publish capacity |
| `gcp / pubsub_subscription` | `queue` | Exactly one source topic, pull subscription, independent consumption and backlog |
| `gcp / memorystore_redis` | `cache` | Managed placement, dataset size, measured operation throughput, client connection budgets, miss/error policies |
| `gcp / cloudsql_postgres` | `database_cluster` | Relational storage, TLS, asynchronous read-replica consistency, measured aggregate operation throughput, database connection budgets |

### Topic delivery

```polish
topic Updates {
  provider = gcp product = pubsub_topic max_messages_rps = 100
  delivers_to Indexing
  delivers_to Analytics
}
queue Indexing {
  provider = gcp product = pubsub_subscription delivery = pull
  max_messages_rps = 80 consumer_rate_rps = 10 max_backlog = 50
}
queue Analytics {
  provider = gcp product = pubsub_subscription
  max_messages_rps = 100 consumer_rate_rps = 100 max_backlog = 200
}
```

Services use `publishes_to Updates` and `consumes_from Indexing`. Each publication
delivers one message to every declared subscription. At 20 publications/s over six
seconds, Indexing accumulates `max(0, 20 - 10) × 6 = 60` messages, exceeding its
50-message budget, while Analytics has no growth. Backlog starts at zero for each
scenario. `consumer_rate_rps` is background consumption; explicit `consumes_from`
traffic adds to it. Declared capacity bounds publishing and consumption separately.

SNS uses the same `topic` syntax with `provider = aws product = sns` and
`topic_type = standard` or `fifo`; its `delivers_to` targets must be AWS SQS queues.
Standard SNS topics cannot target FIFO SQS queues. Pub/Sub subscriptions require
exactly one incoming Pub/Sub `delivers_to` relationship; publishing directly to a
subscription is rejected. Topics have publish budgets, not consumer backlogs.

These are aggregate messaging scenarios: a destination overload makes the scenario
fail, not a claim that the real asynchronous publisher receives a synchronous error.
Delivery latency, retries, IAM, filtering, message ordering/deduplication, and
exactly-once guarantees are not simulated. Pub/Sub push/export subscriptions and
SNS HTTP/email endpoints are outside this iteration.

### Kinesis capacity

```polish
stream Events {
  provider = aws product = kinesis capacity_mode = provisioned
  shards = 2 messages_per_shard_rps = 50
  consumer_rate_rps = 100 max_backlog = 200
}
```

Provisioned capacity is `shards × messages_per_shard_rps`: this example supports a
declared 100 messages/s. The per-shard rate is a measured assumption supplied by the
user, not a built-in AWS quota. On-demand mode omits both shard settings and uses an
explicit `max_messages_rps` bound. `on_demand` abstracts AWS on-demand variants;
autoscaling history, warm capacity, hot keys, byte limits, and enhanced fan-out are
not modeled. Read and write rates use the same declared bound independently.
Kinesis connections use HTTPS, while MSK retains its TLS transport model.

### GCP cache and database budgets

Memorystore reuses the Redis dataset, operation-rate, and connection-pool models.
Use `nodes` as the number of modeled client-facing cache nodes, not a claim that
standbys add serving capacity. High availability, tier-specific placement, Redis
Cluster slot routing, failover, and replica endpoints are not modeled.

Cloud SQL uses a `database` hosted on `database_cluster` with
`provider = gcp product = cloudsql_postgres`. Declare `max_ops_rps` on the cluster
as measured aggregate capacity. Replica count does not automatically multiply that
budget. Read replicas must be asynchronous and cannot satisfy strong-consistency
replica reads. Optional service `connection_pool_size` and database
`max_connections`/`reserved_connections` are evaluated as with the other connection
budgets. This is an operation-rate model, not a Cloud SQL machine-type or HA model;
`instance_type` and `cpu_utilization` are rejected for this profile.

The example uses explicitly sized self-hosted application compute; it does not imply
that GCP Compute Engine machine types or Cloud Run execution are supported yet.
All new rate limits are declared modeling inputs, not provider performance promises.

Product references: [SNS FIFO](https://docs.aws.amazon.com/sns/latest/dg/sns-fifo-topics.html),
[Kinesis capacity modes](https://docs.aws.amazon.com/streams/latest/dev/how-do-i-size-a-stream.html),
[Pub/Sub subscriptions](https://docs.cloud.google.com/pubsub/docs/subscription-overview),
[Memorystore for Redis](https://docs.cloud.google.com/memorystore/docs/redis/memorystore-for-redis-overview),
and [Cloud SQL replication](https://docs.cloud.google.com/sql/docs/postgres/replication).

## Local runtimes, mounted storage, and live streams

```sh
polish simulate examples/streaming-runtime.polishd
```

This synthetic example covers eight success/failure cases. Its capacity values are
illustrative assumptions. It introduces:

- `stream` with `provider = self_hosted product = redis_stream`, hosted on a
  singleton `compute_pool`. Each consumer has an independent cursor; reads do not
  remove messages. This differs from competing queue consumers.
- `request.concurrent_connections` for concurrent long-lived listeners, separate
  from HTTP `rate_rps`. Multiple consumed streams share their host's declared
  `max_stream_connections`, minus optional `reserved_stream_connections`. Each
  consumer edge uses one connection per client unless `connections_per_client`
  declares another positive integer. These are declared connection assumptions,
  not client-library socket measurements. Existing reusable cache pools and these
  stream budgets are separate; reserve capacity for other Redis usage explicitly.
- `consumer_lag_seconds` compared with `retention_messages` and declared
  `source_rate_rps`. Required history is `ceil(lag × source_rate)`; delivery demand
  is `concurrent_connections × source_rate`. A declared source rate requires a
  `max_messages_rps` delivery bound when evaluating listeners. Readers must supply
  concurrency for rate-based scenarios. Functional runs can omit capacity inputs.
- Proxy `supports_streaming`, `response_buffering`, and optional
  `max_client_connections`. A concurrent-stream scenario rejects explicit lack of
  support or enabled buffering. Omitted proxy settings do not prove streaming
  compatibility. Client limits are logical declared capacities, not a direct
  translation of a proxy's worker/file-descriptor settings.
- `volume` with `storage = persistent` or `ephemeral`. Services declare
  `mounts Media { access = read_only }` or `read_write`; their operations may
  `reads Media` / `writes Media`. Missing mounts fail compilation and writes through
  read-only mounts fail simulation. Optional `used_mib`/`capacity_mib` check declared
  occupancy; rate-based access requires `max_ops_rps`. No file data is created and
  writes do not automatically grow modeled occupancy.
- `external_service` for API dependencies without a modeled local host. Use
  `invokes Provider { protocol = https }`; TLS must be declared on the provider.
  Rate scenarios require its measured/contractual `max_requests_rps`. Provider
  response content, failure branches, tokens, costs, and retry timing are not modeled.
- Service/operation `reads` and `writes` to object stores, with transport checks
  and a `max_requests_rps` bound for rate scenarios. Container registries still use
  `image_from`. S3 website endpoints reject writes.

LocalStack profiles (`provider = localstack`, `product = sqs` or `s3`) use HTTP
explicitly and remain separate from AWS profiles. They model API dependencies and
declared budgets, not emulator durability, redrive, visibility renewal, or cloud
parity. Existing MSK and Kinesis streams retain managed placement; Redis streams
require a compute host. `self_hosted` database-cluster profiles now include
`postgres`, `elasticsearch`, and `opensearch`, with relational/document contracts,
appropriate TCP/TLS or HTTP/HTTPS transports, and declared `max_ops_rps` budgets.
They do not simulate query plans, index semantics, or transaction isolation.

Self-hosted Redis cache declarations may omit `capacity_mib` for functional checks
with no dataset or request rate. Capacity scenarios still require the missing
measurements; omission does not mean infinite memory or throughput.

The live-stream model is steady-state and deterministic. It does not calculate
end-to-end latency, audio timing, byte bandwidth, proxy idle timeout, retries,
backpressure, failover, or graceful connection draining. Redis approximate trimming
is represented by an explicit conservative retention bound. A connection-budget
failure identifies a violated assumption, not the time of a predicted outage.
