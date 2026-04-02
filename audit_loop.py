#!/usr/bin/env python3
"""
Autonomous audit competition hunt loop.

Runs structured analysis in a subprocess loop, accumulates multiple findings,
auto-runs Foundry tests for each PoC, and generates submission-ready reports.

Usage:
    python3 audit_loop.py <contest-url> <platform> [max_iterations] [rpc-url]

Platforms:
    sherlock    → auto-submits via `gh issue create` in the contest repo
    codearena   → generates markdown file, manual submit
    cantina     → generates markdown file, manual submit
    codehawks   → generates markdown file, manual submit

Examples:
    python3 audit_loop.py "https://audits.sherlock.xyz/contests/123" sherlock 12
    python3 audit_loop.py "https://code4rena.com/audits/2026-03-foo" codearena 10
    python3 audit_loop.py "https://cantina.xyz/competitions/abc" cantina 8
"""

import subprocess
import sys
import json
import os
import re
import time
import shutil
from pathlib import Path
from datetime import datetime

# ─── CONFIG ───────────────────────────────────────────────────────────────────

MAX_ITERATIONS_DEFAULT = 12
STATE_FILE  = "audit-state.json"
LOG_DIR     = Path("audit-logs")
REPORTS_DIR = Path("audit-reports")

# Signals Claude must emit
SIGNAL_FOUND     = "AUDIT_SIGNAL:FOUND:"      # AUDIT_SIGNAL:FOUND:finding-name:High
SIGNAL_DROP      = "AUDIT_SIGNAL:DROP:"       # AUDIT_SIGNAL:DROP:candidate:reason
SIGNAL_CONTINUE  = "AUDIT_SIGNAL:CONTINUE"
SIGNAL_EXHAUSTED = "AUDIT_SIGNAL:EXHAUSTED"
SIGNAL_RECON     = "AUDIT_SIGNAL:RECON_DONE"
SIGNAL_REPORT    = "AUDIT_SIGNAL:REPORT:"     # AUDIT_SIGNAL:REPORT:finding-name:file-path

# ─── STATE ────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if Path(STATE_FILE).exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "contest_url":    "",
        "platform":       "",
        "rpc":            "",
        "repo_path":      "",
        "deadline":       "",
        "prize_pool":     "",
        "iteration":      0,
        "recon_done":     False,
        "findings":       [],        # [{name, severity, file, status, report_file, submitted}]
        "drop_history":   [],        # [{iteration, candidate, reason, lesson}]
        "poc_failures":   [],        # [{iteration, candidate, failure_reason}]
        "lessons":        [],
        "start_time":     datetime.now().isoformat(),
    }

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def confirmed_findings(state: dict) -> list:
    return [f for f in state["findings"] if f["status"] == "CONFIRMED"]

# ─── PROMPT BUILDERS ──────────────────────────────────────────────────────────

def _context_block(state: dict) -> str:
    drops = ""
    if state["drop_history"]:
        drops = "\n\nPREVIOUSLY DROPPED CANDIDATES (do not re-explore these):\n"
        for d in state["drop_history"]:
            drops += f"  - [{d['iteration']}] {d['candidate']} → {d['reason']}\n"
            if d.get("lesson"):
                drops += f"    Lesson: {d['lesson']}\n"

    poc_fails = ""
    if state["poc_failures"]:
        poc_fails = "\n\nPoC FAILURES (seemed valid but test failed — do not repeat):\n"
        for p in state["poc_failures"]:
            poc_fails += f"  - [{p['iteration']}] {p['candidate']} → {p['failure_reason']}\n"

    already_found = ""
    if confirmed_findings(state):
        already_found = "\n\nALREADY CONFIRMED FINDINGS (do not re-report):\n"
        for f in confirmed_findings(state):
            already_found += f"  - {f['name']} ({f['severity']}): {f['file']}\n"

    lessons = ""
    if state["lessons"]:
        lessons = "\n\nACCUMULATED LESSONS:\n" + "\n".join(f"  - {l}" for l in state["lessons"])

    return f"""
CONTEST: {state['contest_url']}
PLATFORM: {state['platform']}
REPO: {state['repo_path']}
DEADLINE: {state['deadline'] or 'unknown'}
ITERATION: {state['iteration']}
{drops}{poc_fails}{already_found}{lessons}
""".strip()


def build_recon_prompt(state: dict) -> str:
    return f"""
You are running Phase 1 (Contest Recon + Codebase Mapping) of an audit competition hunt.

CONTEST URL: {state['contest_url']}
PLATFORM: {state['platform']}

Tasks:
1. Fetch the contest page. Extract:
   - Contest end date/deadline
   - Total prize pool (USD)
   - Severity tiers (High/Medium payouts)
   - In-scope files/contracts (exact file paths)
   - Out-of-scope exclusions
   - Special judging notes (trusted admin, etc.)

2. Find and clone the GitHub repository:
   git clone <repo-url> contest-repo-<name>
   Record the local path.

3. Read in order:
   - README.md
   - Any SECURITY.md or scope.md
   - All in-scope contract files (NatSpec + logic)
   - /docs if present

4. Build a complete contract inventory:
   For each in-scope file:
   CONTRACT: <name>
   FILE: <path>
   LOC: <approx>
   ROLE: <what it does>
   ENTRY_POINTS: <external/public functions>
   TRUST_LEVEL: <permissionless | user-role | privileged | callback>

5. Map asset flows:
   [User] → deposit() → [Vault] → ... → [External]

6. Note all complexity hotspots (HIGH/MEDIUM/LOW).

When done emit EXACTLY this block:
{SIGNAL_RECON}
DEADLINE: <ISO date or 'unknown'>
PRIZE_POOL: <USD amount or 'unknown'>
REPO_PATH: <local path to cloned repo>
IN_SCOPE_COUNT: <N>
HOTSPOTS: <comma-separated list of file:function>
"""


def build_hunt_prompt(state: dict) -> str:
    ctx = _context_block(state)
    platform = state["platform"]

    # Platform-specific judging notes
    judging_notes = {
        "sherlock":   "Admin/owner issues OOS unless user funds can be stolen without admin action.",
        "codearena":  "QA/gas goes in separate report. Medium = temporary freeze or value leak with conditions.",
        "cantina":    "Check contest README for judging criteria.",
        "codehawks":  "Check severity matrix in contest docs.",
        "hackenproof": "Check program scope for impact categories.",
    }.get(platform, "")

    return f"""
You are running Phase 2+3 (Attack Surface + Triage) of an audit competition hunt.
This is iteration {state['iteration']}.

{ctx}

JUDGING NOTES FOR {platform.upper()}: {judging_notes}

Your task — find NEW bugs not in the already-confirmed list above:

1. Generate 3-5 NEW attack scenario candidates for this codebase.
   For each candidate:
   - Named attack class (e.g. "cross-function reentrancy via callback")
   - Exact file:line of the vulnerable code
   - Step-by-step attack scenario from the attacker's perspective
   - Estimated severity: High | Medium

2. For each candidate run 4 triage checks:
   CHECK 1 — Is it technically exploitable?
     Trace every function call. Check every require/modifier/access control.
     Verdict: EXPLOITABLE | BLOCKED:<guard> | UNCERTAIN
   CHECK 2 — Is it in scope?
     Check file is in scope. Check platform exclusions.
     Verdict: IN_SCOPE | OUT_OF_SCOPE | CHECK
   CHECK 3 — Is it a known design decision?
     Read NatSpec. Check docs. Check if analogous pattern exists elsewhere.
     Verdict: LIKELY_BUG | KNOWN_DESIGN | UNCERTAIN
   CHECK 4 — Does it meet severity threshold?
     Must be Medium or above for this platform.
     Verdict: HIGH | MEDIUM | LOW

3. For each candidate emit ONE of:
   {SIGNAL_DROP}<candidate-name>:<reason>        ← failed a check
   GO:<candidate-name>:<severity>:<file:line>:<one-line description>

4. If no candidates pass: emit {SIGNAL_CONTINUE}
5. If no new attack surface remains: emit {SIGNAL_EXHAUSTED}

Remember: ALL code is the target (no audit-history filter). High AND Medium count.
Speed over perfection — if it passes all 4 checks, emit GO.
"""


def build_poc_prompt(state: dict, candidate: str, severity: str, location: str, desc: str) -> str:
    ctx = _context_block(state)
    repo = state["repo_path"]
    platform = state["platform"]

    return f"""
You are running Phase 4+5 (PoC + Report) of an audit competition hunt.
Iteration {state['iteration']}.

{ctx}

GO CANDIDATE:
  Name:     {candidate}
  Severity: {severity}
  Location: {location}
  Desc:     {desc}
  Repo:     {repo}

STEP 1 — Write the Foundry PoC.

Requirements:
- File: {repo}/test/poc_{candidate.replace('-','_')}.t.sol
- Test contract name: {candidate.replace('-','_').replace(' ','_')}_PoC
- setUp() mirrors realistic protocol state (use existing test fixtures if available)
- test_control_normalOperation() — must PASS
- test_exploit_{candidate.replace('-','_').replace(' ','_')}() — must demonstrate impact
- Comments explaining each step
- No vm.mockCall unless absolutely necessary (prefer real contract interactions)

STEP 2 — Run the test.

Run this command and capture the full output:
forge test --match-contract {candidate.replace('-','_').replace(' ','_')}_PoC -vv
(use FOUNDRY_PROFILE=<profile> if the repo has multiple profiles)

STEP 3 — Evaluate results.

If BOTH tests pass as expected:
  Emit: {SIGNAL_FOUND}{candidate}:{severity}
  Then proceed to Step 4.

If tests fail (or control fails / exploit doesn't demonstrate impact):
  Emit: {SIGNAL_DROP}{candidate}:<exact technical reason the test failed>
  STOP — do not write a report for a failing PoC.

STEP 4 — Write the report (only if tests passed).

Platform: {platform}

{'Write a GitHub issue in Markdown with these exact sections:' if platform == 'sherlock' else 'Write a Markdown file with these exact sections:'}

{_report_template(platform)}

Save the report to: audit-reports/{candidate}-report.md
Emit: {SIGNAL_REPORT}{candidate}:audit-reports/{candidate}-report.md
"""


def _report_template(platform: str) -> str:
    if platform == "sherlock":
        return """
## Summary
[One sentence: what is vulnerable and what happens.]

## Vulnerability Detail
[2-4 paragraphs: root cause, exact code that is wrong, why it's a bug not design decision.]
```solidity
// Vulnerable code (10-25 lines, exact file:line)
```

## Impact
[Specific: "attacker can steal 100% of deposited USDC" not "funds at risk"]

## Code Snippet
[GitHub permalink to the exact lines]

## Tool Used
Manual Review

## Recommendation
```solidity
// Before:
// After:
+ fix
```

## Proof of Concept
[Paste full Foundry test + forge test command + expected output]
"""
    elif platform == "codearena":
        return """
## Lines of code
[GitHub permalink]

## Vulnerability details

### Impact
[Severity and why]

### Proof of Concept
[Full Foundry test code + forge test command + expected output]

### Tools Used
Manual review

### Recommended Mitigation Steps
[Fix — 1-5 lines max]
"""
    else:  # cantina, codehawks, hackenproof
        return """
## Title
[Severity] ContractName — Short description of bug

## Summary
[One sentence]

## Vulnerability Detail
[Root cause, exact code, why it's a bug]
```solidity
// Vulnerable code with file:line
```

## Impact
[Specific impact]

## Proof of Concept
[Full Foundry test + run command + expected output]

## Recommended Mitigation
[Fix — 1-5 lines]
"""


# ─── CLAUDE INVOCATION ────────────────────────────────────────────────────────

def run_claude(prompt: str, iteration: int, phase: str) -> str:
    LOG_DIR.mkdir(exist_ok=True)
    REPORTS_DIR.mkdir(exist_ok=True)

    log_file = LOG_DIR / f"iter{iteration:02d}_{phase}_{int(time.time())}.log"

    print(f"\n{'─'*64}")
    print(f"  [iter {iteration}] [{phase}] running claude...")
    print(f"  log → {log_file}")
    print(f"{'─'*64}")

    prompt_file = LOG_DIR / f"prompt_{iteration}_{phase}.txt"
    prompt_file.write_text(prompt)

    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=900,  # 15 min — PoC phase needs time to write + run tests
            cwd=str(Path.cwd()),
        )
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        output = f"[TIMEOUT] Phase {phase} exceeded 15 minutes"
    except FileNotFoundError:
        print("\n[ERROR] 'claude' CLI not found. Install: npm install -g @anthropic-ai/claude-code")
        sys.exit(1)

    log_file.write_text(output)
    preview = output[:3000]
    print(preview)
    if len(output) > 3000:
        print(f"  ... (truncated — full output in {log_file})")

    return output


# ─── SIGNAL PARSING ───────────────────────────────────────────────────────────

def parse_output(output: str) -> dict:
    lines = output.split("\n")

    # FOUND: AUDIT_SIGNAL:FOUND:name:severity
    for line in lines:
        if SIGNAL_FOUND in line:
            rest = line.split(SIGNAL_FOUND)[-1].strip()
            parts = rest.split(":", 1)
            name = parts[0].strip()
            severity = parts[1].strip() if len(parts) > 1 else "Medium"
            return {"action": "FOUND", "finding": name, "severity": severity}

    # EXHAUSTED
    if SIGNAL_EXHAUSTED in output:
        return {"action": "EXHAUSTED"}

    # REPORT: AUDIT_SIGNAL:REPORT:name:file-path
    for line in lines:
        if SIGNAL_REPORT in line:
            rest = line.split(SIGNAL_REPORT)[-1].strip()
            parts = rest.split(":", 1)
            if len(parts) == 2:
                return {"action": "REPORT", "finding": parts[0].strip(), "file": parts[1].strip()}

    # GO: GO:name:severity:location:desc
    for line in lines:
        if line.strip().startswith("GO:"):
            parts = line.strip().split(":", 4)
            if len(parts) >= 5:
                return {
                    "action": "GO",
                    "candidate": parts[1].strip(),
                    "severity":  parts[2].strip(),
                    "location":  parts[3].strip(),
                    "desc":      parts[4].strip(),
                }
            elif len(parts) >= 2:
                return {
                    "action":    "GO",
                    "candidate": parts[1].strip(),
                    "severity":  parts[2].strip() if len(parts) > 2 else "Medium",
                    "location":  parts[3].strip() if len(parts) > 3 else "unknown",
                    "desc":      parts[4].strip() if len(parts) > 4 else "",
                }

    # DROPs
    drops = []
    for line in lines:
        if SIGNAL_DROP in line:
            rest = line.split(SIGNAL_DROP)[-1].strip()
            if ":" in rest:
                candidate, reason = rest.split(":", 1)
                drops.append({"candidate": candidate.strip(), "reason": reason.strip()})
            else:
                drops.append({"candidate": rest, "reason": "unspecified"})
    if drops:
        return {"action": "DROP", "drops": drops}

    if SIGNAL_CONTINUE in output:
        return {"action": "CONTINUE"}

    return {"action": "CONTINUE", "note": "no signal detected"}


# ─── SHERLOCK AUTO-SUBMIT ─────────────────────────────────────────────────────

def sherlock_auto_submit(finding: dict, state: dict) -> bool:
    """Submit finding as a GitHub issue in the contest repo. Returns True on success."""
    report_file = finding.get("report_file")
    if not report_file or not Path(report_file).exists():
        print(f"  [!] No report file for {finding['name']} — skipping auto-submit")
        return False

    repo_path = state["repo_path"]
    if not repo_path or not Path(repo_path).exists():
        print(f"  [!] Repo path not found — skipping auto-submit")
        return False

    report_body = Path(report_file).read_text()
    title_match = re.search(r'^## Summary\s*\n(.+)', report_body, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else finding["name"]

    print(f"\n  [→] Auto-submitting to Sherlock via gh issue create...")
    try:
        result = subprocess.run(
            ["gh", "issue", "create",
             "--title", f"[{finding['severity']}] {title}",
             "--body", report_body,
             "--label", finding["severity"].lower()],
            capture_output=True,
            text=True,
            cwd=repo_path,
            timeout=60,
        )
        if result.returncode == 0:
            issue_url = result.stdout.strip()
            print(f"  [✓] Submitted: {issue_url}")
            finding["submitted"] = True
            finding["issue_url"] = issue_url
            return True
        else:
            print(f"  [!] gh issue create failed: {result.stderr}")
            return False
    except Exception as e:
        print(f"  [!] Auto-submit error: {e}")
        return False


# ─── MAIN LOOP ────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    contest_url  = sys.argv[1]
    platform     = sys.argv[2].lower()
    max_iter     = int(sys.argv[3]) if len(sys.argv) > 3 else MAX_ITERATIONS_DEFAULT
    rpc          = sys.argv[4] if len(sys.argv) > 4 else ""

    valid_platforms = {"sherlock", "codearena", "cantina", "codehawks", "hackenproof"}
    if platform not in valid_platforms:
        print(f"[ERROR] Unknown platform '{platform}'. Use: {', '.join(valid_platforms)}")
        sys.exit(1)

    state = load_state()
    state["contest_url"] = contest_url
    state["platform"]    = platform
    state["rpc"]         = rpc
    save_state(state)

    print(f"""
╔══════════════════════════════════════════════════════════════╗
  AUDIT COMPETITION HUNT LOOP
  Contest:  {contest_url}
  Platform: {platform}
  Max iter: {max_iter}
  State:    {STATE_FILE}
╚══════════════════════════════════════════════════════════════╝
""")

    # ── Phase 1: Recon (once) ──────────────────────────────────────────────────
    if not state["recon_done"]:
        print("\n[PHASE 1] CONTEST RECON + CODEBASE MAPPING")
        output = run_claude(build_recon_prompt(state), 0, "recon")

        if SIGNAL_RECON in output:
            state["recon_done"] = True
            for line in output.split("\n"):
                if line.startswith("DEADLINE:"):
                    state["deadline"] = line.split(":", 1)[1].strip()
                elif line.startswith("PRIZE_POOL:"):
                    state["prize_pool"] = line.split(":", 1)[1].strip()
                elif line.startswith("REPO_PATH:"):
                    state["repo_path"] = line.split(":", 1)[1].strip()
            save_state(state)
            print(f"\n[✓] Recon complete.")
            print(f"    Deadline:  {state.get('deadline', 'unknown')}")
            print(f"    Prize:     {state.get('prize_pool', 'unknown')}")
            print(f"    Repo:      {state.get('repo_path', 'unknown')}")
        else:
            print("\n[!] Recon did not emit RECON_DONE. Check logs. Continuing.")
            state["recon_done"] = True
            # Try to extract repo path anyway
            match = re.search(r'REPO_PATH:\s*(.+)', output)
            if match:
                state["repo_path"] = match.group(1).strip()
            save_state(state)

    # ── Phase 2–5: Hunt loop ───────────────────────────────────────────────────
    while state["iteration"] < max_iter:
        state["iteration"] += 1
        save_state(state)

        n_confirmed = len(confirmed_findings(state))
        print(f"\n{'═'*64}")
        print(f"  ITERATION {state['iteration']} / {max_iter}")
        print(f"  Confirmed findings: {n_confirmed}")
        print(f"  Drops so far:       {len(state['drop_history'])}")
        print(f"  PoC failures:       {len(state['poc_failures'])}")
        print(f"{'═'*64}")

        # ── Phase 2+3: Hunt + Triage ───────────────────────────────────────
        output = run_claude(build_hunt_prompt(state), state["iteration"], "hunt")
        result = parse_output(output)

        if result["action"] == "EXHAUSTED":
            print("\n[✗] Attack surface exhausted.")
            break

        if result["action"] == "DROP":
            for d in result["drops"]:
                state["drop_history"].append({
                    "iteration": state["iteration"],
                    "candidate": d["candidate"],
                    "reason":    d["reason"],
                    "lesson":    "",
                })
            lessons = re.findall(r'Lesson:\s*(.+)', output)
            state["lessons"].extend(lessons)
            save_state(state)
            print(f"\n[→] Candidates dropped. Feeding {len(result['drops'])} reasons into next iteration.")
            continue

        if result["action"] in ("CONTINUE",):
            print(f"\n[→] No GO candidates this iteration. Continuing.")
            continue

        if result["action"] == "GO":
            candidate = result["candidate"]
            severity  = result.get("severity", "Medium")
            location  = result.get("location", "unknown")
            desc      = result.get("desc", "")

            print(f"\n[GO] {candidate} ({severity}) — {desc}")
            print("[PHASE 4+5] Writing PoC + running forge test...")

            # ── Phase 4+5: PoC + Report ────────────────────────────────────
            poc_output = run_claude(
                build_poc_prompt(state, candidate, severity, location, desc),
                state["iteration"],
                "poc",
            )
            poc_result = parse_output(poc_output)

            if poc_result["action"] == "FOUND":
                finding = {
                    "name":        poc_result["finding"],
                    "severity":    poc_result.get("severity", severity),
                    "file":        location,
                    "status":      "CONFIRMED",
                    "report_file": None,
                    "submitted":   False,
                    "issue_url":   None,
                    "iteration":   state["iteration"],
                }

                # Check if report was also generated in same output
                report_result = None
                for line in poc_output.split("\n"):
                    if SIGNAL_REPORT in line:
                        rest = line.split(SIGNAL_REPORT)[-1].strip()
                        parts = rest.split(":", 1)
                        if len(parts) == 2:
                            report_result = {"file": parts[1].strip()}

                if report_result:
                    finding["report_file"] = report_result["file"]
                    print(f"\n[✓] FINDING CONFIRMED: {finding['name']} ({finding['severity']})")
                    print(f"    Report: {finding['report_file']}")
                else:
                    print(f"\n[✓] FINDING CONFIRMED: {finding['name']} ({finding['severity']})")
                    print(f"    [!] Report file not detected in output — check logs")

                state["findings"].append(finding)
                save_state(state)

                # Sherlock: auto-submit
                if platform == "sherlock" and finding.get("report_file"):
                    sherlock_auto_submit(finding, state)
                    save_state(state)

                # Don't stop — keep hunting for more bugs
                print(f"\n[→] Continuing hunt for more findings...")
                continue

            else:
                # PoC failed
                failure_reason = "unknown"
                if poc_result.get("drops"):
                    failure_reason = poc_result["drops"][0].get("reason", "unknown")

                state["poc_failures"].append({
                    "iteration":      state["iteration"],
                    "candidate":      candidate,
                    "failure_reason": failure_reason,
                })
                state["drop_history"].append({
                    "iteration": state["iteration"],
                    "candidate": candidate,
                    "reason":    f"PoC failed: {failure_reason}",
                    "lesson":    f"Triage passed but PoC revealed: {failure_reason}",
                })
                save_state(state)
                print(f"\n[✗] PoC failed for '{candidate}': {failure_reason}")
                print("[→] Feeding failure into next iteration.")
                continue

    # ── Final summary ──────────────────────────────────────────────────────────
    _print_summary(state)


def _print_summary(state: dict):
    confirmed = confirmed_findings(state)

    print(f"""
{'═'*64}
  AUDIT HUNT COMPLETE
  Contest:    {state['contest_url']}
  Iterations: {state['iteration']}
  Drops:      {len(state['drop_history'])}
  PoC fails:  {len(state['poc_failures'])}
{'═'*64}
""")

    if confirmed:
        print(f"  ✅ CONFIRMED FINDINGS ({len(confirmed)}):\n")
        for i, f in enumerate(confirmed, 1):
            status = "submitted ✓" if f.get("submitted") else "ready to submit"
            url    = f.get("issue_url", "")
            print(f"  {i}. [{f['severity']}] {f['name']}")
            print(f"     File:   {f['file']}")
            print(f"     Report: {f.get('report_file', 'not found')}")
            if url:
                print(f"     Issue:  {url}")
            print(f"     Status: {status}\n")
    else:
        print("  No confirmed findings this run.")
        print("  → Try increasing max_iterations or a different attack angle.")
        print(f"  → Drops logged in: {STATE_FILE}")

    # Write final summary file
    summary_file = REPORTS_DIR / "summary.md"
    REPORTS_DIR.mkdir(exist_ok=True)
    with open(summary_file, "w") as f:
        f.write(f"# Audit Hunt Summary\n\n")
        f.write(f"**Contest:** {state['contest_url']}\n")
        f.write(f"**Platform:** {state['platform']}\n")
        f.write(f"**Date:** {datetime.now().strftime('%Y-%m-%d')}\n\n")
        f.write(f"## Confirmed Findings\n\n")
        for finding in confirmed:
            f.write(f"### [{finding['severity']}] {finding['name']}\n")
            f.write(f"- File: `{finding['file']}`\n")
            f.write(f"- Report: `{finding.get('report_file', 'N/A')}`\n")
            if finding.get("issue_url"):
                f.write(f"- Issue: {finding['issue_url']}\n")
            f.write("\n")
        f.write(f"## Drop History\n\n")
        for d in state["drop_history"]:
            f.write(f"- **[{d['iteration']}]** {d['candidate']}: {d['reason']}\n")
    print(f"\n  Summary written to: {summary_file}")


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()
