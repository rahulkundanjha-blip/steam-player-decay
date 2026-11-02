"""How fast do Steam player bases decay after launch, and what predicts it?

Decision rules, fixed before running:
- Schedule the first major content beat at the first month where median
  players fall below 50% of launch. Schedule the second where they fall below 25%.
- Treat launch size as a planning signal only if |Spearman rho| >= 0.3 between
  launch players and month-12 retention.
"""
import os
import re
import sys

import numpy as np
import pandas as pd
from scipy import stats

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gamestats as gs  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

OUT = os.path.join(HERE, "..", "out")
MIN_LAUNCH = 500          # average concurrent players; below this, ratios are noise
MILESTONES = [1, 2, 3, 6, 9, 12, 18, 24, 36]
TIERS = [(500, 2_000, "500–2k"), (2_000, 10_000, "2k–10k"), (10_000, np.inf, "10k+")]
REBOUND = 0.5             # month-on-month rise that counts as a comeback


def load():
    d = pd.read_csv(gs.data_path("steam_charts.csv"))
    assert d.shape == (83631, 7) and d["gamename"].nunique() == 1258, "unexpected file"
    assert not d.duplicated(["gamename", "year", "month"]).any()
    assert d["avg"].ge(0).all()
    d["date"] = pd.to_datetime(d["year"].astype(str) + " " + d["month"].str.strip(), format="%Y %B")
    d["m"] = d["date"].dt.year * 12 + d["date"].dt.month
    return d.sort_values(["gamename", "m"])


def cohort(d):
    """Games first tracked after the file starts; month 0 is the higher of their first two months."""
    file_start = d["m"].min()
    rows = []
    for name, g in d.groupby("gamename"):
        first = g["m"].iloc[0]
        if first <= file_start + 5:
            continue
        s = g.set_index("m")["avg"]
        head = s[s.index <= first + 1]
        m0 = int(head.idxmax())
        base = float(head.max())
        if base < MIN_LAUNCH:
            continue
        for m, v in s[s.index >= m0].items():
            rows.append({"game": name, "m0": m0, "base": base, "k": int(m - m0), "ret": v / base, "avg": v})
    return pd.DataFrame(rows)


def sensitivity_curve(d, base="max2", only=None, min_launch=MIN_LAUNCH):
    """Median retention under a different launch definition or game set."""
    start, last = d["m"].min(), d["m"].max()
    rows = []
    for name, g in d.groupby("gamename"):
        first = g["m"].iloc[0]
        if first <= start + 5 or (only is not None and name not in only):
            continue
        s = g.set_index("m")["avg"]
        if base == "max2":
            head = s[s.index <= first + 1]
            m0, b = int(head.idxmax()), head.max()
        elif base == "first":
            m0, b = int(first), s.loc[first]
        else:
            if first + 1 not in s.index:
                continue
            m0, b = int(first + 1), s.loc[first + 1]
        if b >= min_launch:
            rows.append((m0, b, s))
    med = {}
    for k in range(1, 37):
        v = [s.loc[m0 + k] / b for m0, b, s in rows if m0 + k <= last and m0 + k in s.index]
        med[k] = float(np.median(v))
    below = next((k for k, x in med.items() if x < .5), None)
    return {"games": len(rows), "m1": med[1], "m2": med[2], "m3": med[3], "m6": med[6], "m12": med[12], "first_month_below_50": below}


def release_check(d):
    """Is the first tracked month really the release month? Uses a separate Steam release-date file."""
    rel = pd.read_csv(gs.data_path("steam_releases.csv"))
    rel["date"] = pd.to_datetime(rel["release_date"], errors="coerce")
    norm = lambda x: re.sub(r"[^a-z0-9]", "", str(x).lower())
    rel = rel.dropna(subset=["date"]).assign(k=lambda r: r["game"].map(norm)).drop_duplicates("k").set_index("k")
    first = d.groupby("gamename")["m"].first()
    first = first[first > d["m"].min() + 5]
    keys = pd.Series({n: norm(n) for n in first.index})
    keys = keys[keys.isin(rel.index)]
    diff = first[keys.index] - (rel.loc[keys, "date"].dt.year.values * 12 + rel.loc[keys, "date"].dt.month.values)
    near = set(diff.index[diff.abs() <= 1])
    return near, {"games_matched": len(diff), "of_games": len(first), "within_1_month": float((diff.abs() <= 1).mean()),
                  "tracked_earlier_than_release": float((diff < -1).mean()), "tracked_later_than_release": float((diff > 1).mean()),
                  "tracked_6plus_months_later": float((diff > 6).mean()), "median_months_difference": float(diff.median())}


def main():
    gs.style()
    d = load()
    c = cohort(d)
    games = c.drop_duplicates("game")[["game", "m0", "base"]]
    last = d["m"].max()
    total_months = len(pd.period_range(d["date"].min(), d["date"].max(), freq="M"))
    facts = {"checks": {"rows": len(d), "games": d["gamename"].nunique(), "first_month": str(d["date"].min().date()),
                        "last_month": str(d["date"].max().date()), "months_covered": total_months},
             "cohort": {"games": len(games), "min_launch_players": MIN_LAUNCH,
                        "excluded_tracked_from_start_or_small": d["gamename"].nunique() - len(games)}}

    # Median retention at each month, among games old enough to have reached it.
    curve, ms = [], {}
    for k in range(0, 37):
        eligible = games[games["m0"] + k <= last]
        v = c[c["k"] == k].set_index("game")["ret"].reindex(eligible["game"]).dropna()
        row = {"k": k, "games": len(v), "median": float(v.median()), "p25": float(v.quantile(.25)), "p75": float(v.quantile(.75))}
        curve.append(row)
        if k in MILESTONES:
            ms[k] = {**gs.bootstrap_stat(v.values), "p25": row["p25"], "p75": row["p75"],
                     "still_above_half": float((v >= .5).mean())}
    curve = pd.DataFrame(curve)
    facts["retention"] = ms
    below = lambda t: int(curve.loc[curve["median"] < t, "k"].iloc[0])
    facts["decision"] = {"first_beat_month_below_50": below(.5), "second_beat_month_below_25": below(.25)}

    # Monthly decay rate between milestones, from the median curve.
    seg = []
    for a, b in [(0, 3), (3, 6), (6, 12), (12, 24), (24, 36)]:
        ra, rb = [curve.loc[curve.k == x, "median"].iloc[0] for x in (a, b)]
        seg.append({"from": a, "to": b, "monthly_decay": 1 - (rb / ra) ** (1 / (b - a))})
    facts["monthly_decay"] = seg

    # Launch size as a predictor.
    r12 = c[c["k"] == 12].set_index("game")["ret"]
    j = games.set_index("game").join(r12.rename("r12"), how="inner")
    rho, p = stats.spearmanr(j["base"], j["r12"])
    facts["launch_size"] = {"games": len(j), "spearman_rho": float(rho), "p_value": float(p), "planning_signal": bool(abs(rho) >= .3)}
    tiers = []
    for lo, hi, label in TIERS:
        t = j[(j["base"] >= lo) & (j["base"] < hi)]["r12"]
        tiers.append({"tier": label, **gs.bootstrap_stat(t.values)})
    facts["tiers_m12"] = tiers
    facts["launch_size"]["kruskal_p"] = float(stats.kruskal(*[j[(j["base"] >= lo) & (j["base"] < hi)]["r12"] for lo, hi, _ in TIERS]).pvalue)

    # Launch cohorts: did retention differ by launch year? 2019 cohorts reach month 12 in the pandemic.
    j["year"] = (j["m0"] - 1) // 12
    facts["cohort_years_m12"] = [{"year": int(y), **gs.bootstrap_stat(g["r12"].values)} for y, g in j.groupby("year") if len(g) >= 20]

    # Comebacks: a rise of 50%+ month on month, months 3 to 24.
    c2 = c.sort_values(["game", "k"]).copy()
    c2["prev"] = c2.groupby("game")["avg"].shift()
    c2["prev_k"] = c2.groupby("game")["k"].shift()
    reb = c2[(c2["k"].between(3, 24)) & (c2["prev_k"] == c2["k"] - 1) & (c2["prev"] > 0) & (c2["avg"] / c2["prev"] >= 1 + REBOUND)]
    old = games[games["m0"] + 24 <= last]
    any_reb = old["game"].isin(reb["game"])
    facts["comebacks"] = {"games_observed_24_months": len(old), **{k: v for k, v in gs.rate(int(any_reb.sum()), len(old)).items() if k != "n"}}
    fade = []
    for _, r in reb[reb["game"].isin(old["game"])].sort_values("k").groupby("game").head(1).iterrows():
        after = c[(c["game"] == r["game"]) & (c["k"] == r["k"] + 3)]["avg"]
        if len(after) and r["prev"] > 0:
            fade.append(float(after.iloc[0] / r["prev"]))
    facts["comebacks"]["median_players_3_months_later_vs_before"] = gs.bootstrap_stat(fade)

    # Second look: how much do the launch definition and the game set matter?
    near, facts["release_check"] = release_check(d)
    panel = {n for n, g in d.groupby("gamename") if g["m"].iloc[-1] - g["m"].iloc[0] >= 37}
    facts["sensitivity"] = {"as_reported": sensitivity_curve(d), "launch_is_first_month": sensitivity_curve(d, "first"),
                            "launch_is_second_month": sensitivity_curve(d, "second"), "launch_2000_plus": sensitivity_curve(d, min_launch=2000),
                            "tracked_36_plus_months": sensitivity_curve(d, only=panel), "release_date_confirmed": sensitivity_curve(d, only=near)}
    last_tracked = d.groupby("gamename")["m"].last()
    facts["checks"]["games_no_longer_tracked_at_end"] = int((last_tracked < last).sum())

    # Second look: comebacks by player base, against months with no jump.
    months = []
    for name, g in c.groupby("game"):
        s = g.set_index("k")["avg"]
        for k in s.index:
            if 3 <= k <= 24 and k - 1 in s.index and k + 3 in s.index and s[k - 1] > 0:
                months.append((name, s[k - 1], s[k] / s[k - 1], s[k + 3] / s[k - 1], s[k + 3] / s[k]))
    mdf = pd.DataFrame(months, columns=["game", "prev", "jump", "after", "kept"])
    comeback_sizes = []
    for floor in (0, 500):
        q = mdf[(mdf["prev"] >= floor) & mdf["game"].isin(old["game"])]
        j = q[q["jump"] >= 1 + REBOUND]
        comeback_sizes.append({"min_players_before": floor, "games": int(q["game"].nunique()), "games_with_jump": int(j["game"].nunique()),
                               "share": j["game"].nunique() / q["game"].nunique(), "after_3_months_vs_before": float(j["after"].median()),
                               "no_jump_months_after_3_months_vs_before": float(q[q["jump"] < 1 + REBOUND]["after"].median()),
                               "share_of_jump_peak_kept_3_months_later": float(j["kept"].median())})
    facts["comeback_sizes"] = comeback_sizes

    pd.DataFrame(curve).to_csv(os.path.join(OUT, "retention_curve.csv"), index=False)

    fig, ax = plt.subplots(figsize=(10, 4.2))
    ax.fill_between(curve.k, 100 * curve.p25, 100 * curve.p75, color=gs.NEUTRAL, alpha=.16, linewidth=0, label="middle 50% of games")
    ax.plot(curve.k, 100 * curve["median"], color=gs.NEUTRAL, label="median game")
    for t, label in ((50, "first beat line: 50%"), (25, "second beat line: 25%")):
        ax.axhline(t, color=gs.MUTED, lw=1, zorder=1)
        ax.text(35.8, t + 1.2, label, ha="right", color=gs.INK2, fontsize=9)
    ax.set_xlim(0, 36); ax.set_ylim(0, 105)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.0f}%"))
    ax.set_xlabel("months since launch month"); ax.set_ylabel("players, % of launch month")
    ax.set_title(f"Steam player decay after launch ({len(games):,} games)")
    ax.legend(loc="upper right")
    gs.save_fig(fig, OUT, "01_decay_curve.png")

    fig, ax = plt.subplots(figsize=(10, 4.2))
    for (lo, hi, label), col in zip(TIERS, gs.SERIES):
        ids = games[(games.base >= lo) & (games.base < hi)]
        pts = []
        for k in range(0, 25):
            el = ids[ids.m0 + k <= last]
            v = c[c.k == k].set_index("game")["ret"].reindex(el.game).dropna()
            pts.append(100 * v.median())
        ax.plot(range(25), pts, color=col, label=f"{label} launch players")
    ax.set_xlim(0, 24); ax.set_ylim(0, 105)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.0f}%"))
    ax.set_xlabel("months since launch month"); ax.set_ylabel("median players, % of launch month")
    ax.set_title("Decay by launch size (median game in each group)")
    ax.legend(loc="upper right")
    gs.save_fig(fig, OUT, "02_decay_by_launch_size.png")
    gs.save_results(facts, OUT)
    return facts


if __name__ == "__main__":
    import json
    print(json.dumps(main(), indent=1, default=float))
