"""
DeepSeek AI Integration Module for NautilusTrader

Provides AI-powered market analysis and trading signal generation.
"""

import json
import re
import logging
from typing import Dict, Any, Optional
from datetime import datetime

from openai import OpenAI


class DeepSeekAnalyzer:
    """
    DeepSeek AI analyzer for generating trading signals.

    Analyzes market conditions using technical indicators, K-line patterns,
    and sentiment data to produce structured trading signals.
    """

    # Friction assumptions (Bybit linear perps). Round-trip = entry + exit.
    # Taker fee ~5.5 bps/side -> 11 bps round trip. Spread is added live from
    # microstructure; a small slippage buffer covers market-order impact.
    ROUND_TRIP_FEE_BPS = 11.0
    SLIPPAGE_BUFFER_BPS = 2.0

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-chat",
        temperature: float = 0.1,
        base_url: str = "https://api.deepseek.com",
        max_retries: int = 2,
        instrument_id: str = "",
        bar_type: str = "",
        nautilus_logger=None,
    ):
        """
        Initialize DeepSeek analyzer.

        Parameters
        ----------
        api_key : str
            DeepSeek API key
        model : str
            Model name (default: deepseek-chat)
        temperature : float
            Temperature for response generation (0.0-1.0)
        base_url : str
            API base URL
        max_retries : int
            Maximum retry attempts on failure
        instrument_id : str
            Active trading instrument id (e.g. ETHUSDT-LINEAR.BYBIT)
        bar_type : str
            Active bar type string for timeframe context
        nautilus_logger : optional
            NautilusTrader logger instance (self.log from Strategy) to route
            errors/warnings into the JSON log file. Falls back to standard
            Python logging if not provided.
        """
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.temperature = temperature
        self.max_retries = max_retries

        # Use NautilusTrader logger if provided, otherwise fall back to stdlib
        self._nautilus_log = nautilus_logger
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

        # Track signal history
        self.signal_history = []
        self._context_key = None
        self._set_instrument_context(instrument_id=instrument_id, bar_type=bar_type, reset_history=False)

    def _derive_timeframe_label(self, bar_type: str) -> str:
        """Build readable timeframe label from Nautilus bar_type."""
        if not bar_type:
            return "5-minute"
        text = bar_type.upper()
        m = re.search(r"-([0-9]+)-(MINUTE|HOUR|DAY)-", text)
        if not m:
            return "5-minute"
        qty = int(m.group(1))
        unit = m.group(2).lower()
        return f"{qty}-{unit}"

    def _build_instrument_context(self, instrument_id: str, bar_type: str) -> Dict[str, str]:
        """Extract pair/venue/unit metadata from instrument_id and bar_type."""
        instr = str(instrument_id or "UNKNOWNUSDT-LINEAR.UNKNOWN")
        symbol = instr.split("-")[0] if "-" in instr else instr.split(".")[0]
        venue = instr.split(".")[-1] if "." in instr else "UNKNOWN"

        quote = ""
        base = symbol
        for candidate in ("USDT", "USDC", "USD", "BTC", "ETH", "EUR"):
            if symbol.endswith(candidate) and len(symbol) > len(candidate):
                quote = candidate
                base = symbol[:-len(candidate)]
                break

        pair_label = f"{base}/{quote}" if quote else symbol
        timeframe_label = self._derive_timeframe_label(bar_type)
        return {
            "instrument_id": instr,
            "bar_type": str(bar_type or ""),
            "venue": venue,
            "symbol": symbol,
            "base_asset": base,
            "quote_asset": quote or "QUOTE",
            "pair_label": pair_label,
            "timeframe_label": timeframe_label,
        }

    def _set_instrument_context(
        self,
        instrument_id: str,
        bar_type: str,
        reset_history: bool,
    ) -> None:
        """Set active prompt context and optionally clear stale signal history."""
        ctx = self._build_instrument_context(instrument_id=instrument_id, bar_type=bar_type)
        new_key = (ctx["instrument_id"], ctx["bar_type"])
        previous_key = self._context_key
        changed = previous_key is not None and previous_key != new_key

        self.instrument_id = ctx["instrument_id"]
        self.bar_type = ctx["bar_type"]
        self.venue = ctx["venue"]
        self.symbol = ctx["symbol"]
        self.base_asset = ctx["base_asset"]
        self.quote_asset = ctx["quote_asset"]
        self.pair_label = ctx["pair_label"]
        self.timeframe_label = ctx["timeframe_label"]
        self._context_key = new_key

        if changed and reset_history and previous_key is not None:
            stale = len(self.signal_history)
            self.signal_history.clear()
            self._log_warning(
                f"⚠️ Instrument context changed ({previous_key[0]} -> {new_key[0]}), "
                f"cleared stale signal_history={stale}"
            )

    def _refresh_context_from_price_data(self, price_data: Dict[str, Any]) -> None:
        """Refresh analyzer context from live payload metadata."""
        instrument_id = str(price_data.get("instrument_id") or self.instrument_id)
        bar_type = str(price_data.get("bar_type") or self.bar_type)
        self._set_instrument_context(
            instrument_id=instrument_id,
            bar_type=bar_type,
            reset_history=True,
        )

    def _build_system_prompt(self) -> str:
        """Build dynamic system prompt tied to active instrument context."""
        return (
            f"You are a regime-aware intraday move-capture trader on {self.pair_label} perpetuals "
            f"({self.timeframe_label} bars, {self.venue}).\n\n"
            "CORE MOVE-CAPTURE RULES:\n"
            "1. Optimize net risk-adjusted move capture, not activity or tiny scalp wins.\n"
            "2. Think in regime, structure, ATR, daily/weekly volatility, and order-flow persistence.\n"
            "3. If in position and invalidation is intact, default to HOLD_POSITION.\n"
            "4. Do not exit on small giveback alone; exit only on invalidation hit, clear structure failure, confirmed reversal, "
            "or large opposing micro shift with price moving toward invalidation.\n"
            "5. Reject friction-sized churn: if expected move is not clearly beyond round-trip friction, prefer NO_ACTION.\n"
            "6. HOLD/NO_ACTION is correct when the prior thesis remains valid or edge is mixed.\n\n"
            "BYBIT EXCHANGE POSITION/OPEN_ORDERS IS THE SOURCE OF TRUTH for actual exposure state.\n\n"
            "Respond ONLY in English. Output a single JSON object. No markdown, no prose outside JSON."
        )

    @staticmethod
    def _normalize_technical_labels(technical_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize legacy/non-English trend labels for stable prompt semantics.
        """
        if not isinstance(technical_data, dict):
            return technical_data

        trend_map = {
            "强势上涨": "strong_up",
            "强势下跌": "strong_down",
            "震荡整理": "mixed",
            "上涨": "up",
            "下跌": "down",
        }
        normalized = dict(technical_data)
        for key in ("overall_trend", "short_term_trend", "medium_term_trend"):
            value = normalized.get(key)
            if isinstance(value, str):
                normalized[key] = trend_map.get(value, value)
        return normalized

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        """Return True when text contains CJK characters."""
        return bool(re.search(r"[\u4e00-\u9fff]", text or ""))

    def _signal_contains_non_english_content(self, signal_data: Dict[str, Any]) -> bool:
        """Heuristic guard for non-English synthesis fields."""
        for key in ("thesis", "reason", "regime", "invalidation", "execution_note", "volume_note"):
            value = signal_data.get(key)
            if isinstance(value, str) and self._contains_cjk(value):
                return True
        return False

    @staticmethod
    def _compact_previous_signal(previous: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Compact previous signal snapshot for payload logging."""
        if not isinstance(previous, dict):
            return None
        keys = (
            "signal",
            "confidence",
            "position_action",
            "regime",
            "trend_strength",
            "risk_assessment",
            "thesis",
            "invalidation",
            "execution_note",
            "target_r",
            "timestamp",
        )
        compact = {k: previous.get(k) for k in keys if k in previous}
        thesis = compact.get("thesis")
        if isinstance(thesis, str) and len(thesis) > 220:
            compact["thesis"] = thesis[:220] + "..."
        return compact

    @staticmethod
    def _format_payload_summary(payload: Dict[str, Any]) -> str:
        """Compact one-line payload summary for INFO logs."""
        tech = payload.get("technical") or {}
        micro = payload.get("microstructure") or {}
        pos = payload.get("position") or {}
        side = pos.get("side") if isinstance(pos, dict) else None
        qty = pos.get("quantity") if isinstance(pos, dict) else None
        upnl = pos.get("unrealized_pnl") if isinstance(pos, dict) else None
        return (
            "🤖 LLM Context: "
            f"px={payload.get('price')} "
            f"pos={side or 'flat'} qty={qty if qty is not None else '-'} "
            f"upnl={upnl if upnl is not None else '-'} "
            f"trend={tech.get('overall_trend')} "
            f"rsi={tech.get('rsi')} "
            f"rvol={tech.get('rvol')} "
            f"vol_regime={tech.get('volume_regime')} "
            f"tfi={micro.get('trade_flow_imbalance')}"
        )

    def _log_info(self, msg: str):
        if self._nautilus_log:
            self._nautilus_log.info(msg)
        else:
            self.logger.info(msg)

    def _log_warning(self, msg: str):
        if self._nautilus_log:
            self._nautilus_log.warning(msg)
        else:
            self.logger.warning(msg)

    def _log_error(self, msg: str):
        if self._nautilus_log:
            self._nautilus_log.error(msg)
        else:
            self.logger.error(msg)

    def _log_debug(self, msg: str):
        if self._nautilus_log:
            self._nautilus_log.debug(msg)
        else:
            self.logger.debug(msg)

    def analyze(
        self,
        price_data: Dict[str, Any],
        technical_data: Dict[str, Any],
        sentiment_data: Optional[Dict[str, Any]] = None,
        current_position: Optional[Dict[str, Any]] = None,
        risk_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Analyze market conditions and generate trading signal.

        Parameters
        ----------
        price_data : Dict
            Current price and K-line data
        technical_data : Dict
            Technical indicator values
        sentiment_data : Dict, optional
            Market sentiment data
        current_position : Dict, optional
            Current position information
        risk_context : Dict, optional
            Exchange account, open order, recent execution, and realized P&L context

        Returns
        -------
        Dict
            Trading signal with structure:
            {
                "signal": "BUY|SELL|HOLD",
                "confidence": "HIGH|MEDIUM|LOW",
                "reason": str,
                "stop_loss/take_profit": required floats for entry actions,
                "regime/thesis/invalidation/execution_note/volume_note": synthesis fields,
                "timestamp": str
            }
        """
        self._refresh_context_from_price_data(price_data)
        technical_data = self._normalize_technical_labels(technical_data)

        for attempt in range(self.max_retries):
            try:
                signal = self._analyze_with_retry(
                    price_data, technical_data, sentiment_data, current_position, risk_context
                )

                if signal and not signal.get("is_fallback", False):
                    if self._signal_contains_non_english_content(signal):
                        self._log_warning("⚠️ Non-English synthesis fields detected; using signal as-is.")
                    self._record_signal(signal)
                    return signal

                self._log_warning(f"⚠️ Attempt {attempt + 1} returned fallback, retrying...")

            except Exception as e:
                self._log_error(f"❌ Analysis attempt {attempt + 1} failed: {type(e).__name__}: {e}")
                if attempt == self.max_retries - 1:
                    fallback = self._emit_fallback(price_data)
                    self._record_signal(fallback)
                    return fallback

        fallback = self._emit_fallback(price_data)
        self._record_signal(fallback)
        return fallback

    def _finalize_signal_compat(self, signal_data: Dict[str, Any], price_data: Dict[str, Any]) -> None:
        """
        Bridge synthesis-era schema to downstream expectations (telemetry, brackets).

        Keeps synthesis-era fields compatible with downstream telemetry.
        """
        thesis_raw = signal_data.get("thesis") or ""
        legacy_raw = signal_data.get("reason") or ""

        thesis = thesis_raw.strip() if isinstance(thesis_raw, str) else str(thesis_raw)
        legacy = legacy_raw.strip() if isinstance(legacy_raw, str) else str(legacy_raw)

        if thesis:
            signal_data["reason"] = thesis
            signal_data["thesis"] = thesis
        elif legacy:
            signal_data["reason"] = legacy
            signal_data["thesis"] = legacy
        signal_data.setdefault("trend_strength", "MODERATE")

    def _analyze_with_retry(
        self,
        price_data: Dict[str, Any],
        technical_data: Dict[str, Any],
        sentiment_data: Optional[Dict[str, Any]],
        current_position: Optional[Dict[str, Any]],
        risk_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Internal analysis with single attempt."""

        # Build comprehensive prompt
        prompt = self._build_analysis_prompt(
            price_data, technical_data, sentiment_data, current_position, risk_context
        )
        prompt_payload = self._build_prompt_payload(
            price_data=price_data,
            technical_data=technical_data,
            sentiment_data=sentiment_data,
            current_position=current_position,
            risk_context=risk_context,
        )
        self._log_info(self._format_payload_summary(prompt_payload))
        self._log_debug(f"🤖 LLM Prompt Payload: {json.dumps(prompt_payload, ensure_ascii=False)}")
        micro_included = self._has_microstructure_features(price_data.get("microstructure"))
        self._log_info(
            f"🤖 Prompt microstructure section included: {'true' if micro_included else 'false'}"
        )

        # Call DeepSeek API
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": self._build_system_prompt()
                },
                {"role": "user", "content": prompt}
            ],
            stream=False,
            temperature=self.temperature
        )

        # Parse response
        message = response.choices[0].message
        result = message.content or ""
        reasoning_content = getattr(message, "reasoning_content", None) or ""
        self._log_info(f"🤖 DeepSeek Raw Response: {result[:500]}")
        if reasoning_content:
            self._log_debug(
                f"🧠 DeepSeek reasoning captured: {len(reasoning_content)} chars"
            )

        signal_data = self._safe_parse_json(result)

        if signal_data is None:
            self._log_error(f"❌ JSON parse failed for response: {result[:200]}")
            fb = self._emit_fallback(price_data)
            rc = fb.get("reasoning_content") or ""
            snippet = reasoning_content.strip()[:2000] if reasoning_content else ""
            fb["reasoning_content"] = (
                rc + ("\nreasoner:" + snippet if snippet else "")
                + "\n parse_fail:" + result[:480]
            )
            return fb

        self._log_info(
            f"🤖 LLM Response JSON: {json.dumps(signal_data, ensure_ascii=False)}"
        )

        # Validate synthesis-required fields / legacy bridging
        required_fields = ["signal", "position_action", "confidence"]
        synth_fields = (
            "regime",
            "thesis",
            "invalidation",
            "execution_note",
            "volume_note",
            "risk_assessment",
        )
        if not all(field in signal_data for field in required_fields):
            missing = [f for f in required_fields if f not in signal_data]
            self._log_warning(f"⚠️ Missing required fields in signal data: {missing}")
            return self._emit_fallback(price_data)

        position_action = str(signal_data.get("position_action") or "").upper()
        valid_actions = {
            "ENTER_LONG",
            "ENTER_SHORT",
            "HOLD_POSITION",
            "EXIT_NOW",
            "NO_ACTION",
        }
        if position_action not in valid_actions:
            self._log_warning(f"⚠️ Invalid position_action in signal data: {position_action}")
            return self._emit_fallback(price_data)
        signal_data["position_action"] = position_action

        if "thesis" not in signal_data and "reason" in signal_data:
            signal_data["thesis"] = signal_data["reason"]

        thesis_val = signal_data.get("thesis") or ""
        legacy_reason_val = signal_data.get("reason") or ""
        if not str(thesis_val).strip() and not str(legacy_reason_val).strip():
            self._log_warning(
                "⚠️ Missing thesis/reason synthesis field in signal data."
            )
            return self._emit_fallback(price_data)

        synth_missing = [f for f in synth_fields if f not in signal_data]
        if synth_missing:
            self._log_warning(
                "⚠️ Missing synthesis structured fields "
                f"({','.join(synth_missing)}) — patching defaults"
            )
            for fld in synth_missing:
                signal_data.setdefault(
                    fld,
                    "MEDIUM" if fld == "risk_assessment" else "",
                )

        self._finalize_signal_compat(signal_data, price_data)

        if "partial_close_pct" in signal_data:
            self._log_warning(
                "⚠️ Ignoring partial_close_pct; move-capture v1 does not allow per-bar partial churn"
            )
            signal_data.pop("partial_close_pct", None)

        target_r_raw = signal_data.get("target_r")
        if target_r_raw is not None:
            try:
                target_r = float(target_r_raw)
                signal_data["target_r"] = min(3.0, max(0.5, target_r))
            except (TypeError, ValueError):
                self._log_warning("⚠️ Invalid target_r from LLM, dropping field")
                signal_data.pop("target_r", None)

        # Add metadata
        signal_data["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        signal_data["reasoning_content"] = reasoning_content
        signal_data["llm_model"] = self.model

        return signal_data

    def _record_signal(self, signal_data: Dict[str, Any]) -> None:
        """Persist signal/fallback in history so previous_signal never goes stale."""
        self.signal_history.append(signal_data)
        if len(self.signal_history) > 30:
            self.signal_history.pop(0)
        self._log_signal_stats(signal_data)

    def _build_prompt_payload(
        self,
        price_data: Dict[str, Any],
        technical_data: Dict[str, Any],
        sentiment_data: Optional[Dict[str, Any]],
        current_position: Optional[Dict[str, Any]],
        risk_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Build a concise structured payload for audit logs/dashboard.

        This is intentionally compact so operators can inspect what the model saw
        without dumping the full static instruction block every cycle.
        """
        kline_data = price_data.get("kline_data", []) or []
        kline_summary = []
        for bar in kline_data[-5:]:
            kline_summary.append(
                {
                    "o": bar.get("open"),
                    "h": bar.get("high"),
                    "l": bar.get("low"),
                    "c": bar.get("close"),
                    "v": bar.get("volume"),
                }
            )

        micro = price_data.get("microstructure") or {}
        micro_summary = {}
        compact_windows = {}
        tfw = micro.get("tf_windows")
        if isinstance(tfw, dict):
            compact_windows = {
                k: tfw[k]
                for k in ("W_fast_sec", "W_main_sec", "W_context_sec")
                if k in tfw
            }
            for lbl in ("fast", "main", "context"):
                node = tfw.get(lbl)
                if isinstance(node, dict):
                    compact_windows[f"{lbl}_spread_mean"] = node.get("spread_mean_bps")
                    compact_windows[f"{lbl}_tfi"] = node.get("trade_flow_imbalance")
                    labs = node.get("labels") or {}
                    compact_windows[f"{lbl}_liquidity"] = labs.get("liquidity")
                    compact_windows[f"{lbl}_pressure"] = labs.get("directional_pressure")

        for key in (
            "spread_bps",
            "tob_imbalance",
            "depth_imbalance",
            "ema_ofi",
            "queue_pressure",
            "trade_flow_imbalance",
            "depth_regime",
        ):
            if key in micro:
                micro_summary[key] = micro.get(key)

        technical_summary = {}
        for key in (
            "overall_trend",
            "short_term_trend",
            "rsi",
            "macd_trend",
            "macd_histogram",
            "support",
            "resistance",
            "atr",
            "atr_pct",
            "dmi_dx",
            "adx",
            "dmi_pos",
            "dmi_neg",
        ):
            if key in technical_data:
                technical_summary[key] = technical_data.get(key)

        for vol_key in (
            "rvol",
            "volume_zscore",
            "volume_trend_slope",
            "directional_volume_confirmation",
            "volume_regime",
        ):
            if vol_key in technical_data:
                technical_summary[vol_key] = technical_data.get(vol_key)

        sentiment_summary = None
        if sentiment_data:
            sentiment_summary = {}
            for key in ("score", "label", "confidence", "timeframe", "source"):
                if key in sentiment_data:
                    sentiment_summary[key] = sentiment_data.get(key)

        position_health = None
        if current_position and isinstance(current_position, dict):
            position_health = current_position.get("position_health")

        return {
            "ts": price_data.get("timestamp"),
            "price": price_data.get("price"),
            "high": price_data.get("high"),
            "low": price_data.get("low"),
            "volume": price_data.get("volume"),
            "price_change_pct": price_data.get("price_change"),
            "position": current_position,
            "position_health": position_health,
            "risk_context": self._compact_risk_context(risk_context),
            "technical": technical_summary,
            "microstructure": micro_summary,
            "kline_tail_5": kline_summary,
            "sentiment": sentiment_summary,
            "market_state": price_data.get("market_state"),
            "llm_trigger_reason": price_data.get("llm_trigger_reason"),
            "previous_signal": self._compact_previous_signal(
                self.signal_history[-1] if self.signal_history else None
            ),
            "micro_tf_windows": compact_windows if compact_windows else None,
        }

    def _compact_risk_context(self, risk_context: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Return the model/audit-safe subset of exchange account context."""
        if not risk_context:
            return None

        wallet = risk_context.get("wallet") or {}
        position = risk_context.get("position")
        open_orders = risk_context.get("open_orders") or []
        trade_summary = risk_context.get("recent_trade_summary") or {}

        return {
            "source": risk_context.get("source"),
            "ok": risk_context.get("ok"),
            "fetched_at": risk_context.get("fetched_at"),
            "wallet": {
                "total_equity": wallet.get("total_equity"),
                "total_available_balance": wallet.get("total_available_balance"),
                "total_initial_margin": wallet.get("total_initial_margin"),
                "total_maintenance_margin": wallet.get("total_maintenance_margin"),
                "usdt_equity": wallet.get("usdt_equity"),
                "usdt_wallet_balance": wallet.get("usdt_wallet_balance"),
            } if wallet else None,
            "exchange_position": position,
            "open_orders_count": len(open_orders),
            "open_orders": open_orders[:5],
            "recent_trade_summary": trade_summary,
        }

    def _estimate_round_trip_friction_bps(self, price_data: Dict[str, Any]) -> float:
        """
        Estimate round-trip trading friction in basis points.

        friction = round-trip taker fees + live spread (crossed on entry+exit)
        + a small slippage buffer. Used to give the LLM a concrete breakeven so
        it stops locking gross gains that are net-negative after costs.
        """
        micro = price_data.get("microstructure") or {}
        try:
            spread_bps = float(micro.get("spread_bps") or 0.0)
        except (TypeError, ValueError):
            spread_bps = 0.0
        spread_bps = max(0.0, spread_bps)
        return self.ROUND_TRIP_FEE_BPS + spread_bps + self.SLIPPAGE_BUFFER_BPS

    def _build_analysis_prompt(
        self,
        price_data: Dict[str, Any],
        technical_data: Dict[str, Any],
        sentiment_data: Optional[Dict[str, Any]],
        current_position: Optional[Dict[str, Any]],
        risk_context: Optional[Dict[str, Any]],
    ) -> str:
        """Build comprehensive analysis prompt for DeepSeek."""

        # K-line data
        kline_text = self._format_kline_data(price_data.get("kline_data", []))

        # Technical analysis
        technical_text = self._format_technical_data(technical_data)

        # Sentiment data
        sentiment_text = self._format_sentiment_data(sentiment_data)

        # Position info
        position_text = self._format_position_data(current_position)
        risk_context_text = self._format_risk_context_data(risk_context)
        microstructure_text = self._format_microstructure_data(
            price_data.get("microstructure")
        )
        market_state_text = self._format_market_state(
            price_data.get("market_state"),
            price_data.get("llm_trigger_reason"),
        )

        # Previous signal
        signal_text = ""
        if self.signal_history:
            last_signal = self.signal_history[-1]
            signal_text = self._format_prior_decision_context(
                last_signal,
                price_data.get("bars_since_last_llm_decision"),
            )

        # Position health context for scalp-aware decision making
        position_health_text = self._format_position_health(current_position)

        # Concrete round-trip cost so the model can size net edge vs gross move.
        friction_bps = self._estimate_round_trip_friction_bps(price_data)
        friction_text = (
            f"FRICTION round_trip~={friction_bps:.1f}bps (~{friction_bps / 100:.3f}% "
            "gross move just to break even; need gross profit clearly above this to net positive)"
        )
        risk_unit_text = self._format_risk_unit_context(price_data)

        prompt = (
            f"{self.pair_label} | {self.timeframe_label} | MARKET-CHANGE RE-ANALYSIS — output JSON only.\n\n"
            f"{market_state_text}\n"
            f"{kline_text}\n"
            f"{technical_text}\n"
            f"{microstructure_text}\n"
            f"{sentiment_text}\n"
            f"{risk_context_text}\n"
            f"{position_health_text}\n"
            f"{friction_text}\n"
            f"{risk_unit_text}\n"
            f"{signal_text}\n"
            "CURRENT\n"
            f"price={price_data['price']:.4f} time={price_data['timestamp']} "
            f"h={price_data.get('high', 0):.4f} l={price_data.get('low', 0):.4f} "
            f"vol={price_data.get('volume', 0):.4f} chg_pct={price_data.get('price_change', 0):+.4f}% "
            f"position={position_text}\n"
            "HEADLINE TECH\n"
            f"trend={technical_data.get('overall_trend')} st={technical_data.get('short_term_trend')} "
            f"rsi={technical_data.get('rsi', 0):.1f} macd_side={technical_data.get('macd_trend')}\n\n"
            "DECISION FRAMEWORK (move capture):\n"
            "- Step 1: classify regime as RANGE, TREND, or TRANSITION from structure, ATR/BB, volume, and OB windows.\n"
            "- Step 2: decide whether the trigger changed the prior thesis or simply confirms no action.\n"
            "- Step 3: compare expected path to FRICTION and invalidation distance; avoid sub-friction churn.\n"
            "- Step 4: choose target_r from 0.5, 0.75, 1, 2, or 3 based on market quality and structural room.\n"
            "- RANGE: enter only near range extremes, not mid-range. Put SL beyond invalidation outside the range edge. "
            "TP toward opposing range edge or conservative structural target. Mid-range -> prefer NO_ACTION.\n"
            "- TREND: prefer continuation/pullback participation. Hold while invalidation remains intact. "
            "Do not exit from one pause or partial retrace.\n"
            "- TRANSITION: default NO_ACTION unless both structure and flow confirm breakout.\n"
            "- Do not flip from trend_up/trend_down to range from one bar drift unless structure actually breaks.\n"
            "- If already in position, do not exit because of small giveback alone.\n"
            "- Exit only when invalidation is hit, structure clearly fails, reversal is confirmed, "
            "or opposing microstructure is materially strong while price moves toward invalidation.\n"
            "- If expected move from current price is not clearly larger than round-trip friction, do not enter.\n"
            "- If in a trade and PnL is still within friction noise, do not force exit unless invalidation is threatened.\n"
            "- If flat: enter only when structure + volume + liquidity imply a clean directional move worth the risk.\n"
            "- For ENTER_LONG or ENTER_SHORT, provide explicit numeric stop_loss and take_profit prices.\n"
            "- EXIT_NOW means close the current position only. Never imply or request an automatic reversal.\n"
            "- Entry actions are valid only while flat. While exposed choose HOLD_POSITION or EXIT_NOW.\n"
            "- If exchange/local state looks conflicting or stale, prefer HOLD and wait for confirmation.\n"
            "- If exchange_position in RISK is flat, treat exposure as flat (do not act as if position exists).\n"
            "- If evidence is mixed or low quality, HOLD (NO_ACTION).\n\n"
            "Output: single JSON object in English, no markdown.\n"
            'Schema (all string values except numeric target_r/stop_loss/take_profit):\n'
            "{\n"
            '  "signal": "BUY|SELL|HOLD",\n'
            '  "position_action": "ENTER_LONG|ENTER_SHORT|HOLD_POSITION|EXIT_NOW|NO_ACTION",\n'
            '  "confidence": "HIGH|MEDIUM|LOW",\n'
            '  "regime": "short label",\n'
            '  "thesis": "compact reasoning IN ENGLISH",\n'
            '  "invalidation": "what would prove this wrong",\n'
            '  "execution_note": "scaling/spread/friction-aware note",\n'
            '  "volume_note": "volume context",\n'
            '  "risk_assessment": "LOW|MEDIUM|HIGH",\n'
            '  "trend_strength": "STRONG|MODERATE|WEAK",\n'
            '  "target_r": 1,\n'
            '  "stop_loss": 0,\n'
            '  "take_profit": 0\n'
            "}\n"
            "BUY pairs with ENTER_LONG. SELL pairs with ENTER_SHORT. HOLD pairs with NO_ACTION, HOLD_POSITION, or EXIT_NOW.\n"
            "For entries, stop_loss and take_profit are required numeric prices. For non-entry actions they may be omitted.\n"
            "HOLD is allowed when evidence conflicts; avoid inventing precise SL/TP.\n"
        )
        return prompt

    @staticmethod
    def _format_prior_decision_context(
        previous: Dict[str, Any],
        bars_since: Any,
    ) -> str:
        thesis = str(previous.get("thesis") or previous.get("reason") or "")
        invalidation = str(previous.get("invalidation") or "")
        execution_note = str(previous.get("execution_note") or "")
        if len(thesis) > 320:
            thesis = thesis[:320] + "..."
        if len(invalidation) > 220:
            invalidation = invalidation[:220] + "..."
        if len(execution_note) > 180:
            execution_note = execution_note[:180] + "..."
        return (
            "PRIOR_DECISION\n"
            f"bars_since={bars_since if bars_since is not None else 'unknown'} "
            f"signal={previous.get('signal')} action={previous.get('position_action')} "
            f"conf={previous.get('confidence')} regime={previous.get('regime')} "
            f"target_r={previous.get('target_r')}\n"
            f"prior_thesis={thesis}\n"
            f"prior_invalidation={invalidation}\n"
            f"prior_execution_note={execution_note}\n"
            "Use this to decide whether the trigger invalidates, confirms, or leaves the prior thesis unchanged."
        )

    @staticmethod
    def _format_risk_unit_context(price_data: Dict[str, Any]) -> str:
        margin = price_data.get("fixed_trade_margin_usdt")
        notional = price_data.get("fixed_trade_notional_usdt")
        leverage = price_data.get("configured_leverage")
        return (
            "RISK_UNIT "
            f"fixed_margin_usdt={margin if margin is not None else 'unknown'} "
            f"fixed_position_notional_usdt={notional if notional is not None else 'unknown'} "
            f"configured_leverage={leverage if leverage is not None else 'unknown'} "
            "1R=actual bracket loss distance from entry to SL times quantity; "
            "valid reward examples: 0.5R, 0.75R, 1R, 2R, 3R."
        )

    @staticmethod
    def _format_market_state(
        market_state: Optional[Dict[str, Any]],
        trigger_reason: Optional[str],
    ) -> str:
        if not isinstance(market_state, dict):
            return f"MARKET_STATE trigger={trigger_reason or 'unknown'} unavailable"
        keys = (
            "app_regime",
            "position_key",
            "open_orders_count",
            "main_pressure",
            "main_regime_shift",
            "main_trade_flow_imbalance",
            "main_normalized_ofi_score",
            "support_12",
            "resistance_12",
            "support_48",
            "resistance_48",
            "position_invalidation_price",
        )
        pairs = []
        for key in keys:
            value = market_state.get(key)
            if value is None or value == "":
                continue
            pairs.append(f"{key}={value}")
        body = " ".join(pairs) if pairs else "none"
        return f"MARKET_STATE trigger={trigger_reason or 'unknown'} {body}"

    def _format_kline_data(self, kline_data: list) -> str:
        """Format K-line data for prompt."""
        if not kline_data:
            return "KLINES none"

        window = kline_data[-min(len(kline_data), 20):]
        lines = [f"KLINES ({len(window)} bars, OHLCV):"]
        for i, k in enumerate(window, 1):
            lines.append(
                f"{i}: o={k['open']:.4f} h={k['high']:.4f} l={k['low']:.4f} "
                f"c={k['close']:.4f} v={k['volume']:.4f}"
            )
        return "\n".join(lines)

    def _has_microstructure_features(self, micro_data: Optional[Dict[str, Any]]) -> bool:
        """Return True when at least one target microstructure field is present."""
        if not isinstance(micro_data, dict):
            return False
        if isinstance(micro_data.get("tf_windows"), dict) and micro_data["tf_windows"].get("ready"):
            return True
        fields = (
            "spread_bps",
            "spread_volatility",
            "tob_imbalance",
            "depth_imbalance",
            "ema_ofi",
            "queue_pressure",
            "trade_flow_imbalance",
            "vwap_deviation_bps",
            "sweep_buy_count",
            "sweep_sell_count",
            "depth_regime",
        )
        return any(micro_data.get(field) is not None for field in fields)

    def _format_microstructure_data(self, micro_data: Optional[Dict[str, Any]]) -> str:
        """Format order book microstructure section for analysis prompt."""
        if not self._has_microstructure_features(micro_data):
            return "MICRO none"

        micro = micro_data or {}
        def _f(key: str, default: float = 0.0) -> float:
            val = micro.get(key, default)
            try:
                return float(val)
            except (TypeError, ValueError):
                return default

        def _i(key: str, default: int = 0) -> int:
            val = micro.get(key, default)
            try:
                return int(val)
            except (TypeError, ValueError):
                return default

        lines = [
            "MICRO",
            (
                f"spread_bps={_f('spread_bps'):.2f} spread_vol={_f('spread_volatility'):.4f} "
                f"tob_imb={_f('tob_imbalance'):+.4f} depth_imb={_f('depth_imbalance'):+.4f}"
            ),
            (
                f"ema_ofi={_f('ema_ofi'):+.5f} queue_p={_f('queue_pressure'):+.4f} "
                f"tfi={_f('trade_flow_imbalance'):+.4f} vwap_dev_bps={_f('vwap_deviation_bps'):+.2f}"
            ),
            f"sweep buy/sell={_i('sweep_buy_count')}/{_i('sweep_sell_count')} depth_regime={micro.get('depth_regime', '?')}",
        ]
        tw = micro.get("tf_windows")
        if isinstance(tw, dict) and tw.get("ready"):
            lines.append("")
            lines.append("MICRO_TF")
            for key, pretty in (
                ("fast", "FAST_60s"),
                ("main", "MAIN_TF"),
                ("context", "CONTEXT_3X_TF"),
            ):
                lines.append(self._summarize_tf_ob_window(pretty, tw.get(key)))

        return "\n".join(lines)

    @staticmethod
    def _summarize_tf_ob_window(title: str, node: Any) -> str:
        """Render one window bucket for prompting."""

        def _nf(val: Any) -> str:
            try:
                if val is None:
                    return "na"
                return f"{float(val):.3f}"
            except (TypeError, ValueError):
                return "na"

        if not isinstance(node, dict) or not node.get("ready"):
            return (
                f"├─ {title}: sparse snapshots "
                f"(n={node.get('n_snapshots') if isinstance(node, dict) else '0'})"
            )
        labels = node.get("labels") or {}
        regimes = node.get("depth_regime_proportions") or {}
        reg_txt = ",".join(f"{k}:{v}" for k, v in list(regimes.items())[:4])
        return (
            f"├─ {title}: "
            f"spread_mu/p95/sig {_nf(node.get('spread_mean_bps'))}/"
            f"{_nf(node.get('spread_p95_bps'))}/{_nf(node.get('spread_vol'))} | "
            f"norm_OFI {_nf(node.get('normalized_ofi_score'))} | "
            f"tfi {_nf(node.get('trade_flow_imbalance'))} | "
            f"sweep_imb {_nf(node.get('sweep_imbalance'))} | "
            f"µpx_mid_bps {_nf(node.get('microprice_vs_mid_bps_avg'))} || "
            f"L:{labels.get('liquidity')} F:{labels.get('friction')} "
            f"P:{labels.get('directional_pressure')} A:{labels.get('absorption')} "
            f"RS:{labels.get('regime_shift')}"
            f"{(' | regimes ' + reg_txt) if reg_txt else ''}"
        )

    def _format_technical_data(self, technical_data: Dict[str, Any]) -> str:
        """Format technical indicator data for prompt."""

        def safe_float(val, default=0):
            return float(val) if val is not None else default

        rsi = safe_float(technical_data.get('rsi'))
        atr = safe_float(technical_data.get('atr'))
        atr_pct = safe_float(technical_data.get('atr_pct'))
        ddx = safe_float(technical_data.get('dmi_dx'))
        adx = safe_float(technical_data.get('adx'))

        text = (
            "TECH "
            f"st/mt/overall={technical_data.get('short_term_trend')}/{technical_data.get('medium_term_trend')}/"
            f"{technical_data.get('overall_trend')} macd_trend={technical_data.get('macd_trend')} "
            f"rsi={rsi:.1f} macd_hist={safe_float(technical_data.get('macd_histogram')):.5f}\n"
            f"bb u/m/l={safe_float(technical_data.get('bb_upper')):.4f}/{safe_float(technical_data.get('bb_middle')):.4f}/"
            f"{safe_float(technical_data.get('bb_lower')):.4f} bb_pos={safe_float(technical_data.get('bb_position')):.3f}\n"
            f"atr={atr:.6f} atr_pct={atr_pct:.4f} adx={adx:.2f} dmi_dx={ddx:.2f} "
            f"dmi+/-={safe_float(technical_data.get('dmi_pos')):.4f}/{safe_float(technical_data.get('dmi_neg')):.4f}\n"
            f"SMA: {self._format_sma_data(technical_data).replace(chr(10), ' ')} "
            f"levels R/S={safe_float(technical_data.get('resistance')):.4f}/{safe_float(technical_data.get('support')):.4f}\n"
            f"ranges 12b={safe_float(technical_data.get('range_12_pct')):.3f}% "
            f"48b={safe_float(technical_data.get('range_48_pct')):.3f}% "
            f"288b={safe_float(technical_data.get('range_288_pct')):.3f}% "
            f"R48/S48={safe_float(technical_data.get('resistance_48')):.4f}/{safe_float(technical_data.get('support_48')):.4f}\n"
            f"VOL rvol={safe_float(technical_data.get('rvol', technical_data.get('volume_ratio'))):.3f} "
            f"z={safe_float(technical_data.get('volume_zscore')):.2f} slope={safe_float(technical_data.get('volume_trend_slope')):.6f} "
            f"dir_conf={technical_data.get('directional_volume_confirmation')} regime={technical_data.get('volume_regime')}"
        )
        return text
    
    def _format_sma_data(self, technical_data: Dict[str, Any]) -> str:
        """Format SMA data dynamically based on available periods."""
        sma_keys = sorted(
            [key for key in technical_data.keys() if key.startswith('sma_')],
            key=lambda x: int(x.split('_')[1]),
        )
        if not sma_keys:
            return "none"
        return " ".join(
            f"SMA{k.split('_')[1]}={float(technical_data[k]):.4f}"
            for k in sma_keys
        )

    def _format_sentiment_data(self, sentiment_data: Optional[Dict[str, Any]]) -> str:
        """Format sentiment data for prompt."""
        if not sentiment_data:
            return "SENT n/a"

        sign = '+' if sentiment_data['net_sentiment'] >= 0 else ''
        return (
            "SENT "
            f"+/- {sentiment_data['positive_ratio']:.1%}/{sentiment_data['negative_ratio']:.1%} "
            f"net {sign}{sentiment_data['net_sentiment']:.3f}"
        )

    def _format_position_data(self, position: Optional[Dict[str, Any]]) -> str:
        """Format position data for prompt."""
        if not position:
            return "No position"

        return (
            f"{position['side']} position, "
            f"Size: {position.get('quantity', 0):.3f} {self.base_asset}, "
            f"Avg Price: ${position.get('avg_px', 0):.2f}, "
            f"P&L: {position.get('unrealized_pnl', 0):.2f} {self.quote_asset}"
        )

    def _format_position_health(self, position: Optional[Dict[str, Any]]) -> str:
        """Format observational position trajectory context for prompting."""
        if not position:
            return "POS_HEALTH flat"

        health = position.get("position_health") or {}
        if not health:
            return "POS_HEALTH n/a"

        profit_pct = health.get("profit_pct", 0)
        peak_profit_pct = health.get("peak_profit_pct", 0)
        giveback_pct = health.get("giveback_pct", 0)
        bars_held = health.get("bars_held", 0)
        recommendation = health.get("recommendation", "")

        lines = [
            "POS_HEALTH",
            f"current_profit_pct={profit_pct:+.3f}% peak_profit_pct={peak_profit_pct:+.3f}%",
            f"giveback_from_peak={giveback_pct:.1f}% bars_held={bars_held}",
        ]
        if recommendation:
            lines.append(f"health_state={recommendation}")
        if giveback_pct > 40:
            lines.append("note=giveback observed")
        elif peak_profit_pct > 0.2 and profit_pct < 0.05:
            lines.append("note=profit retrace within open thesis")
        lines.append("note=monitor invalidation proximity")

        return "\n".join(lines)

    def _format_risk_context_data(self, risk_context: Optional[Dict[str, Any]]) -> str:
        """Format account, open-order, and recent realized P&L context."""
        if not risk_context:
            return "RISK n/a"

        compact = self._compact_risk_context(risk_context) or {}
        wallet = compact.get("wallet") or {}
        exchange_position = compact.get("exchange_position") or {}
        trade_summary = compact.get("recent_trade_summary") or {}
        open_orders = compact.get("open_orders") or []
        errors = risk_context.get("errors") or []

        lines = [
            "RISK",
            (
                f"wallet eq={wallet.get('total_equity')} avail={wallet.get('total_available_balance')} "
                f"init_m={wallet.get('total_initial_margin')} maint_m={wallet.get('total_maintenance_margin')}"
            ),
        ]

        if exchange_position:
            lines.append(
                f"ex_pos {exchange_position.get('side')} {exchange_position.get('quantity')} "
                f"@ {exchange_position.get('avg_price')} notional={exchange_position.get('position_value')} "
                f"upnl={exchange_position.get('unrealized_pnl')} liq={exchange_position.get('liq_price')}"
            )
        else:
            lines.append("ex_pos flat")

        oo_n = compact.get('open_orders_count', 0)
        lines.append(f"open_orders={oo_n}")
        for order in open_orders[:5]:
            lines.append(
                f"  ord {order.get('side')} {order.get('quantity')} {order.get('order_type')} "
                f"st={order.get('status')} ro={order.get('reduce_only')} px={order.get('price')}"
            )

        lines.append(
            "closed_5 "
            f"n={trade_summary.get('last_5_count')} W/L={trade_summary.get('last_5_wins')}/"
            f"{trade_summary.get('last_5_losses')} pnl={trade_summary.get('last_5_realized_pnl')}"
        )
        for trade in (trade_summary.get("last_5_outcomes") or [])[:5]:
            lines.append(
                f"  tr {trade.get('outcome')} {trade.get('side')} {trade.get('quantity')} "
                f"pnl={trade.get('closed_pnl')}"
            )

        if errors:
            lines.append(f"warn fetch_errors={len(errors)}")
        return "\n".join(lines)

    def _safe_parse_json(self, json_str: str) -> Optional[Dict[str, Any]]:
        """Safely parse JSON response, handling format issues."""
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            # Try to extract JSON from response
            start_idx = json_str.find('{')
            end_idx = json_str.rfind('}') + 1

            if start_idx != -1 and end_idx != 0:
                json_str_original = json_str[start_idx:end_idx]

                try:
                    # Parse line by line and fix quotes in string values
                    lines = json_str_original.split('\n')
                    fixed_lines = []
                    
                    for line in lines:
                        # Check if this is a line with a key-value pair containing quotes
                        if '": "' in line and line.strip().endswith((',', '",')):
                            # Find the value part (between the first ": " and the last ")
                            key_end = line.find('": "') + 4
                            if line.strip().endswith(','):
                                value_end = line.rfind('",')
                            else:
                                value_end = line.rfind('"')
                            
                            if key_end > 4 and value_end > key_end:
                                prefix = line[:key_end]
                                value = line[key_end:value_end]
                                suffix = line[value_end:]
                                
                                # Replace internal quotes with single quotes
                                fixed_value = value.replace('"', "'")
                                fixed_line = prefix + fixed_value + suffix
                                fixed_lines.append(fixed_line)
                            else:
                                fixed_lines.append(line)
                        else:
                            fixed_lines.append(line)
                    
                    json_str = '\n'.join(fixed_lines)
                    
                    # Try parsing
                    return json.loads(json_str)
                except json.JSONDecodeError as e:
                    self._log_error(f"❌ JSON parse failed: {e}")
                    self._log_debug(f"Original content: {json_str_original[:500]}...")
                except Exception as e:
                    self._log_error(f"❌ JSON fix error: {e}")

            return None

    def _emit_fallback(self, price_data: Dict[str, Any]) -> Dict[str, Any]:
        """Build fallback signal and run legacy SL/TP bridging for downstream consumers."""
        fb = self._create_fallback_signal(price_data)
        self._finalize_signal_compat(fb, price_data)
        fb["is_fallback"] = True
        return fb

    def _create_fallback_signal(self, price_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create conservative fallback signal when AI analysis fails."""
        px_raw = price_data.get("price")
        try:
            px = float(px_raw) if px_raw is not None else 0.0
        except (TypeError, ValueError):
            px = 0.0
        thesis = "Conservative fallback: model output unavailable or failed validation."
        return {
            "signal": "HOLD",
            "position_action": "NO_ACTION",
            "reason": thesis,
            "thesis": thesis,
            "regime": "unknown",
            "invalidation": "n/a",
            "execution_note": "No new risk until model output is restored.",
            "volume_note": "n/a",
            "risk_assessment": "HIGH",
            "stop_loss": round(px, 10) if px else 0.0,
            "take_profit": round(px, 10) if px else 0.0,
            "trend_strength": "WEAK",
            "confidence": "LOW",
            "reasoning_content": "fallback_default_no_model_output",
            "llm_model": self.model,
            "is_fallback": True,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    def _log_signal_stats(self, signal_data: Dict[str, Any]):
        """Log signal statistics."""
        signal = signal_data['signal']
        signal_count = sum(1 for s in self.signal_history if s.get('signal') == signal)
        total = len(self.signal_history)

        self._log_debug(f"📊 Signal Stats: {signal} (appeared {signal_count}/{total} times in recent history)")

        # Check for consecutive same signals (only warn on 3rd and every 5th after)
        if len(self.signal_history) >= 3:
            consecutive = 0
            for s in reversed(self.signal_history):
                if s.get('signal') == signal:
                    consecutive += 1
                else:
                    break
            if consecutive == 3 or (consecutive > 3 and consecutive % 5 == 0):
                self._log_warning(f"⚠️ {consecutive} consecutive {signal} signals")
