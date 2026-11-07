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
python src/main.py --profile-config "$PROFILE" --profile-meta "$META" \
  --output daily_report.md "${ARGS[@]+"${ARGS[@]}"}" || exit 1

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
echo "  Site:   https://ai-job-desk.vercel.app"
echo "  Report: daily_report.md"
