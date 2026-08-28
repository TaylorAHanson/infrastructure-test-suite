#!/usr/bin/env bash
#
# unblock — fan out the next N unblocked implementable tickets into a tmux
# "swarm", each in its own git worktree running a supervised, interactive
# isaac session seeded with /implement.
#
# A ticket is implementable iff it carries the `wayfinder:task` label (the
# positive marker `/to-tickets` stamps on leaves) — this deliberately EXCLUDES
# specs (titled `Spec: …`, never labelled `wayfinder:task`) and the map. See
# docs/agents/issue-tracker.md → "Implementable-ticket discriminator". Pointing
# /implement at a spec makes an agent swallow the whole spec in one PR (see
# PR #48), which is exactly what this filter avoids.
#
# Supervised, not AFK: you attach, watch each pane, and push + open a PR by
# hand when a ticket's work looks right. Each ticket's PR should `Closes` only
# its own number — never fan out to siblings or the spec.
#
# Usage:   scripts/unblock.sh [N]        # N tickets, default 4, capped at 4
# Env:     ISAAC_CMD (default "isaac")   SWARM_SESSION (default swarm)
#          READY_WAIT secs before send (default 14)
#          DRY_RUN=1  -> print the selected frontier and exit; claims nothing,
#                        creates no worktree, starts no tmux session.
set -euo pipefail

N="${1:-4}"; [ "$N" -gt 4 ] 2>/dev/null && N=4
SESSION="${SWARM_SESSION:-swarm}"
ISAAC="${ISAAC_CMD:-isaac}"
READY_WAIT="${READY_WAIT:-14}"   # isaac's TUI needs time to init before it accepts a submit
DRY_RUN="${DRY_RUN:-0}"

command -v tmux >/dev/null || { echo "unblock: tmux not installed (brew install tmux)" >&2; exit 1; }
command -v gh   >/dev/null || { echo "unblock: gh not installed" >&2; exit 1; }
command -v jq   >/dev/null || { echo "unblock: jq not installed" >&2; exit 1; }

REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
ROOT=$(git rev-parse --show-toplevel)

# --- frontier: open `wayfinder:task` tickets (the implementable-ticket marker),
# unassigned, with no open blocker. We select by LABEL rather than by walking
# the map's sub-issue tree, because a child may be linked to the map either as a
# native GitHub sub-issue OR only via the map's task-list body — the tree walk
# misses the latter, but the label is authoritative either way. Order is
# ascending issue number (approximates spec/creation order). Assumes a single
# map per repo; add map scoping here if you ever run more than one.
echo "unblock: scanning open wayfinder:task tickets (want $N) ..."
pick=()
for n in $(gh issue list --repo "$REPO" --state open --label "wayfinder:task" --limit 100 --json number --jq '.[].number' | sort -n); do
  n_asg=$(gh issue view "$n" --repo "$REPO" --json assignees --jq '.assignees | length' 2>/dev/null) || continue
  blocked=$(gh api "repos/$REPO/issues/$n" --jq '.issue_dependencies_summary.blocked_by // 0' 2>/dev/null || echo 0)
  if [ "$n_asg" = "0" ] && [ "$blocked" = "0" ]; then
    pick+=("$n")
    [ "${#pick[@]}" -ge "$N" ] && break
  fi
done

if [ "${#pick[@]}" -eq 0 ]; then
  echo "unblock: no open, unassigned, unblocked wayfinder:task tickets."
  exit 0
fi

if [ "$DRY_RUN" = "1" ]; then
  echo "unblock: DRY RUN — would claim @me + spawn ${#pick[@]} pane(s):"
  for n in "${pick[@]}"; do
    t=$(gh issue view "$n" --repo "$REPO" --json title --jq .title 2>/dev/null)
    echo "  #$n  ->  worktree ../ist-$n   branch feature/$n   ($t)"
  done
  echo "unblock: dry run — nothing claimed, no worktrees, no tmux session."
  exit 0
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "unblock: tmux session '$SESSION' already exists — attach with:  tmux attach -t $SESSION" >&2
  exit 1
fi

echo "unblock: claiming + spawning ${#pick[@]} panes for: ${pick[*]}"
first=1
for n in "${pick[@]}"; do
  wt="$ROOT/../ist-$n"
  branch="feature/$n"

  gh issue edit "$n" --repo "$REPO" --add-assignee @me >/dev/null   # CLAIM (removes from frontier)

  # Worktree + branch off main (inherits the auto-port dev.sh). Reuse if present.
  if ! git -C "$ROOT" worktree add "$wt" -b "$branch" main 2>/dev/null; then
    echo "unblock: worktree/branch for #$n exists; reusing $wt" >&2
  fi

  url="https://github.com/$REPO/issues/$n"
  if [ "$first" = 1 ]; then
    tmux new-session -d -s "$SESSION" -n swarm -c "$wt"
    pane=$(tmux list-panes -t "$SESSION" -F '#{pane_id}' | head -1)
    first=0
  else
    pane=$(tmux split-window -t "$SESSION" -c "$wt" -P -F '#{pane_id}')
    tmux select-layout -t "$SESSION" tiled >/dev/null
  fi

  tmux send-keys -t "$pane" "$ISAAC" Enter
  # Seed the ticket once the REPL is up. READY_WAIT is a heuristic — if it fired
  # early, just re-send "/implement $url" in that pane. (Alternative worth trying:
  # `$ISAAC \"/implement $url\"` as a seeded first prompt, dropping the wait.)
  ( sleep "$READY_WAIT"; tmux send-keys -t "$pane" "/implement $url" Enter ) &
done
wait
tmux select-layout -t "$SESSION" tiled >/dev/null

echo
echo "unblock: spawned ${#pick[@]} supervised panes (claimed @me, one worktree each)."
echo "  attach:   tmux attach -t $SESSION"
echo "  when a ticket is done: review, push, open a PR that Closes only that #, then: git worktree remove ../ist-<n>"
