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
