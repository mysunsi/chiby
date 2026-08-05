# Confirm & safety

ChibyTerm emphasizes **confirm before change** and **auditability**.

## Confirmation cards

When a command is treated as **high-risk** (mass delete, shutdown, account changes, etc.), a confirmation card appears:

- Shows the command and risk hints
- Runs only after you explicitly **allow**
- You can refuse / cancel to avoid mistakes

## Policy gateway

Server-side execution may apply policy blocks, change windows, freeze queues (depending on config). See env vars such as `OPS_POLICY_*` / `OPS_CLOSURE_*` in the repo docs.

## Practices

1. On production hosts, start with read-only checks for connectivity and permissions.
2. Before high-risk ops, re-read host and paths on the confirmation card.
3. **Fleet** broadcasts to every open session (OS-specific commands) — prefer read-only probes; rely on the policy gateway. See help **Fleet**.  
4. Do not commit plaintext passwords in `hosts.json`.
5. Change the UI password after first login (menu → Change password).
