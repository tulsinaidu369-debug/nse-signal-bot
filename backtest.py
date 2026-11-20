import re
import numpy as np
import signal_bot as sb


def trades_from(ev):
    out, cur = [], None
    for e in ev:
        if e["kind"] == "ENTRY":
            first = e["msg"].split("\n")[0]
            risk = float(re.search(r"Risk: ([\d.]+)", e["msg"]).group(1))
            cur = {"side": "BUY" if "BUY" in first else "SELL", "risk": risk}
        elif cur:
            pnl = float(re.search(r"P/L: ([+-][\d.]+)", e["msg"]).group(1))
            out.append({**cur, "pnl": pnl, "r": pnl / cur["risk"],
                        "how": e["kind"], "ts": e["ts"]})
            cur = None
    return out


def summary(name, tr):
    if not tr:
        return f"{name}: koi trade nahi bana"
    pnl = np.array([t["pnl"] for t in tr]); r = np.array([t["r"] for t in tr])
    w, lo = pnl[pnl > 0], pnl[pnl <= 0]
    pf = w.sum() / abs(lo.sum()) if len(lo) and lo.sum() != 0 else float("inf")
    eq = np.cumsum(pnl); dd = (np.maximum.accumulate(eq) - eq).max()
    how = {k: sum(t["how"] == k for t in tr) for k in ["TARGET", "SL", "TIME"]}
    b = [t["pnl"] for t in tr if t["side"] == "BUY"]
    s = [t["pnl"] for t in tr if t["side"] == "SELL"]
    return (f"📊 {name}\n"
            f"Trades: {len(tr)} | Win: {len(w)} | Loss: {len(lo)}\n"
            f"Win rate: {len(w) / len(tr) * 100:.0f}%\n"
            f"Target: {how['TARGET']} | SL: {how['SL']} | 3:15 exit: {how['TIME']}\n"
            f"Total: {pnl.sum():+.0f} pts | {r.sum():+.1f} R\n"
            f"Avg win: {w.mean() if len(w) else 0:+.1f} | Avg loss: {lo.mean() if len(lo) else 0:+.1f}\n"
            f"Profit factor: {pf:.2f}\n"
            f"Max drawdown: {dd:.0f} pts\n"
            f"BUY: {len(b)} trades {sum(b):+.0f} pts | SELL: {len(s)} trades {sum(s):+.0f} pts")


def main():
    allt, first, last = [], None, None
    for name, tk in sb.SYMS.items():
        df = sb.load(tk)
        if df is None or len(df) < 60:
            print(name, "data nahi mila"); continue
        first, last = df.index[0].date(), df.index[-1].date()
        tr = trades_from(sb.run(df, name))
        allt += tr
        sb.notify(summary(name, tr))
    if allt:
        sb.notify(summary("SAB MILAKAR", allt) +
                  f"\n\nPeriod: {first} se {last}\nBrokerage/slippage shamil nahi.")


if __name__ == "__main__":
    main()
