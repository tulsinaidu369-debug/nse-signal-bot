import json, os, sys
import numpy as np
import pandas as pd
import requests
import yfinance as yf

CFG = json.load(open("config.json"))
N = CFG["pivot_n"]; ATR_P = CFG["atr_period"]; MULT = CFG["atr_mult"]
STATE_FILE = "state.json"


def load(sym):
    df = yf.download(sym, period="59d", interval="15m",
                     auto_adjust=False, progress=False)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close"]].dropna()
    df.index = df.index.tz_convert("Asia/Kolkata")
    now = pd.Timestamp.now(tz="Asia/Kolkata")
    df = df[df.index + pd.Timedelta(minutes=15) <= now]
    return df


def atr(df):
    pc = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"],
                    (df["High"] - pc).abs(),
                    (df["Low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / ATR_P, adjust=False).mean()


def run(df, sym, result_dates):
    o, h, l, c = (df[k].values for k in ["Open", "High", "Low", "Close"])
    a = atr(df).values
    dates = [ts.date().isoformat() for ts in df.index]
    highs, lows = [], []
    pos, pending, used = None, None, set()
    ev = []

    def add(t, kind, msg):
        ev.append({"ts": df.index[t].isoformat(), "kind": kind, "msg": msg})

    for t in range(len(df)):
        if pending and pos is None:
            side, sl0 = pending; pending = None
            ok = (o[t] > sl0) if side == "LONG" else (o[t] < sl0)
            if ok:
                pos = {"side": side, "entry": o[t], "sl": sl0}

        if pos:
            if pos["side"] == "LONG" and l[t] <= pos["sl"]:
                px = min(o[t], pos["sl"])
                add(t, "EXIT", f"🔴 EXIT LONG {sym}\nPrice ~ {px:.2f} (SL hit)\nEntry tha {pos['entry']:.2f}")
                pos = None
            elif pos["side"] == "SHORT" and h[t] >= pos["sl"]:
                px = max(o[t], pos["sl"])
                add(t, "EXIT", f"🔴 EXIT SHORT {sym}\nPrice ~ {px:.2f} (SL hit)\nEntry tha {pos['entry']:.2f}")
                pos = None

        new_low = new_high = False
        i = t - N
        if i >= N:
            if h[i] > h[i - N:i].max() and h[i] >= h[i + 1:t + 1].max():
                highs.append((i, h[i])); new_high = True
            if l[i] < l[i - N:i].min() and l[i] <= l[i + 1:t + 1].min():
                lows.append((i, l[i])); new_low = True

        if np.isnan(a[t]):
            continue

        if pos and pos["side"] == "LONG" and new_low and len(lows) >= 2 \
                and lows[-1][1] > lows[-2][1]:
            new = lows[-1][1] - MULT * a[t]
            if new > pos["sl"]:
                pos["sl"] = new
                add(t, "TRAIL", f"🔼 TRAIL SL LONG {sym}\nNaya SL: {new:.2f}")
        if pos and pos["side"] == "SHORT" and new_high and len(highs) >= 2 \
                and highs[-1][1] < highs[-2][1]:
            new = highs[-1][1] + MULT * a[t]
            if new < pos["sl"]:
                pos["sl"] = new
                add(t, "TRAIL", f"🔽 TRAIL SL SHORT {sym}\nNaya SL: {new:.2f}")

        if pos or pending or t < 1:
            continue
        if dates[t] in result_dates:
            continue

        if highs and len(lows) >= 2:
            sh, hl, prev = highs[-1], lows[-1], lows[-2]
            key = ("L", sh[0], hl[0])
            if hl[0] > sh[0] and hl[1] > prev[1] and key not in used \
                    and c[t] > sh[1] and c[t - 1] <= sh[1]:
                sl0 = hl[1] - MULT * a[t]
                used.add(key); pending = ("LONG", sl0)
                add(t, "BUY", f"🟢 BUY SIGNAL {sym}\n15m close {c[t]:.2f} > Swing High {sh[1]:.2f}\nEntry: NEXT candle open par\nSL: {sl0:.2f}")
                continue

        if lows and len(highs) >= 2:
            sw, lh, prev = lows[-1], highs[-1], highs[-2]
            key = ("S", sw[0], lh[0])
            if lh[0] > sw[0] and lh[1] < prev[1] and key not in used \
                    and c[t] < sw[1] and c[t - 1] >= sw[1]:
                sl0 = lh[1] + MULT * a[t]
                used.add(key); pending = ("SHORT", sl0)
                add(t, "SELL", f"🔴 SELL SIGNAL {sym}\n15m close {c[t]:.2f} < Swing Low {sw[1]:.2f}\nEntry: NEXT candle open par\nSL: {sl0:.2f}")
    return ev


def notify(text):
    print(text, "\n")
    tok, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if tok and chat:
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          data={"chat_id": chat, "text": text}, timeout=20)
        if not r.ok:
            print("Telegram error:", r.text, file=sys.stderr)


def main():
    state = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}
    for sym, meta in CFG["symbols"].items():
        try:
            df = load(sym)
            if df is None or len(df) < 3 * N + ATR_P:
                print(f"{sym}: data kam/nahi mila"); continue
            events = run(df, sym, set(meta.get("result_dates", [])))
            last_bar = df.index[-1].isoformat()
            seen = state.get(sym)
            for e in events:
                fresh = e["ts"] > seen if seen else e["ts"] == last_bar
                if fresh:
                    notify(f"{e['msg']}\n🕒 {e['ts'][:16]}")
            state[sym] = last_bar
        except Exception as ex:
            print(f"{sym}: error {ex}", file=sys.stderr)
    json.dump(state, open(STATE_FILE, "w"), indent=1)


if __name__ == "__main__":
    main()
