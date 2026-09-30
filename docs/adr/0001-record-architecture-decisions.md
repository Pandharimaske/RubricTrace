# ADR-0001: Record architecture decisions

- Status: accepted
- Date: 2026-09-30

## Context

RubricTrace is built to industry standards and is expected to be maintained and explained over time. Design choices (workflow engine, database, confidence method) have trade-offs that are easy to forget.

## Decision

Significant architecture decisions are recorded as short ADRs in `docs/adr/`, numbered sequentially (`NNNN-title.md`). Each records context, the decision, and its consequences. Superseded ADRs are kept and marked as superseded, not deleted.

## Consequences

- A new contributor can see why the system looks the way it does.
- Each major choice needs a written justification before it is built.
- A small amount of writing overhead per decision.

## Template

```
# ADR-NNNN: Title

- Status: proposed | accepted | superseded by ADR-XXXX
- Date: YYYY-MM-DD

## Context
## Decision
## Alternatives considered
## Consequences
```
