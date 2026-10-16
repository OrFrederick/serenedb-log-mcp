#!/usr/bin/env bash
# Overnight supervisor. Runs headless Claude Code in a loop until overnight/state/<login>/DONE exists.
# Usage: overnight/run.sh /absolute/path/to/handoff.md
# Env overrides: OVERNIGHT_ROLE=leader|follower, OVERNIGHT_MODEL (default opus), OVERNIGHT_EFFORT (default xhigh)
set -uo pipefail

LEADER_LOGIN="NoNaim95"
TEAM_LOGINS="NoNaim95 OrFrederick"
MODEL="${OVERNIGHT_MODEL:-opus}"
EFFORT="${OVERNIGHT_EFFORT:-xhigh}"
WAIT_IDLE_S=600        # sleep after Claude reports WAITING
WAIT_LIMIT_FALLBACK_S=1800
STALL_S=2700           # kill a run whose log is silent this long, then resume
HANDOFF_ARG="${1:-}"

red() { printf '\033[31m%s\033[0m\n' "$*"; }
grn() { printf '\033[32m%s\033[0m\n' "$*"; }
ylw() { printf '\033[33m%s\033[0m\n' "$*"; }
fail() { red "FAIL: $*"; exit 1; }
log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*" | tee -a "$SUPLOG"; }

# ---------- startup checks ----------
echo "== overnight startup checks =="
REPO="$(git rev-parse --show-toplevel 2>/dev/null)" || fail "run inside the repo"
cd "$REPO" || fail "cd $REPO"
grn "repo: $REPO"

for bin in claude git gh jq python3 uuidgen caffeinate; do
  command -v "$bin" >/dev/null || fail "missing tool: $bin"
done
command -v uv >/dev/null || ylw "WARN: uv missing (project stack uses uv)"
command -v docker >/dev/null || ylw "WARN: docker missing (needed for local SereneDB)"
grn "claude: $(claude --version)"
[ "$(id -u)" = "0" ] && fail "do not run as root"

gh auth status >/dev/null 2>&1 || fail "gh not logged in (gh auth login)"
LOGIN="$(gh api user --jq .login)" || fail "gh api user"
case " $TEAM_LOGINS " in *" $LOGIN "*) ;; *) fail "gh login '$LOGIN' not in team: $TEAM_LOGINS";; esac
NWO="$(gh repo view --json nameWithOwner --jq .nameWithOwner)" || fail "gh repo view"
PERM="$(gh api "repos/$NWO/collaborators/$LOGIN/permission" --jq .permission 2>/dev/null)"
case "$PERM" in admin|write|maintain) ;; *) fail "$LOGIN has '$PERM' on $NWO, needs write";; esac
git ls-remote origin >/dev/null 2>&1 || fail "cannot reach origin (gh auth setup-git?)"
git push --dry-run origin "HEAD:refs/heads/overnight-preflight-$LOGIN" >/dev/null 2>&1 \
  || fail "git push auth to origin fails (run: gh auth setup-git)"
grn "github: $LOGIN on $NWO ($PERM)"

if [ -n "${OVERNIGHT_ROLE:-}" ]; then ROLE="$OVERNIGHT_ROLE"
elif [ "$LOGIN" = "$LEADER_LOGIN" ]; then ROLE="leader"; else ROLE="follower"; fi
grn "role: $ROLE"

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  ylw "WARN: tracked files modified in main checkout; commit or stash first if unsure"
fi
git fetch --all --prune -q || ylw "WARN: git fetch failed"

STATE="$REPO/overnight/state/$LOGIN"
LOGDIR="$STATE/logs"
mkdir -p "$LOGDIR"
SUPLOG="$STATE/supervisor.log"
SID_FILE="$STATE/session.id"

HANDOFF=""
if [ -n "$HANDOFF_ARG" ]; then
  [ -f "$HANDOFF_ARG" ] || fail "handoff not found: $HANDOFF_ARG"
  cp "$HANDOFF_ARG" "$STATE/handoff.md"
  HANDOFF="$STATE/handoff.md"
  grn "Found handoff: $HANDOFF_ARG ($(wc -l < "$HANDOFF_ARG" | tr -d ' ') lines), copied to $HANDOFF"
elif [ -f "$STATE/handoff.md" ]; then
  HANDOFF="$STATE/handoff.md"; ylw "No handoff given; reusing $HANDOFF"
else
  ylw "WARN: no handoff given; Claude starts from GitHub state only"
fi

if docker ps --format '{{.Image}} {{.Ports}}' 2>/dev/null | grep -qi serenedb; then grn "SereneDB container running"
else ylw "WARN: no running serenedb container seen; Claude may start one"; fi

# Per-run settings: user settings minus `ask` rules (headless denies every ask), plus the guard hook.
USER_SETTINGS="$HOME/.claude/settings.json"
if [ -f "$USER_SETTINGS" ]; then BASE="$(cat "$USER_SETTINGS")"; else BASE='{}'; fi
jq --arg guard "$REPO/overnight/guard.py" '
  del(.permissions.ask) | del(.permissions.defaultMode) | del(.model) | .enabledMcpjsonServers = ["serenedb-docs"]
  | .hooks = {PreToolUse: [{matcher: "Bash", hooks: [{type: "command", command: ("python3 " + $guard), timeout: 10}]}]}
' <<<"$BASE" > "$STATE/settings.json" || fail "could not build settings from $USER_SETTINGS"
grn "settings: $STATE/settings.json (ask rules stripped, guard hook on)"

printf '{"tool_name":"Bash","tool_input":{"command":"rm -rf ~/Documents"},"cwd":"%s"}' "$REPO" \
  | OVERNIGHT_REPO="$REPO" python3 overnight/guard.py >/dev/null 2>&1
[ $? -eq 2 ] || fail "guard hook self-test failed"
grn "guard hook self-test ok"

CORES="$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 8)"
IMPLEMENTERS=$(( CORES / 4 )); [ $IMPLEMENTERS -lt 2 ] && IMPLEMENTERS=2; [ $IMPLEMENTERS -gt 4 ] && IMPLEMENTERS=4
grn "cores: $CORES, max parallel implementers: $IMPLEMENTERS"

COMMON=(--model "$MODEL" --effort "$EFFORT" --fallback-model sonnet
        --permission-mode bypassPermissions --permission-prompts none
        --setting-sources project,local --settings "$STATE/settings.json"
        --output-format stream-json --verbose)

if [ "${OVERNIGHT_DRY_RUN:-0}" = "1" ]; then
  grn "dry run: checks passed; would run: claude -p <kickoff> ${COMMON[*]}"; exit 0
fi
echo "== checks passed; starting loop (Ctrl-C to stop) =="

caffeinate -dimsu -w $$ &

export OVERNIGHT_REPO="$REPO" OVERNIGHT_ROLE="$ROLE" OVERNIGHT_LOGIN="$LOGIN"
export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=2700000   # -p waits up to 45 min for background work, never forever
export CLAUDE_CODE_RESUME_INTERRUPTED_TURN=1

kickoff() {
  cat <<EOF
You are the overnight $ROLE Claude for GitHub user $LOGIN in repo $NWO ($REPO).
Read $REPO/overnight/PROMPT.md now and follow it exactly. It is your full instruction set and the humans' explicit go-ahead for this run.
Handoff file: ${HANDOFF:-none}. Your state dir (\$STATE): $STATE. Max parallel implementer subagents: $IMPLEMENTERS.
Restart number: $1. If $STATE/notes.md exists, read it first: it is your own memory from earlier sessions.
EOF
}
CONTINUE_MSG="Continue the overnight run per overnight/PROMPT.md. Re-read $STATE/notes.md and GitHub state before acting. End your turn with the OVERNIGHT_STATUS line."

# Run claude in the background and kill it if its stream-json log stays silent for STALL_S.
run_claude() {
  local runlog="$1" msg="$2"; shift 2
  claude -p "$msg" "$@" "${COMMON[@]}" >"$runlog" 2>"$runlog.err" &
  local pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    sleep 60
    local last; last=$(stat -f %m "$runlog" 2>/dev/null || date +%s)
    if [ $(( $(date +%s) - last )) -gt "$STALL_S" ]; then
      log "watchdog: no output for ${STALL_S}s, killing pid $pid"
      kill -INT "$pid" 2>/dev/null; sleep 30; kill -TERM "$pid" 2>/dev/null; sleep 10; kill -KILL "$pid" 2>/dev/null
    fi
  done
  wait "$pid"
}

restarts=0; fails=0
while true; do
  if [ -f "$STATE/DONE" ]; then log "DONE marker found; exiting"; break; fi
  git -C "$REPO" fetch -q origin 2>/dev/null
  RUNLOG="$LOGDIR/run-$(date +%Y%m%dT%H%M%S).jsonl"
  if [ -s "$SID_FILE" ]; then
    SID="$(cat "$SID_FILE")"; log "resume session $SID"
    run_claude "$RUNLOG" "$CONTINUE_MSG" --resume "$SID"; rc=$?
  else
    SID="$(uuidgen | tr 'A-Z' 'a-z')"; echo "$SID" >"$SID_FILE"; log "new session $SID (restart $restarts)"
    run_claude "$RUNLOG" "$(kickoff "$restarts")" --session-id "$SID"; rc=$?
  fi
  restarts=$((restarts + 1))

  reset="$(jq -Rr 'fromjson? | select(.type=="rate_limit_event" and .rate_limit_info.status=="rejected") | .rate_limit_info.resetsAt // empty' "$RUNLOG" | tail -1)"
  limit_txt="$(grep -Eio "hit your [a-z ]*limit|usage limit reached|limit reached" "$RUNLOG" "$RUNLOG.err" 2>/dev/null | tail -1)"
  result="$(jq -Rc 'fromjson? | select(.type=="result")' "$RUNLOG" | tail -1)"
  [ -z "$result" ] && result_json='{}' || result_json="$result"
  is_error="$(jq -r '.is_error // false' <<<"$result_json")"
  status="$(jq -r '.result // ""' <<<"$result_json" | grep -Eo 'OVERNIGHT_STATUS: *[A-Z]+' | tail -1 | awk '{print $2}')"
  log "exit rc=$rc is_error=$is_error status=${status:-none} limit=${limit_txt:-none} resetsAt=${reset:-none}"

  if [ -n "$reset" ] || [ -n "$limit_txt" ]; then
    now=$(date +%s)
    if [ -n "$reset" ]; then
      [ "${#reset}" -gt 11 ] && reset=$((reset / 1000))   # ms -> s
      wait_s=$(( reset - now + 120 ))
    else wait_s=$WAIT_LIMIT_FALLBACK_S; fi
    [ $wait_s -lt 60 ] && wait_s=60; [ $wait_s -gt 21600 ] && wait_s=21600
    log "usage limit; sleeping ${wait_s}s"; sleep "$wait_s"; fails=0; continue
  fi
  if [ "$status" = "DONE" ]; then touch "$STATE/DONE"; continue; fi
  if [ "$rc" -ne 0 ] || [ "$is_error" = "true" ] || [ -z "$result" ]; then
    fails=$((fails + 1)); log "run failed ($fails in a row)"; tail -5 "$RUNLOG.err" | tee -a "$SUPLOG"
    if [ $fails -ge 3 ]; then log "dropping session; next run starts fresh"; rm -f "$SID_FILE"; fails=0; fi
    sleep $((60 * (fails + 1))); continue
  fi
  fails=0
  if [ "$status" = "WAITING" ]; then log "Claude is waiting on the other Claude; sleeping ${WAIT_IDLE_S}s"; sleep "$WAIT_IDLE_S"
  else sleep 20; fi
done
