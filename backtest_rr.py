import numpy as np
import signal_bot as sb
from backtest import trades_from

RRS = [1.5, 2.0, 3.0]


def stat(tr):
    if not tr:
        return 0, 0, 0.0, 0.0
    r = np.array([t["r"] for t in tr])
    w, lo = r[r > 0], r[r <= 0]
    pf = w.sum() / abs(lo.sum()) if len(lo) and lo.sum() != 0 else float("inf")
    return len(tr), len(w) / len(tr) * 100, r.sum(), pf


def main():
    data = {}
    for name, tk in sb.SYMS.items():
        df = sb.load(tk)
        if df is not None and len(df) >= 60:
            data[name] = df
    lines = ["📊 RR comparison (sab R mein)\n"]
    for rr in RRS:
        sb.RR = rr
        per, allt = {}, []
        for name, df in data.items():
            tr = trades_from(sb.run(df, name))
            per[name] = tr; allt += tr
        n, wr, tot, pf = stat(allt)
        lines.append(f"━ 1:{rr:g} ━\nTrades {n} | Win {wr:.0f}% | Total {tot:+.1f} R | PF {pf:.2f}")
        lines.append(" | ".join(f"{k[:5]} {stat(v)[2]:+.1f}R" for k, v in per.items()) + "\n")
    lines.append("Brokerage/slippage shamil nahi.")
    sb.notify("\n".join(lines))


if __name__ == "__main__":
    main()
