"""Autopilot: lets the terminal buy and sell by itself (paper trading only).

How a decision is made, for each stock on the watchlist:
  1. Fourier view  - FFT of the last 64 days splits price into trend, cycles
                     and noise. Gives: dominant cycle length, where we are in
                     that cycle, how noisy the stock is, and a denoised curve.
  2. Probability   - a logistic regression, trained on ~2 years of history
                     for the watchlist, turns Fourier + momentum features into
                     P(price is higher in 5 trading days).
  3. Sizing        - a GARCH(1,1) volatility forecast shrinks positions in
                     jumpy stocks.
  4. Guardrails    - stop-loss, take-profit, max per stock, max per sector,
                     cash buffer, daily trade limit, watchlist only.

None of this predicts prices with certainty. The model's accuracy on data it
never trained on is shown on screen next to the "always guess up" baseline.
"""
import math
import threading
import time
from collections import deque
from datetime import datetime

import numpy as np

from scoring import SECTORS
from signals import rsi as rsi_fn

WINDOW = 64        # days of prices fed to the FFT
MIN_HISTORY = 80   # days needed before features exist
HORIZON = 5        # model predicts the move over this many trading days

FEATURE_NAMES = [
    "5-day return", "20-day return", "RSI", "price vs 20-day average",
    "20 vs 50-day average", "cycle position", "cycle direction",
    "cycle strength", "noise level", "denoised slope", "volatility",
]

DEFAULTS = {
    "watchlist": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "JPM", "V", "JNJ",
                  "UNH", "XOM", "PG", "KO", "HD", "CAT", "NEE"],
    "interval_minutes": 5,
    "buy_threshold": 0.53,
    "sell_threshold": 0.47,
    "max_position_pct": 0.10,
    "max_sector_pct": 0.30,
    "cash_buffer_pct": 0.10,
    "stop_loss_pct": 0.05,
    "take_profit_pct": 0.10,
    "max_trades_per_day": 10,
    "max_noise_ratio": 0.60,
    "target_volatility": 0.25,
    "stop_on_lock": True,
}

_MODEL_CACHE = {}  # market data is shared, so one trained model serves everyone


# ---- 1. Fourier view ------------------------------------------------------
def fourier_view(closes):
    y = np.log(np.asarray(closes[-WINDOW:], dtype=float))
    n = len(y)
    x = np.arange(n)
    slope, intercept = np.polyfit(x, y, 1)
    trend = slope * x + intercept
    spec = np.fft.rfft(y - trend)          # detrended price -> frequencies
    power = np.abs(spec) ** 2
    ks = np.arange(1, len(spec))
    periods = n / ks
    total = power[1:].sum() or 1e-12

    band = ks[(periods >= 8) & (periods <= 32)]            # 8-32 day cycles
    k_dom = int(band[np.argmax(power[band])])
    phase = np.angle(spec[k_dom]) + 2 * np.pi * k_dom * (n - 1) / n
    noise = power[ks[periods < 8]].sum() / total            # faster than 8 days

    low_pass = spec.copy()
    low_pass[ks[periods < 8]] = 0                           # drop the noise
    smooth = np.fft.irfft(low_pass, n) + trend

    return {
        "period": n / k_dom,
        "position": math.cos(phase),     # +1 = cycle peak, -1 = cycle low
        "rising": -math.sin(phase),      # > 0 = cycle heading up
        "strength": power[k_dom] / total,
        "noise": noise,
        "slope": (smooth[-1] - smooth[-4]) / 3,
        "smooth": np.exp(smooth),
        "raw": np.exp(y),
    }


def describe_cycle(fv):
    if fv["position"] > 0.7:
        where = "near its peak"
    elif fv["position"] < -0.7:
        where = "near its low"
    else:
        where = "heading up" if fv["rising"] > 0 else "heading down"
    return f"{fv['period']:.0f}-day cycle {where}"


# ---- 3. GARCH(1,1) volatility --------------------------------------------
def garch_volatility(closes, alpha=0.08, beta=0.90):
    c = np.asarray(closes[-251:], dtype=float)
    r = np.diff(np.log(c))
    long_run = r.var() or 1e-8
    omega = long_run * (1 - alpha - beta)
    s2 = long_run
    for e in r:
        s2 = omega + alpha * e * e + beta * s2   # sigma^2_t = w + a*e^2 + b*sigma^2
    return math.sqrt(s2 * 252)                   # annualized


# ---- features -------------------------------------------------------------
def features(closes):
    c = np.asarray(closes, dtype=float)
    last = c[-1]
    sma20, sma50 = c[-20:].mean(), c[-50:].mean()
    r = rsi_fn(list(c[-60:]))
    fv = fourier_view(c)
    vol = garch_volatility(c)
    vec = np.array([
        last / c[-6] - 1, last / c[-21] - 1, ((r if r is not None else 50) - 50) / 50,
        last / sma20 - 1, sma20 / sma50 - 1,
        fv["position"], fv["rising"], fv["strength"], fv["noise"], fv["slope"], vol,
    ])
    return vec, fv, vol


# ---- 2. logistic regression ---------------------------------------------
class LogisticModel:
    """P(up) = 1 / (1 + e^-(w.x + b)), fit by gradient descent with L2."""

    def fit(self, X, y, l2=0.01, lr=0.1, iters=800):
        self.mu, self.sd = X.mean(0), X.std(0) + 1e-9
        Z = (X - self.mu) / self.sd
        self.w, self.b = np.zeros(Z.shape[1]), 0.0
        for _ in range(iters):
            err = self._sigmoid(Z @ self.w + self.b) - y
            self.w -= lr * (Z.T @ err / len(y) + l2 * self.w)
            self.b -= lr * err.mean()
        return self

    @staticmethod
    def _sigmoid(z):
        return 1 / (1 + np.exp(-np.clip(z, -30, 30)))

    def predict(self, X):
        return self._sigmoid(((np.atleast_2d(X) - self.mu) / self.sd) @ self.w + self.b)


def train_model(client, watchlist):
    X, y, dates = [], [], []
    for sym in watchlist:
        bars = client.daily_bars(sym, days=760)
        closes = [float(b["c"]) for b in bars]
        days = [b["t"][:10] for b in bars]
        for t in range(MIN_HISTORY - 1, len(closes) - HORIZON):
            X.append(features(closes[:t + 1])[0])
            y.append(1.0 if closes[t + HORIZON] > closes[t] else 0.0)
            dates.append(days[t])
    if len(X) < 200:
        raise RuntimeError("Not enough price history to train the model.")
    X, y = np.array(X), np.array(y)

    # Honest test: train on the older 80% of dates, test on the newest 20%.
    uniq = sorted(set(dates))
    idx = {d: i for i, d in enumerate(uniq)}
    di = np.array([idx[d] for d in dates])
    split = int(len(uniq) * 0.8)
    train, test = di < split - HORIZON, di >= split
    test_model = LogisticModel().fit(X[train], y[train])
    accuracy = float(((test_model.predict(X[test]) > 0.5) == y[test]).mean())
    base_rate = float(y[test].mean())

    model = LogisticModel().fit(X, y)          # final model uses all history
    order = np.argsort(-np.abs(model.w))[:3]
    learned = [f"{FEATURE_NAMES[i]} ({'+' if model.w[i] > 0 else '−'})" for i in order]
    info = {
        "accuracy": accuracy,
        "baseline": max(base_rate, 1 - base_rate),
        "samples": int(len(y)),
        "test_samples": int(test.sum()),
        "top_features": learned,
        "trained_at": datetime.now().strftime("%b %d, %I:%M %p"),
    }
    return model, info


# ---- the autopilot loop --------------------------------------------------
class Autopilot:
    def __init__(self, client, settings, owner_uid):
        self.client = client
        self.cfg = {**DEFAULTS, **(settings or {})}
        self.owner = owner_uid
        self.state, self.dry_run, self.error = "off", True, None
        self.model, self.model_info = None, None
        self.radar, self.log = [], deque(maxlen=60)
        self.last_run = self.next_run = None
        self.trades_today, self.trade_day = 0, None
        self._last_decision = {}
        self._stop = threading.Event()
        self._thread = None

    # -- control --
    def start(self, dry_run=True):
        changed = self.dry_run != dry_run
        self.dry_run = dry_run
        if self._thread and self._thread.is_alive():
            if changed:
                self._note("info", None, f"Switched to {'dry run' if dry_run else 'live trading'}.")
                self._last_decision.clear()
            return
        self._stop.clear()
        self.state, self.error = "training", None
        self._note("info", None, f"Autopilot on ({'dry run' if dry_run else 'live trading'}).")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        if self.state != "off":
            self._note("info", None, "Autopilot off.")
        self._stop.set()
        self.state = "off"
        self.next_run = None

    def status(self):
        return {
            "available": True, "state": self.state, "dry_run": self.dry_run,
            "error": self.error, "model": self.model_info, "radar": self.radar,
            "log": list(self.log), "trades_today": self.trades_today,
            "max_trades": self.cfg["max_trades_per_day"],
            "last_run": self.last_run, "next_run": self.next_run,
        }

    # -- internals --
    def _note(self, action, symbol, text, qty=None, status=None):
        self.log.appendleft({
            "time": datetime.now().strftime("%a %I:%M %p"), "action": action,
            "symbol": symbol, "qty": qty, "text": text, "status": status,
        })

    def _run(self):
        try:
            key = (tuple(self.cfg["watchlist"]), datetime.now().date().isoformat())
            if key not in _MODEL_CACHE:
                self._note("info", None, "Training the model on 2 years of watchlist prices…")
                _MODEL_CACHE[key] = train_model(self.client, self.cfg["watchlist"])
            self.model, self.model_info = _MODEL_CACHE[key]
            if self._stop.is_set():
                return
            self.state = "running"
        except Exception as e:  # training failure is fatal
            self.state, self.error = "error", f"Training failed: {e}"
            return

        while not self._stop.is_set():
            try:
                self._cycle()
            except Exception as e:  # network blip etc.: log and try next time
                self._note("info", None, f"Check skipped: {e}")
            wait = self.cfg["interval_minutes"] * 60
            self.next_run = datetime.fromtimestamp(time.time() + wait).strftime("%I:%M %p")
            self._stop.wait(wait)

    def _evaluate(self, sym):
        bars = self.client.daily_bars(sym, days=400)
        closes = [float(b["c"]) for b in bars]
        if len(closes) < MIN_HISTORY:
            return None
        vec, fv, vol = features(closes)
        p = float(self.model.predict(vec)[0])
        price = self.client.latest_price(sym) or closes[-1]
        lo = min(fv["raw"].min(), fv["smooth"].min())
        span = (max(fv["raw"].max(), fv["smooth"].max()) - lo) or 1
        return {
            "symbol": sym, "price": float(price), "p_up": p, "vol": vol,
            "noise": fv["noise"], "period": fv["period"], "cycle": describe_cycle(fv),
            "spark_raw": [round(float((v - lo) / span), 3) for v in fv["raw"]],
            "spark_smooth": [round(float((v - lo) / span), 3) for v in fv["smooth"]],
            "decision": "Hold",
        }

    def _cycle(self):
        cfg, client = self.cfg, self.client
        clock = client.clock()
        today = (clock.get("timestamp") or "")[:10]
        if today != self.trade_day:
            self.trade_day, self.trades_today = today, 0
        market_open = bool(clock.get("is_open"))
        live = not self.dry_run and market_open

        account = client.account()
        equity, cash = float(account["equity"]), float(account["cash"])
        positions = {p["symbol"]: p for p in client.positions()}
        pending = {o["symbol"] for o in client.open_orders()}

        evals = {}
        for sym in cfg["watchlist"]:
            try:
                ev = self._evaluate(sym)
            except Exception:
                ev = None
            if ev:
                evals[sym] = ev

        # Sells first: stop-loss, take-profit, or the model turning negative.
        for sym, pos in positions.items():
            ev = evals.get(sym)
            qty = int(float(pos["qty"]))
            if sym not in cfg["watchlist"] or sym in pending or qty <= 0:
                continue
            plpc = float(pos["unrealized_plpc"])
            if plpc <= -cfg["stop_loss_pct"]:
                reason = f"Down {abs(plpc):.1%} from purchase, so the stop-loss sold it."
            elif plpc >= cfg["take_profit_pct"]:
                reason = f"Up {plpc:.1%} from purchase, so it took the profit."
            elif ev and ev["p_up"] < cfg["sell_threshold"]:
                reason = f"Only a {ev['p_up']:.0%} chance of rising; {ev['cycle']}."
            else:
                continue
            if ev:
                ev["decision"] = "Sell"
            self._act("sell", sym, qty, reason, live, market_open)

        # Buys: best probabilities first, within every guardrail.
        sector_w = {}
        for sym, pos in positions.items():
            sec = SECTORS.get(sym, "Other")
            sector_w[sec] = sector_w.get(sec, 0) + abs(float(pos["market_value"])) / equity
        spendable = cash - cfg["cash_buffer_pct"] * equity
        candidates = sorted(
            (ev for ev in evals.values()
             if ev["symbol"] not in positions and ev["symbol"] not in pending
             and ev["p_up"] >= cfg["buy_threshold"]),
            key=lambda ev: -ev["p_up"])
        for ev in candidates:
            sym = ev["symbol"]
            if ev["noise"] > cfg["max_noise_ratio"]:
                ev["decision"] = "Too noisy"
                continue
            if live and self.trades_today >= cfg["max_trades_per_day"]:
                ev["decision"] = "Daily limit"
                continue
            weight = cfg["max_position_pct"] * min(1.0, cfg["target_volatility"] / max(ev["vol"], 1e-6))
            budget = min(weight * equity, spendable)
            sec = SECTORS.get(sym, "Other")
            if sector_w.get(sec, 0) + budget / equity > cfg["max_sector_pct"]:
                ev["decision"] = "Sector full"
                continue
            qty = int(budget // ev["price"])
            if qty < 1:
                ev["decision"] = "Cash buffer"
                continue
            ev["decision"] = "Buy"
            reason = (f"{ev['p_up']:.0%} chance of rising over {HORIZON} days; {ev['cycle']}; "
                      f"sized for {ev['vol']:.0%} volatility.")
            self._act("buy", sym, qty, reason, live, market_open)
            spendable -= qty * ev["price"]
            sector_w[sec] = sector_w.get(sec, 0) + qty * ev["price"] / equity

        self.radar = sorted(evals.values(), key=lambda ev: -ev["p_up"])
        self.last_run = datetime.now().strftime("%I:%M %p")

    def _act(self, side, sym, qty, reason, live, market_open):
        if live:
            try:
                order = self.client.submit_market_order(sym, qty, side)
                self.trades_today += 1
                self._note(side, sym, reason, qty, f"placed ({order.get('status')})")
            except Exception as e:
                self._note(side, sym, f"Order failed: {e}", qty, "failed")
            return
        # Dry run or market closed: report once, not every check.
        if self._last_decision.get(sym) == (side, qty):
            return
        self._last_decision[sym] = (side, qty)
        why = "dry run" if self.dry_run else "market closed, will retry when open"
        self._note(side, sym, reason, qty, f"would {side} ({why})")
