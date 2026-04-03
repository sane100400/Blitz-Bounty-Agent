#!/usr/bin/env bash
# EVMBench Official Harness Setup
# Sets up the official OpenAI/Paradigm EVMbench for running detect/patch/exploit evals.
#
# Prerequisites:
#   - Docker (running)
#   - uv (https://docs.astral.sh/uv/)
#   - API-style credentials if you plan to run upstream Claude solvers
#
# Note:
#   This script is for the official upstream harness path.
#   The repo-local Blitz runners use `claude -p` and can run from a logged-in
#   Claude Code subscription session without ANTHROPIC_API_KEY.
#
# Usage:
#   bash benchmark/evmbench_setup.sh          # full setup
#   bash benchmark/evmbench_setup.sh --quick   # skip docker builds (if already done)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EVMBENCH_DIR="$REPO_ROOT/evmbench-upstream/project/evmbench"

echo "=== EVMBench Official Harness Setup ==="

# 1. Check prerequisites
for cmd in docker uv; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: $cmd is required but not found. Install it first."
        exit 1
    fi
done

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    echo "WARNING: ANTHROPIC_API_KEY not set."
    echo "         That's fine for Blitz's local `claude -p` runners."
    echo "         It may block upstream EVMBench runs that expect API credentials."
fi

# 2. Clone if not present
if [ ! -d "$REPO_ROOT/evmbench-upstream" ]; then
    echo "Cloning openai/frontier-evals (sparse)..."
    cd "$REPO_ROOT"
    git clone --depth 1 --filter=blob:none --sparse \
        https://github.com/openai/frontier-evals.git evmbench-upstream
    cd evmbench-upstream
    git sparse-checkout set project/evmbench
else
    echo "EVMBench repo already cloned."
fi

cd "$EVMBENCH_DIR"

# 3. Install Python deps
echo "Installing Python dependencies..."
uv sync

# 4. Build Docker images (skip with --quick)
if [ "${1:-}" != "--quick" ]; then
    echo "Building ploit-builder base image..."
    docker build -f ploit/Dockerfile -t ploit-builder:latest --target ploit-builder .

    echo "Building all audit Docker images (this takes a while)..."
    uv run docker_build.py --split all
else
    echo "Skipping Docker builds (--quick mode)"
fi

# 5. Verify setup with debug split
echo ""
echo "=== Setup complete ==="
echo ""
echo "Quick test (human agent, debug split):"
echo "  cd $EVMBENCH_DIR"
echo "  uv run python -m evmbench.nano.entrypoint \\"
echo "      evmbench.audit_split=debug \\"
echo "      evmbench.mode=detect \\"
echo "      evmbench.apply_gold_solution=False \\"
echo "      evmbench.log_to_run_dir=True \\"
echo "      evmbench.solver=evmbench.nano.solver.EVMbenchSolver \\"
echo "      evmbench.solver.agent_id=human"
echo ""
echo "Run Claude Opus 4.6 on full detect split:"
echo "  cd $EVMBENCH_DIR"
echo "  ANTHROPIC_API_KEY=\$ANTHROPIC_API_KEY \\"
echo "  uv run python -m evmbench.nano.entrypoint \\"
echo "      evmbench.audit_split=detect-tasks \\"
echo "      evmbench.mode=detect \\"
echo "      evmbench.hint_level=none \\"
echo "      evmbench.log_to_run_dir=True \\"
echo "      evmbench.solver=evmbench.nano.solver.EVMbenchSolver \\"
echo "      evmbench.solver.agent_id=claude-opus-4.6 \\"
echo "      runner.concurrency=3"
echo ""
echo "Repo-local subscription-backed benchmark path:"
echo "  cd $REPO_ROOT"
echo "  python3 benchmark/claude_subscription_check.py --probe"
echo "  python3 benchmark/evmbench_skill_runner.py --audit 2026-01-tempo-feeamm"
