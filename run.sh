#!/usr/bin/env bash
# run.sh — the whole daily loop: search, publish, back up.
#
#   ./run.sh              full run
#   ./run.sh --offline    replay today's snapshots, no network, no quota
#   ./run.sh --no-deploy  skip Vercel
#
# Any other flags pass through to main.py.

set -uo pipefail
cd "$(dirname "$0")"

PROFILE="${PROFILE:-mukul-profile.json}"
META="${META:-profiles/ai-agent-ml.json}"
DEPLOY=1
ARGS=()
for a in "$@"; do
  case "$a" in
    --no-deploy) DEPLOY=0 ;;
    *) ARGS+=("$a") ;;
  esac
done

echo "── search ─────────────────────────────────────────────"
RUNLOG="$(mktemp 2>/dev/null || echo ./.runlog)"
python src/main.py --profile-config "$PROFILE" --profile-meta "$META" \
  --output daily_report.md "${ARGS[@]+"${ARGS[@]}"}" 2>&1 | tee "$RUNLOG"
[ "${PIPESTATUS[0]}" = "0" ] || { rm -f "$RUNLOG"; exit 1; }

# Back up before deploying. These snapshots include paid, rate-capped data;
# re-fetching them costs quota that is already spent, so they get committed
# whether or not the deploy succeeds.
echo
echo "── back up data ───────────────────────────────────────"
if [ -d data/.git ]; then
  if [ -n "$(git -C data status --porcelain)" ]; then
    git -C data add -A
    git -C data commit -q -m "data: snapshot $(date -u +%Y-%m-%d)"
    if git -C data push -q origin main 2>/dev/null; then
      echo "  pushed to the private data repo"
    else
      echo "  committed locally; push failed (offline?) — will go with the next run"
    fi
  else
    echo "  nothing new to back up"
  fi
else
  echo "  data/ is not a git repo — skipping backup"
fi

# The career material is gitignored here because this remote is public, so it
# is backed up to a separate private repo. It changes far less often than
# data/, but it is the part that cannot be re-fetched: 83 verified facts took a
# full session to build, and re-deriving them means re-reading the git history
# of 62 repos. Cheap when nothing changed, so it runs every time.
echo
echo "── back up career material ────────────────────────────"
BK="$(python tools/backup_private.py 2>&1)"
if [ $? -eq 0 ]; then
  # Only the outcome line. The per-file detail is useful when run by hand and
  # pure noise at the end of a long search run.
  echo "$BK" | grep -E "already up to date|pushed to|committed:|!!" | sed 's/^ */  /' | tail -2
else
  echo "  !! backup failed — facts/, prep/ and docs/ are NOT backed up:"
  echo "$BK" | grep -E "!!|Error|error" | sed 's/^ */     /' | head -3
fi

if [ "$DEPLOY" = "1" ]; then
  echo
  echo "── deploy ─────────────────────────────────────────────"
  ( cd web && npx vercel --prod ) || echo "  deploy failed — the report and backup are still good"
fi

echo
# The paid source failing is easy to miss in a long scroll, and its cap resets
# at 00:00 UTC — 05:30 IST — not at local midnight. That gap is what makes a
# late-evening run look mysteriously empty.
if grep -qiE "RemoteRocketship:.*(skipped|Daily request limit|stopping after)" "$RUNLOG" 2>/dev/null; then
  echo "  !! RemoteRocketship did NOT contribute to this run:"
  grep -iE "RemoteRocketship:.*(skipped|Daily request limit|stopping after)|Quota resets|Daily cap is per UTC" "$RUNLOG" \
    | sed 's/^ */     /' | head -4
  echo "     Everything else ran. Rerun after the reset to pick up paid listings."
  echo
fi
rm -f "$RUNLOG" 2>/dev/null

echo "  Site:   https://ai-job-desk.vercel.app"
echo "  Report: daily_report.md"

# A run that ends on a URL tells you where to go and look at more jobs, which
# is the thing there is already too much of. Ending on the queue puts the next
# action in front of you at the one moment you are certainly still looking.
echo
python tools/track.py --brief 2>/dev/null || true
