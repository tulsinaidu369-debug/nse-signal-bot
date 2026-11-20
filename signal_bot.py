import json, os, sys, time
import numpy as np
import pandas as pd
import requests
import yfinance as yf

SYMS = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "SENSEX": "^BSESN"}
N = 3            # pivot left/right candles
ATR_P = 14
MULT = 0.25      # SL buffer = 0.25 x ATR
RR = 2.0         # target 1:2
EQ_TOL = 0.10    # equal high/low tolerance (x ATR)
MIN_GAP = 10     # equal levels ke beech min candles
LOOKBACK = 100   # equal level dhundhne ki max candles
STATE_FILE = "state.json"


def notify(text):
    print(text, "\n")
    tok, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if tok and chat:
        try:
            r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                              data={"chat_id": chat, "text": text}, timeout=20)
            if not r.ok:
                print("Telegram error:", r.text, file=sys.stderr)
        except Exception as ex:
            print("Telegram error:", ex, file=sys.stderr)
        time.sleep(0.4)


def load(tk):
    df = yf.download(tk, period="59d", interval="15m",
                     auto_adjust=False, progress=False)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close"]].dropna()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert("Asia/Kolkata")
    now = pd.Timestamp.now(tz="Asia/Kolkata")
    return df[df.index + pd.Timedelta(minutes=15) <= now]  # sirf closed candles


def atr(df):
    pc = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"],
                    (df["High"] - pc).abs(),
                    (df["Low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / ATR_P, adjust=False).mean()


def run(df, name):
    o, h, l, c = (df[k].values for k in ["Open", "High", "Low", "Close"])
    a = atr(df).values
    day = np.array([ts.date().isoformat() for ts in df.index])
    mins = np.array([ts.hour * 60 + ts.minute for ts in df.index])  # candle start
    g = df.groupby(day)
    dH, dL = g["High"].max(), g["Low"].min()
    days = list(dH.index)
    prev = {days[i]: days[i - 1] for i in range(1, len(days))}

    levels, sw_h, sw_l = [], [], []
    pos, cur, ev = None, None, []

    def add(t, kind, msg):
        ev.append({"ts": df.index[t].isoformat(), "kind": kind, "msg": msg})

    for t in range(len(df)):
        d = day[t]
        if d != cur:                       # naya din: purane PDH/PDL hatao
            cur = d
            levels = [x for x in levels if x["kind"] == "EQ" and not x["dead"]]
            if d in prev:
                levels.append({"side": "LOW", "p": float(dL[prev[d]]), "kind": "PDL", "t0": t, "dead": False})
                levels.append({"side": "HIGH", "p": float(dH[prev[d]]), "kind": "PDH", "t0": t, "dead": False})

        # 1) open trade management (SL pehle, conservative)
        if pos:
            s, sl, tp = pos["side"], pos["sl"], pos["tp"]
            px = kind = None
            if s == "BUY":
                if l[t] <= sl: px, kind = min(o[t], sl), "SL"
                elif h[t] >= tp: px, kind = max(o[t], tp), "TARGET"
            else:
                if h[t] >= sl: px, kind = max(o[t], sl), "SL"
                elif l[t] <= tp: px, kind = min(o[t], tp), "TARGET"
            if kind is None and mins[t] >= 15 * 60:   # 3:15 PM exit
                px, kind = c[t], "TIME"
            if kind:
                pnl = (px - pos["entry"]) if s == "BUY" else (pos["entry"] - px)
                label = {"SL": "🛑 SL HIT", "TARGET": "🎯 TARGET HIT", "TIME": "⏰ 3:15 EXIT"}[kind]
                add(t, kind, f"{label} {s} {name}\nPrice ~ {px:.2f}\nEntry tha {pos['entry']:.2f}\nP/L: {pnl:+.1f} pts")
                pos = None

        # 2) pivots (right side N candle close hone ke baad confirm)
        i = t - N
        if i >= N and not np.isnan(a[t]):
            if l[i] < l[i - N:i].min() and l[i] <= l[i + 1:t + 1].min():
                for j, pj in reversed(sw_l):
                    if i - j > LOOKBACK: break
                    if i - j >= MIN_GAP and abs(l[i] - pj) <= EQ_TOL * a[t]:
                        lv = min(l[i], pj)
                        if c[j + 1:t + 1].min() >= lv:
                            levels.append({"side": "LOW", "p": float(lv), "kind": "EQ", "t0": t, "dead": False})
                        break
                sw_l.append((i, l[i]))
            if h[i] > h[i - N:i].max() and h[i] >= h[i + 1:t + 1].max():
                for j, pj in reversed(sw_h):
                    if i - j > LOOKBACK: break
                    if i - j >= MIN_GAP and abs(h[i] - pj) <= EQ_TOL * a[t]:
                        lv = max(h[i], pj)
                        if c[j + 1:t + 1].max() <= lv:
                            levels.append({"side": "HIGH", "p": float(lv), "kind": "EQ", "t0": t, "dead": False})
                        break
                sw_h.append((i, h[i]))

        # 3) sweep check
        buys = [x for x in levels if x["side"] == "LOW" and not x["dead"] and x["t0"] < t and l[t] < x["p"] and c[t] > x["p"]]
        sells = [x for x in levels if x["side"] == "HIGH" and not x["dead"] and x["t0"] < t and h[t] > x["p"] and c[t] < x["p"]]
        for x in buys + sells:
            x["dead"] = True               # ek level ek hi baar
        for x in levels:                   # close ke saath toot gaya = level khatam
            if x["side"] == "LOW" and c[t] < x["p"]: x["dead"] = True
            if x["side"] == "HIGH" and c[t] > x["p"]: x["dead"] = True

        if pos or np.isnan(a[t]) or not (570 <= mins[t] <= 855):   # 9:30 se 2:30 PM close
            continue
        if bool(buys) == bool(sells):      # koi nahi ya dono: no trade
            continue
        if buys:
            x = max(buys, key=lambda z: z["p"])
            sl = l[t] - MULT * a[t]; risk = c[t] - sl
            if risk <= 0: continue
            tp = c[t] + RR * risk
            pos = {"side": "BUY", "entry": c[t], "sl": sl, "tp": tp}
            nm = {"PDL": "Prev Day Low", "EQ": "Equal Low"}[x["kind"]]
            add(t, "ENTRY", f"🟢 BUY {name}\nSweep: {nm} {x['p']:.2f} (wick low {l[t]:.2f})\nEntry: {c[t]:.2f} (15m close)\nSL: {sl:.2f}\nTarget: {tp:.2f} (1:{RR:g})\nRisk: {risk:.1f} pts")
        else:
            x = min(sells, key=lambda z: z["p"])
            sl = h[t] + MULT * a[t]; risk = sl - c[t]
            if risk <= 0: continue
            tp = c[t] - RR * risk
            pos = {"side": "SELL", "entry": c[t], "sl": sl, "tp": tp}
            nm = {"PDH": "Prev Day High", "EQ": "Equal High"}[x["kind"]]
            add(t, "ENTRY", f"🔴 SELL {name}\nSweep: {nm} {x['p']:.2f} (wick high {h[t]:.2f})\nEntry: {c[t]:.2f} (15m close)\nSL: {sl:.2f}\nTarget: {tp:.2f} (1:{RR:g})\nRisk: {risk:.1f} pts")
    return ev


def main():
    old = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}
    state = {k: old[k] for k in SYMS if k in old}
    for name, tk in SYMS.items():
        try:
            df = load(tk)
            if df is None or len(df) < 60:
                print(f"{name}: data kam/nahi mila"); continue
            events = run(df, name)
            last_bar = df.index[-1].isoformat()
            seen = state.get(name)
            for e in events:
                fresh = e["ts"] > seen if seen else e["ts"] == last_bar
                if fresh:
                    notify(f"{e['msg']}\n🕒 {e['ts'][:16].replace('T', ' ')}")
            state[name] = last_bar
            print(f"{name}: {len(df)} candles, last {last_bar[:16]}, events {len(events)}")
        except Exception as ex:
            print(f"{name}: error {ex}", file=sys.stderr)
    json.dump(state, open(STATE_FILE, "w"), indent=1)


if __name__ == "__main__":
    main()
