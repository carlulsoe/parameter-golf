# Graceful Handoff For Autoresearch Controller

## Problem

The current controller keeps its ready queue only in memory. During deployment, `systemd --user restart` stops the service, which can interrupt proposer, pre-review, or remote execution work. Even when the Python process exits cleanly, queued candidates are merely rewritten back to `approved` in their manifests. On the next start, the controller does not rehydrate those candidates into the live queue.

That creates three concrete failures:

- deploys discard queue depth
- a candidate can be pre-reviewed and durable on disk but never become runnable again
- shutdown is not a true drain; it can kill child `codex` or `ssh` work before the controller decides what to do with it

## Goals

- Stop accepting new work when a graceful shutdown is requested.
- Let already-started prep, review, training, and post-review units finish.
- Persist queue membership and order to disk.
- Rehydrate the queue on startup so the new controller resumes where the old one left off.
- Recover sensible backlog from manifests after unclean exits.
- Keep the status model easy to reason about.

## Non-Goals

- Resuming an already-running remote training job after the controller process disappears.
- Preserving exact in-memory retry timers across restarts.
- Replacing the manifest-per-candidate trace model.

## State Model

Candidate manifest `status` meanings:

- `drafting`: proposer or pre-review still in progress
- `approved`: pre-review passed and durable runnable artifacts exist on disk, but the candidate is not currently in the durable queue
- `queued`: candidate is present in the durable ready queue
- `dequeued`: candidate was removed from the durable queue and is being prepared for execution locally
- `running`: patch applied and experiment execution is in progress
- terminal states: `keep`, `revert`, `error`, `apply_failed`, `rejected_pre_review`, `failed`

Important semantic change:

- `approved` is not a queue substitute
- `queued` must correspond to durable queue membership on disk

## Durable Queue

Add a queue file under `TRACE_ROOT`:

- `controller_state/autoresearch/ready_queue.json`

Format:

```json
{
  "version": 1,
  "items": [
    {
      "candidate_id": "candidate_0138",
      "manifest_path": ".../controller_state/autoresearch/candidates/candidate_0138/manifest.json"
    }
  ]
}
```

Rules:

- Queue order is the order in `items`.
- Every enqueue and dequeue updates this file synchronously.
- `queued` manifests not present in the queue file are considered inconsistent and repaired during startup.
- `approved` manifests are valid backlog but not guaranteed queue members.

## Graceful Shutdown Model

Introduce a controller drain mode triggered by `SIGTERM` or `SIGINT`.

Drain behavior:

- do not start a new proposer/pre-review unit
- do not dequeue a new candidate for execution
- allow already-started proposer/pre-review to finish
- allow the active training run to finish
- allow post-review/finalization of that run to finish
- allow an in-flight prep worker that reaches `approved` to enqueue if there is queue capacity
- if the queue is already full, leave that candidate as durable `approved`

This gives deploys a bounded handoff point without discarding already-produced work.

## Startup Recovery

On controller startup:

1. Load the durable queue file.
2. Rehydrate every queued item into the in-memory queue in file order.
3. Scan candidate manifests for recoverable states:
   - `queued`: ensure they are represented in the durable queue
   - `approved`: eligible backlog; enqueue into free slots in approval order
   - `dequeued` or `running`: treat as interrupted work from a previous process and downgrade to `approved`, then enqueue if capacity is available
4. Rewrite `ready_queue.json` to the reconciled queue snapshot.

This makes graceful restarts and crash recovery converge back to a consistent state.

## Deploy / Service Changes

The service should no longer let `systemd stop` kill child work immediately.

Service changes:

- `KillMode=mixed`
- `TimeoutStopSec=30min`

Why:

- `KillMode=mixed` sends the initial stop signal only to the main process, not the whole cgroup
- the controller can trap `SIGTERM`, enter drain mode, and manage child `codex` / `ssh` processes itself
- `TimeoutStopSec` must exceed the longest acceptable drain window, which includes a 10-minute run plus review/finalization overhead

The deploy script can keep using `systemctl --user restart`, because restart is implemented as stop then start. With the service changes above, stop becomes a graceful drain instead of an immediate teardown.

## Implementation Notes

- Use a dedicated queue lock around queue mutations plus queue-file writes.
- Keep the queue file authoritative for order.
- Keep manifests authoritative for candidate artifacts and status.
- Log recovery actions in the harness log so deploy behavior is visible.
- Remove the old shutdown behavior that drained the in-memory queue back to `approved` without rehydration.

## Verification Plan

- Unit test enqueue/dequeue persistence into `ready_queue.json`.
- Unit test startup recovery from a mix of `queued`, `approved`, and `running` manifests.
- Run controller static checks.
- Confirm that a queued candidate survives controller restart and is still runnable afterward.
