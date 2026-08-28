---
description: Fan out the next N unblocked wayfinder:task tickets into a tmux swarm (default 4, max 4), each in its own worktree running a supervised /implement.
---

Run the project's swarm launcher, then report — do NOT implement anything yourself; the spawned isaac panes do the work.

Run: `bash scripts/unblock.sh $ARGUMENTS`

The script selects the frontier (implementable `wayfinder:task` leaves under the map that are open, unblocked, and unassigned — never specs), claims each with `@me`, creates a `feature/<n>` worktree per ticket, and starts an interactive isaac in a tmux pane seeded with `/implement`.

After it runs, tell me:
- which ticket numbers were claimed and spawned (or that the frontier was empty),
- the `tmux attach -t swarm` command to watch them,
- the reminder that each ticket's PR should `Closes` only its own number.

If the script reports the `swarm` session already exists, surface that verbatim — don't force-kill it.
