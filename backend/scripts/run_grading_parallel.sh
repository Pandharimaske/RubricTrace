#!/usr/bin/env bash
# Runs both grading tracks as real, concurrent OS processes:
#   - grade_mcq_local.py          (local Ollama, sequential, no network)
#   - grade_short_answer_cloud.py (cloud NVIDIA/Groq, --concurrency in-flight requests)
# They write to separate output files (results_mcq.json / results_short_answer.json)
# and never touch each other's state, so they're safe to run at the same time.
#
# After both finish (successfully), merges the two outputs and computes the
# final grading-accuracy report via compute_grading_metrics.py.
#
# Usage:
#   ./scripts/run_grading_parallel.sh
#   ./scripts/run_grading_parallel.sh --limit 5                 # smoke test both tracks
#   ./scripts/run_grading_parallel.sh --provider groq            # short-answer track on Groq
#   ./scripts/run_grading_parallel.sh --student Student_1 --student Student_2
#
# Flags (all optional):
#   --limit N            Only grade the first N pending records per track (smoke test).
#   --student ID          Only grade this student_id. Repeatable.
#   --provider NAME       Cloud provider chain for the short-answer track, comma-separated
#                         (default nvidia,groq — auto-fails-over on credit/quota exhaustion).
#   --mcq-model NAME      Ollama model tag for the MCQ track (default qwen2.5:3b).
#   --concurrency N       In-flight cloud requests for the short-answer track (default 4).
#   --allow-partial       Merge whatever output exists even if one track failed/was interrupted.
#
# Exit behavior: if EITHER track's python process exits non-zero, the script
# stops before merging (a partial results_*.json from the failed track is
# still on disk and safe to resume with a re-run — both graders skip
# already-graded keys). Ctrl+C kills both background processes cleanly.

# NOTE: deliberately no "-u" here (unlike this project's other scripts) —
# macOS ships bash 3.2 by default, and 3.2 treats expanding an EMPTY array
# (e.g. "${STUDENT_ARGS[@]}" when no --student flag was passed) as an
# unbound-variable error under set -u, even though the array is legitimately
# declared-but-empty. bash 4.4+ fixed this; 3.2 has not been patched since
# Apple stopped shipping GPL v3 tools. -e and pipefail alone still catch a
# failing command/pipeline, which is what actually matters here.
set -eo pipefail
cd "$(dirname "$0")/.."

MCQ_MODEL="qwen2.5:3b"
PROVIDER="nvidia,groq"
CONCURRENCY=""
LIMIT=""
ALLOW_PARTIAL=""
STUDENT_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --limit)
      LIMIT="$2"; shift 2 ;;
    --student)
      STUDENT_ARGS+=(--student "$2"); shift 2 ;;
    --provider)
      PROVIDER="$2"; shift 2 ;;
    --mcq-model)
      MCQ_MODEL="$2"; shift 2 ;;
    --concurrency)
      CONCURRENCY="$2"; shift 2 ;;
    --allow-partial)
      ALLOW_PARTIAL="--allow-partial"; shift ;;
    *)
      echo "Unknown flag: $1" >&2; exit 1 ;;
  esac
done

LOG_DIR="data/processed/logs"
mkdir -p "$LOG_DIR"
MCQ_LOG="$LOG_DIR/grade_mcq_local.log"
SA_LOG="$LOG_DIR/grade_short_answer_cloud.log"

MCQ_ARGS=(--model "$MCQ_MODEL" "${STUDENT_ARGS[@]}")
SA_ARGS=(--provider "$PROVIDER" "${STUDENT_ARGS[@]}")
[[ -n "$LIMIT" ]] && MCQ_ARGS+=(--limit "$LIMIT") && SA_ARGS+=(--limit "$LIMIT")
[[ -n "$CONCURRENCY" ]] && SA_ARGS+=(--concurrency "$CONCURRENCY")

echo "=========================================="
echo "RubricTrace parallel grading"
echo "  MCQ track:          local Ollama ($MCQ_MODEL)"
echo "  Short-answer track: cloud ($PROVIDER)"
echo "  Logs:                $MCQ_LOG"
echo "                        $SA_LOG"
echo "=========================================="

run_py() {
  if command -v uv >/dev/null 2>&1; then
    UV_CACHE_DIR=.uv-cache uv run python "$@"
  else
    python3 "$@"
  fi
}

# Rebuild the consolidated extraction index from data/raw/extracted_answers/
# every run, so edits to the per-student source files are always picked up
# before grading — this is a fast, pure JSON transform (no LLM calls), so
# re-running it is cheap even when nothing changed.
echo "Building extraction index from data/raw/extracted_answers/..."
run_py scripts/build_extracted_answers_index.py
echo ""

# Launch both tracks as background OS processes, each streaming to its own
# log file (tail -f'd below with a prefix) so a long run's output stays
# readable instead of the two processes' prints interleaving mid-line.
run_py scripts/grade_mcq_local.py "${MCQ_ARGS[@]}" >"$MCQ_LOG" 2>&1 &
MCQ_PID=$!

run_py scripts/grade_short_answer_cloud.py "${SA_ARGS[@]}" >"$SA_LOG" 2>&1 &
SA_PID=$!

echo "Started MCQ track (pid $MCQ_PID) and short-answer track (pid $SA_PID)."
echo "Tailing both logs — Ctrl+C stops both."
echo ""

# Prefix each track's log stream and forward to this terminal while we wait.
tail -n +1 -f "$MCQ_LOG" 2>/dev/null | sed -u 's/^/[mcq]   /' &
TAIL_MCQ_PID=$!
tail -n +1 -f "$SA_LOG" 2>/dev/null | sed -u 's/^/[short] /' &
TAIL_SA_PID=$!

cleanup() {
  kill "$MCQ_PID" "$SA_PID" "$TAIL_MCQ_PID" "$TAIL_SA_PID" 2>/dev/null || true
}
trap cleanup INT TERM

MCQ_STATUS=0
SA_STATUS=0
wait "$MCQ_PID" || MCQ_STATUS=$?
wait "$SA_PID" || SA_STATUS=$?
kill "$TAIL_MCQ_PID" "$TAIL_SA_PID" 2>/dev/null || true
trap - INT TERM

echo ""
if [[ "$MCQ_STATUS" -ne 0 || "$SA_STATUS" -ne 0 ]]; then
  echo "One or both grading tracks failed (mcq exit=$MCQ_STATUS, short-answer exit=$SA_STATUS)."
  echo "Full logs: $MCQ_LOG , $SA_LOG"
  echo "Both scripts checkpoint and resume by (student_id, question_id) — re-run this"
  echo "script to pick up where they left off, or pass --allow-partial to merge now."
  if [[ -z "$ALLOW_PARTIAL" ]]; then
    exit 1
  fi
fi

echo "Both tracks finished. Merging results..."
run_py scripts/merge_grading_results.py $ALLOW_PARTIAL

echo ""
echo "Computing final grading-accuracy report..."
run_py scripts/compute_grading_metrics.py data/processed/results_merged.json \
  --output data/processed/grading_report.json

echo ""
echo "Done. Merged results: data/processed/results_merged.json"
echo "      Metrics report: data/processed/grading_report.json"
