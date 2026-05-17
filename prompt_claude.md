You are a senior quant engineer with deep expertise in algorithmic trading, market microstructure, and LLM-augmented system design. I need you to critically evaluate and redesign the architecture below for a crypto intraday trading agent.

── MANDATE ──────────────────────────────────────────
1. Use your full quant knowledge — don't hold back on signal theory, execution mechanics, or risk frameworks.
2. Web-search any area where you need current data: latest NautilusTrader docs, DeepSeek API limits, Binance WS schema, relevant academic microstructure papers, or production system case studies.
3. Ask me clarifying questions for anything ambiguous before committing to a design. Number them and wait for my answers before finalising architecture.
─────────────────────────────────────────────────────

── CONTEXT ──────────────────────────────────────────
Repo: https://github.com/Akshay-a/nautilus_ai_trading_agent
Stack: NautilusTrader (forked) · DeepSeek LLM · Binance WS feeds
Target: 100–200 intraday crypto trades/day, backtested 60%+ win rate
Constraint: LLM must NOT be in the hot execution path (latency + context bloat risk).
─────────────────────────────────────────────────────

── CURRENT PLAN (your starting point) ───────────────
Data layer   → Binance WS: L2 order book, trade ticks, funding rate, OI
Feature layer → MicrostructureManager: ring-buffer, real-time analytical features
Algo layer   → Rule-based signals (OFI threshold, funding z-score, OI divergence,
                Markov regime filter, vol-adjusted stops, kill-switch)
               → Executes trades directly if confidence > threshold
LLM layer    → Receives compressed snapshot every 5–10 min
               → Role: regime bias, veto power, position size modifier

── TWO DESIGN PROBLEMS TO SOLVE ─────────────────────

Problem 1 · Context Engineering for the LLM layer
Design the exact structure of the "compressed micro snapshot" sent to the LLM every 5–10 min. Think carefully about:
  • What data maximises the LLM's regime-detection signal (not raw ticks — distilled features)?
  • What schema / token budget keeps it within ~800–1200 tokens while retaining decision-relevant signal?
  • How do you prevent context bloat across multiple snapshot rounds (sliding window? summary compression? stateless per-call?)?
  • Should the LLM have memory of prior regimes, or reason fresh each time?
  • How do you prompt-engineer the LLM to output structured, machine-parseable decisions (veto / size_modifier / regime_label) rather than prose?

Problem 2 · Decision & Execution Layer design
Design the full decision pipeline from signal generation to trade lifecycle:
  • How should algo signals and LLM outputs be combined (gate, weight, override hierarchy)?
  • How do you implement auto-close triggers: price-target hit, trailing stop, time-based expiry — and which layer owns each?
  • When should the LLM be elevated to "primary judge" vs relegated to advisory-only? Design the state machine for this.
  • How do you handle the LLM being slow/unavailable — graceful degradation without halting the algo layer?
  • What does the trade state machine look like? (PENDING → OPEN → [STOP_HIT | TARGET_HIT | LLM_VETO | TIMEOUT] → CLOSED)
─────────────────────────────────────────────────────

── EXPECTED OUTPUT ──────────────────────────────────
A. Clarifying questions (numbered, ask first if anything is ambiguous — do not skip this step)
B. Revised architecture diagram (ASCII or structured) with clear layer ownership
C. Context snapshot schema: exact fields, data types, token estimate
D. LLM prompt template with system prompt + snapshot injection point + output schema
E. Decision layer pseudocode / state machine (language-agnostic)
F. Risk flags and failure modes you'd instrument in production
G. Any alternative architectures worth comparing (e.g. RLHF-tuned small model vs general LLM, or event-driven vs polling for the LLM trigger)
─────────────────────────────────────────────────────

Go deep. Reference academic work on order flow imbalance, microstructure noise, and regime detection where relevant. Cite any production systems or papers you find via web search. This will directly inform a production build.