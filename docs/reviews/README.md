# Reviews

The trail of [code-review-relay](../code-review-relay.md) handoffs — one file per
review, the **baton** passed between the Author (Claude) and Reviewer (Codex).

## How

- Copy [`TEMPLATE.md`](TEMPLATE.md) to `YYYY-MM-DD-<slug>.md`.
- The Author fills the **Brief** and sets `status: awaiting-review`.
- The Reviewer appends **Reviewer findings** and a verdict.
- The full protocol, roles, and reviewer checklist live in
  [../code-review-relay.md](../code-review-relay.md).

## Status vocabulary

`draft` → `awaiting-review` ⇄ `changes-requested` → `approved`

## Keeping vs. discarding

These files are **committed by default** — the accumulated review history is exactly
the kind of corpus Neurobase's recommender learns from once it's dogfooding here. If
you'd rather treat a review as an ephemeral scratch baton, add its path to
`.gitignore`; the process works the same either way.

_(No reviews yet.)_
