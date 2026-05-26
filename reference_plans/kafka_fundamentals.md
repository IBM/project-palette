# Kafka fundamentals — engineering training
Audience: new engineers joining the platform team

Preferences:
- Tone: instructional, term-by-term

## Cover
Kafka fundamentals — week 1 platform onboarding.

## What is Kafka
- Definition: Kafka is a distributed, append-only log that decouples producers from consumers via durable, partitioned topics.
- Not a queue: messages are not deleted on read; consumers track their own position (offset).
- Not a database: optimized for sequential writes and replay, not point lookups or updates.
- Mental model: a replicated commit log you can tail from any offset.

## The broker stack
Layered abstraction inside a single broker, disk up to consumer API. [[show these as a stack, disk at the bottom]]
- Layer 1 — Disk: segment files on local disk, append-only, one directory per partition.
- Layer 2 — Log: segments grouped into a partition log with an index file for offset lookups.
- Layer 3 — Replication: leader/follower protocol, ISR (in-sync replicas) tracked per partition.
- Layer 4 — Request handling: network threads accept produce/fetch requests, I/O threads hit the log.
- Layer 5 — Coordinator: group coordinator assigns partitions to consumers, tracks offsets in __consumer_offsets.
- Layer 6 — Client API: producer and consumer protocols on top of everything below.

## Topics and partitions
- Topic: a named stream of records (e.g., `orders.created`).
- Partition: the unit of parallelism — each topic split into N partitions, ordered within a partition, unordered across.
- Partition count is the upper bound on consumer parallelism for that topic.
- Replication factor: typically 3 in prod — one leader, two followers.
- Retention: time-based (default 7 days) or size-based; records past retention get deleted regardless of whether they were consumed.

## Producer model
```
producer.send(topic, key, value)
  -> partitioner picks partition (hash of key, or round-robin if key is null)
  -> record batched in memory by partition
  -> batch sent to leader broker for that partition
  -> broker appends to log, replicates to ISR
  -> ack returned (acks=0 | 1 | all)
```
- `acks=all` is the durability default — wait for all ISR to replicate before acknowledging.
- Keys matter: same key always lands on the same partition, which is how you get per-entity ordering.

## Consumer model
```
consumer.subscribe([topic])
consumer.poll()
  -> coordinator assigns partitions to this consumer within its group
  -> consumer fetches from the leader of each assigned partition
  -> processes records
  -> commits offset (auto or manual)
```
- Consumer group: set of consumers sharing a group.id; each partition assigned to exactly one consumer in the group.
- Rebalance: when a consumer joins or leaves, partitions are reassigned — processing pauses briefly.
- Offset is stored per (group, topic, partition) in the __consumer_offsets topic.

## Failure modes
- Broker dies: leader election promotes a follower from ISR; brief unavailability on affected partitions.
- Follower falls behind: drops out of ISR; if `min.insync.replicas` can't be met, producer with `acks=all` gets an error.
- Consumer dies: group coordinator detects via missed heartbeat, triggers rebalance, surviving consumers pick up the partitions.
- Network partition: brokers on the minority side stop serving; clients reconnect when the partition heals.
- Disk full: broker stops accepting writes for affected partitions; this is why retention policy matters.

## Common pitfalls
- Creating topics with 1 partition and discovering later you can't scale consumers — partition count is hard to change.
- Using `acks=1` in production and losing data when a leader fails before replication completes.
- Committing offsets before processing finishes — crash means the record is skipped, not reprocessed.
- Relying on cross-partition ordering. It does not exist. Order is only guaranteed within a single partition.
- Letting consumer lag grow unbounded because nobody alerts on it; by the time someone notices, retention has deleted the backlog.
- Using a null key for records that need per-entity ordering — they scatter across partitions round-robin.

## Three mental models to keep
- If you remember nothing else from this session.
- Three short epigrams to take away [[show these in monospace]]:
  - Log, not queue.
  - Offset, not state.
  - Rebalance, not consensus.
- Everything else in Kafka is consequence of these three.
