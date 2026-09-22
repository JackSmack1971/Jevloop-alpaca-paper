# Live-money boundary

This package **does not implement live-money trading**. That is an executable runtime boundary, not merely a prompt instruction:

- the Alpaca adapter accepts only the paper trading base URL;
- the CLI has no `--live` mode;
- `.env.example` contains no live enablement secret or confirmation phrase;
- attempts to route the adapter to `https://api.alpaca.markets` are rejected.

A Skill is workflow/knowledge, not authorization. `SKILL.md` can constrain behavior, but it cannot grant broker authority, credentials, network access, or approval. Live deployment therefore belongs in a separately designed and reviewed runtime/control plane rather than behind a magic phrase inside this package.

If a user asks about live operation, explain the gap and requirements rather than silently restoring the old path. A separate live system would need, at minimum: independently scoped credentials, environment/network policy, explicit approval/operational ownership, broker-state reconciliation, kill/recovery procedures, event-stream validation, audit evidence, deployment controls, and an evaluation basis stronger than paper/simulation behavior.

Passing this package's unit tests, calibration diagnostics, paper runs, or Codex evals is **not** evidence of live suitability or profitability.
