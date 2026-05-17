# Handoff Protocol

> **Parent document:** [`../orchestrator.md`](../orchestrator.md)
> **Purpose:** How the orchestrator delegates tasks to sub-agents, and how sub-agents return results.

---

## Core Principle: Minimal Context, Maximum Clarity

Sub-agents perform better with tight scope and clear deliverables. The orchestrator's job is to frame the task so precisely that the sub-agent doesn't need to guess. Over-context causes drift; under-context causes wrong assumptions.

---

## Delegation Template

When spinning up a sub-agent, the orchestrator constructs a prompt using this template:

```
ROLE: [Researcher | Builder | Backtester | Reviewer]
PROMPT FILE: prompts/sub_agents/{role}.md

TASK: {one-sentence description of the deliverable}

CONTEXT:
- Repo: /Users/akshayapsingi/Projects/nautilus_ai_trading_agent
- Key files to read: {list of 2-5 specific files relevant to this task}
- Current architecture: {brief description — 2-3 sentences max}
- Constraints: {any hard constraints — no new deps, must follow X pattern, etc.}

HYPOTHESIS (if applicable):
{paste the hypothesis from plan.md}

ACCEPTANCE CRITERIA:
1. {specific, measurable criterion}
2. {specific, measurable criterion}
3. {specific, measurable criterion}

KILL CRITERIA:
- {condition that means this task should be abandoned}

DELIVERABLE FORMAT:
{what the sub-agent should return — code? findings? metrics?}
```

---

## What Each Agent Type Needs

### Researcher
- The specific question (not "research microstructure" — that's too broad)
- Which files/docs to read for context
- Whether to web-search (NautilusTrader docs, Binance API, academic papers)
- Does NOT need: code from other sub-agents, the orchestrator's reasoning, backtest results

### Builder
- Exact specification: inputs, outputs, behavior
- File path where the code goes
- Pattern file to mirror (e.g., "follow `indicators/technical_manager.py` structure")
- Coding standards reference (`AGENTS.md`)
- Does NOT need: why the orchestrator chose this approach over alternatives, full research findings, other sub-agents' work in progress

### Backtester
- The code to test (strategy file or feature implementation)
- Data specification: instrument, date range, source
- Metrics to compute (the full table from `backtester.md`)
- Baseline metrics (if comparing against existing system)
- Hypothesis + prediction (what result would constitute pass vs. kill)
- Does NOT need: the source code of other components not being tested, orchestrator's prior failed attempts

### Reviewer
- ONLY the artifact being reviewed (code, design doc, or backtest result)
- The stated goal of the artifact
- Repo style references (`AGENTS.md`, pattern files)
- Does NOT need: orchestrator's reasoning, how many iterations it took, what alternatives were considered, emotional context. This is the whole point — fresh eyes.

---

## Receiving Results

When a sub-agent returns, the orchestrator must:

1. **Check format compliance.** Did the sub-agent return the structured format from its prompt file? If not, the output is harder to parse and compare.

2. **Check against acceptance criteria.** Go through each criterion explicitly. Pass/fail each one.

3. **Check against kill criteria.** If a kill criterion is met, the hypothesis dies. Don't negotiate.

4. **Check for surprises.** Did the sub-agent find something the orchestrator didn't anticipate? This is valuable — update the hypothesis or create a new one.

5. **Update plan.md.** Record the outcome (hypothesis status change, metric update, new information).

6. **Decide next action:**
   - All criteria met → advance to next pipeline stage
   - Some criteria failed but fixable → delegate a focused fix to a Builder agent
   - Kill criterion hit → kill the hypothesis, update Graveyard, move on
   - Surprising finding → may warrant a new hypothesis

---

## Parallel Delegation

When delegating to multiple sub-agents in parallel:
- Each agent must be truly independent (no shared state, no order dependency)
- The orchestrator tracks all pending delegations in plan.md under "Active Hypotheses"
- When results return, process them in any order — each stands on its own

Example of valid parallel delegation:
- Researcher A: "What is the NautilusTrader API for order book subscription?"
- Researcher B: "What is the Binance REST endpoint for funding rate?"
- These are independent questions — run them at the same time.

Example of INVALID parallel delegation:
- Builder A: "Build the OFI feature"
- Backtester B: "Backtest the OFI feature"
- B depends on A's output — these must be sequential.

---

## Context Size Discipline

The orchestrator's context window is its most valuable resource. Every sub-agent call that dumps 2000 lines of code back into the orchestrator's context is wasteful. Rules:

1. **Sub-agents summarize.** Results come as structured tables and verdicts, not walls of code.
2. **Code stays in files.** The Builder writes to disk. The orchestrator reads the file only if it needs to review a specific section.
3. **The orchestrator directs, not debugs.** If a Builder's code has a bug, don't debug it in the orchestrator context — spin a new Builder to fix it with the error message as context.
4. **Hypothesis-level tracking.** The orchestrator tracks hypotheses and metrics, not implementation details. `plan.md` records "H-03 passed Stage 2 with 57% accuracy" — not the 150-line implementation of the OFI calculator.
