import io, json, os, sys, time
import numpy as np
import pandas as pd
import requests
import yfinance as yf

CFG = json.load(open("config.json"))
N = CFG["pivot_n"]; ATR_P = CFG["atr_period"]; MULT = CFG["atr_mult"]
STATE_FILE = "state.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
N500_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
TODAY = pd.Timestamp.now(tz="Asia/Kolkata").date().isoformat()


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


def fetch_universe():
    r = requests.get(N500_URL, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    syms = [str(s).strip() for s in df["Symbol"].dropna()]
    if len(syms) < 300:
        raise ValueError("NSE500 list chhoti aayi")
    return syms


def fetch_results_calendar():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    s.get("https://www.nseindia.com", timeout=30)
    t = pd.Timestamp.now(tz="Asia/Kolkata")
    f = (t - pd.Timedelta(days=3)).strftime("%d-%m-%Y")
    e = (t + pd.Timedelta(days=5)).strftime("%d-%m-%Y")
    url = ("https://www.nseindia.com/api/event-calendar"
           f"?index=equities&from_date={f}&to_date={e}")
    r = s.get(url, headers={
        "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-event-calendar",
        "Accept": "application/json"}, timeout=40)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        data = data.get("data", [])
    out = {}
    for row in data:
        txt = (str(row.get("purpose", "")) + " " + str(row.get("bm_desc", ""))).lower()
        if "result" not in txt:
            continue
        d = pd.to_datetime(row.get("date") or row.get("bm_date"),
                           dayfirst=True, errors="coerce")
        sym = row.get("symbol")
        if sym and not pd.isna(d):
            out.setdefault(sym, []).append(d.date().isoformat())
    if not out:
        raise ValueError("Calendar khali aaya")
    return out


def load_many(syms):
    out = {}
    now = pd.Timestamp.now(tz="Asia/Kolkata")
    for i in range(0, len(syms), 100):
        chunk = [s + ".NS" for s in syms[i:i + 100]]
        try:
            raw = yf.download(chunk, period="59d", interval="15m",
                              group_by="ticker", auto_adjust=False,
                              progress=False, threads=True)
        except Exception as ex:
            print("download error:", ex, file=sys.stderr); continue
        for t in chunk:
            try:
                d = raw[t][["Open", "High", "Low", "Close"]].dropna()
            except Exception:
                continue
            if d.empty:
                continue
            if d.index.tz is None:
                d.index = d.index.tz_localize("UTC")
            d.index = d.index.tz_convert("Asia/Kolkata")
            d = d[d.index + pd.Timedelta(minutes=15) <= now]
            if len(d) >= 3 * N + ATR_P:
                out[t[:-3]] = d
    return out


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


def main():
    state = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}
    warns = []

    # NSE 500 list (roz ek baar)
    if state.get("_n500", {}).get("date") != TODAY:
        try:
            state["_n500"] = {"date": TODAY, "syms": fetch_universe()}
        except Exception as ex:
            warns.append(f"NSE500 list nahi mili ({ex})")
    universe = state.get("_n500", {}).get("syms") or \
        [s.replace(".NS", "") for s in CFG.get("symbols", {})]

    # Result calendar (roz ek baar)
    if state.get("_cal", {}).get("date") != TODAY:
        try:
            state["_cal"] = {"date": TODAY, "dates": fetch_results_calendar()}
        except Exception as ex:
            warns.append(f"Result calendar nahi mila ({ex})")
    cal = state.get("_cal", {}).get("dates", {})

    if warns and state.get("_warn") != TODAY:
        notify("⚠️ " + "; ".join(warns) + "\nPurani/manual dates se kaam chal raha hai.")
        state["_warn"] = TODAY

    # manual dates (config.json) bhi jod do
    manual = {k.replace(".NS", ""): v.get("result_dates", [])
              for k, v in CFG.get("symbols", {}).items()}

    print(f"Universe: {len(universe)} | aaj result wale: "
          f"{sum(TODAY in v for v in cal.values())}")

    data = load_many(universe)
    for sym, df in data.items():
        try:
            rd = set(cal.get(sym, [])) | set(manual.get(sym, []))
            events = run(df, sym, rd)
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
