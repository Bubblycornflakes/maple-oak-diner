#!/usr/bin/env python3
"""Run the five analyses over the data the QR-menu system collected, and write dashboard datasets to ./out.

Menu profit (menu engineering), demand forecasting (Holt-Winters, weekly seasonality), rota (staffing need
per hour vs the schedule), supplier prices (spend, drift, switching savings) and review analysis (rating
trend, theme sentiment), plus waste. Standard library only.
"""
import csv, json, math, os, datetime as dt, statistics as st
from collections import defaultdict, Counter

HERE = os.path.dirname(os.path.abspath(__file__))
DATA, OUT = os.path.join(HERE, "data"), os.path.join(HERE, "out")
os.makedirs(OUT, exist_ok=True)
meta = json.load(open(os.path.join(DATA, "meta.json")))
START, END, CHANGE = (dt.date.fromisoformat(meta[k]) for k in ("start", "end", "change_date"))
CLOSED = set(meta["closed"])
WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKS_BEFORE, WEEKS_AFTER = (CHANGE - START).days // 7, (END - CHANGE).days // 7 + 1
def period(d): return "after" if d >= CHANGE else "before"
def r2(x): return round(x, 2)
def write(name, rows):
    with open(os.path.join(OUT, name + ".json"), "w") as f: json.dump(rows, f)
    print(f"{name:18s} {len(rows):6,} rows")

def rows(name):
    with open(os.path.join(DATA, name + ".csv"), newline="") as f: return list(csv.DictReader(f))
menu = {m["item_id"]: m for m in rows("menu")}
orders = rows("orders")
for o in orders:
    o["date_d"] = dt.date.fromisoformat(o["date"]); o["hour"] = int(o["hour"]); o["party_size"] = int(o["party_size"])
    o["ticket_min"] = float(o["ticket_min"]); o["turn_min"] = float(o["turn_min"]); o["revenue"] = float(o["revenue"]); o["food_cost"] = float(o["food_cost"])
lines = rows("order_lines")
waste = rows("waste"); rota = rows("rota"); quotes = rows("supplier_quotes"); reviews = rows("reviews")

# ------------------------------------------------------------------ review tagging from the text
# Rule-based: each sentence gets a theme from topic words and a sign from negative cue words.
# It reads only the review text; the simulator's own tags are kept aside and used only to score the tagger.
import re
TOPIC = [("qr menu", r"\bqr\b|phone|paper menu|menu site|ordering from|scan"),
         ("wait", r"wait|minutes|slammed|fast|quick|turnaround|arrived|came out"),
         ("portions", r"portion|generous|tiny cup|sides\b"),
         ("value", r"price|pricey|value|\$|steep|under \$"),
         ("service", r"server|staff|manager|flag|checked|attentive"),
         ("atmosphere", r"booth|playlist|patio|loud|cozy"),
         ("food", r"burger|ribs|wings|salmon|chicken|fries|crab|grits|cooked|bland|food|sandwich")]
NEG = r"overcooked|lukewarm|filler|bland|waited|slammed|long wait|with the mains|hard to|nobody|pricey|steep|didn't|miss |slow|smaller|tiny|too loud|worn"
def tag_text(text):
    out = []
    for sent in [x.strip() for x in text.split(".") if x.strip()]:
        low = sent.lower()
        theme = next((t for t, rx in TOPIC if re.search(rx, low)), None)
        if theme: out.append(f"{theme}:{'-' if re.search(NEG, low) else '+'}")
    return ";".join(out)
agree = tot = 0
for r in reviews:
    truth = set(r["themes"].split(";")); got = set(tag_text(r["text"]).split(";")) - {""}
    agree += len(truth & got); tot += len(truth | got)
    r["sim_themes"] = r["themes"]; r["themes"] = ";".join(sorted(got))
TAGGER_AGREEMENT = round(100 * agree / tot, 1)
print("tagger agreement with simulator tags:", TAGGER_AGREEMENT, "%")

# ------------------------------------------------------------------ daily & weekly series
daily = defaultdict(lambda: dict(covers=0, parties=0, revenue=0.0, food_cost=0.0, tickets=[], turns=[]))
for o in orders:
    d = daily[o["date"]]
    d["covers"] += o["party_size"]; d["parties"] += 1; d["revenue"] += o["revenue"]; d["food_cost"] += o["food_cost"]
    d["tickets"].append(o["ticket_min"]); d["turns"].append(o["turn_min"])
waste_by_day = defaultdict(float)
for w in waste: waste_by_day[w["date"]] += float(w["waste_cost"])
def pct(xs, p):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else None
daily_rows = []
d = START
while d <= END:
    k = d.isoformat(); x = daily.get(k)
    daily_rows.append(dict(day=k, weekday=WD[d.weekday()], period=period(d), kind="actual", closed=int(k in CLOSED),
        covers=x["covers"] if x else 0, parties=x["parties"] if x else 0, revenue=r2(x["revenue"]) if x else 0,
        food_cost=r2(x["food_cost"]) if x else 0, waste_cost=r2(waste_by_day[k]),
        median_ticket=r2(st.median(x["tickets"])) if x else None, p90_ticket=r2(pct(x["tickets"], .9)) if x else None,
        avg_turn=r2(st.mean(x["turns"])) if x else None, forecast=None, lo=None, hi=None))
    d += dt.timedelta(days=1)

# ------------------------------------------------------------------ demand forecast
# Three candidate models, compared on rolling holdouts (four 28-day windows, each forecast only from data before it):
#   seasonal naive - average of the last four same weekdays
#   additive Holt-Winters, 7-day season
#   multiplicative Holt-Winters, 7-day season
# Public holidays and closures are replaced by the same day a week earlier before fitting, so one-off days don't bend the weekly pattern.
HOLIDAYS = {"2025-11-27", "2025-11-28", "2025-12-24", "2025-12-25", "2025-12-31", "2026-01-01", "2026-02-08", "2026-02-14",
            "2026-05-10", "2026-06-21", "2026-07-04", "2026-09-07"}
def holt_winters(y, m, alpha, beta, gamma, h, mult=False):
    L = sum(y[:m]) / m; T = (sum(y[m:2 * m]) - sum(y[:m])) / (m * m)
    S = [(y[i] / L) if mult else (y[i] - L) for i in range(m)]
    fitted = []
    for t, v in enumerate(y):
        s = S[t % m]; f = (L + T) * s if mult else L + T + s; fitted.append(f)
        Ln = alpha * ((v / s) if mult else (v - s)) + (1 - alpha) * (L + T); T = beta * (Ln - L) + (1 - beta) * T
        S[t % m] = gamma * ((v / Ln) if mult else (v - Ln)) + (1 - gamma) * s; L = Ln
    fc = [((L + (i + 1) * T) * S[(len(y) + i) % m]) if mult else (L + (i + 1) * T + S[(len(y) + i) % m]) for i in range(h)]
    return fitted, fc
def fit_hw(y, h, mult):
    best = None
    for a in (0.1, 0.2, 0.3, 0.4):
        for b in (0.0, 0.01, 0.03):
            for g in (0.1, 0.2, 0.3, 0.5):
                fitted, fc = holt_winters(y, 7, a, b, g, h, mult)
                sse = sum((v - f) ** 2 for v, f in zip(y[14:], fitted[14:]))
                if best is None or sse < best[0]: best = (sse, (a, b, g), fitted, fc)
    return best
def fit_naive(y, h):
    fc = [st.mean(y[len(y) - 7 * k + (i % 7)] for k in range(1, 5)) for i in range(h)]
    fitted = [None] * 28 + [st.mean(y[t - 7 * k] for k in range(1, 5)) for t in range(28, len(y))]
    sse = sum((v - f) ** 2 for v, f in zip(y[28:], fitted[28:]))
    return (sse, None, fitted, fc)
MODELS = {"Seasonal naive": fit_naive, "Additive Holt-Winters": lambda y, h: fit_hw(y, h, False), "Multiplicative Holt-Winters": lambda y, h: fit_hw(y, h, True)}
series = []
for i, r in enumerate(daily_rows):
    series.append(r["covers"] if not (r["closed"] or r["day"] in HOLIDAYS) else series[i - 7])
actual_cov = [r["covers"] for r in daily_rows]
def mape_of(fc, start):
    pairs = [(actual_cov[start + i], f) for i, f in enumerate(fc) if actual_cov[start + i] > 0 and daily_rows[start + i]["day"] not in HOLIDAYS]
    return st.mean(abs(v - f) / v for v, f in pairs) * 100
model_rows = []
for name, fn in MODELS.items():
    errs = []
    for k in (4, 3, 2, 1):
        start = len(series) - 28 * k
        errs.append(mape_of(fn(series[:start], 28)[3], start))
    model_rows.append(dict(model=name, mape=r2(st.mean(errs)), mape_last=r2(errs[-1]), windows=4))
best_name = min(model_rows, key=lambda r: r["mape"])["model"]
for r in model_rows: r["chosen"] = int(r["model"] == best_name)
write("forecast_models", model_rows)
fit = MODELS[best_name]
sse, params, fitted, fc = fit(series[:-28], 28)
mape = mape_of(fc, len(series) - 28)
resid = [v - f for v, f in zip(series[:-28], fitted) if f is not None][14:]
resid_sd = st.pstdev(resid)
for r, f in zip(daily_rows[-28:], fc): r["forecast"] = r2(f); r["lo"] = r2(f - 1.28 * resid_sd); r["hi"] = r2(f + 1.28 * resid_sd)
sse2, params2, fitted2, fc2 = fit(series, 28)
sd2 = st.pstdev([v - f for v, f in zip(series, fitted2) if f is not None][14:])
ahead = []
for i, f in enumerate(fc2):
    d = END + dt.timedelta(days=i + 1)          # the next 28 days hold no public holiday, so no event adjustment is applied
    ahead.append(dict(day=d.isoformat(), weekday=WD[d.weekday()], period="forecast", kind="forecast", closed=0, covers=None, parties=None,
        revenue=None, food_cost=None, waste_cost=None, median_ticket=None, p90_ticket=None, avg_turn=None,
        forecast=r2(f), lo=r2(f - 1.28 * sd2 * math.sqrt(1 + i / 28)), hi=r2(f + 1.28 * sd2 * math.sqrt(1 + i / 28))))
write("daily", daily_rows + ahead)
forecast_meta = dict(model=best_name, mape_holdout=r2(mape), mape_rolling=r2(min(r["mape"] for r in model_rows)), params=params2,
                     holdout_days=28, horizon_days=28, forecast_4wk_covers=round(sum(fc2)), last_4wk_covers=sum(actual_cov[-28:]))

# weekly roll-up (52 actual + 4 forecast)
rate_by_role = {r["role"]: float(r["hourly_rate"]) for r in rota}
def weekly_labour(periodname):
    return sum((float(r["end_hour"]) - float(r["start_hour"])) * int(r["headcount"]) * float(r["hourly_rate"]) for r in rota if r["period"] == periodname)
rev_by_month = defaultdict(float)
weekly = []
all_rows = daily_rows + ahead
for wk in range(56):
    chunk = all_rows[wk * 7:(wk + 1) * 7]; ws = START + dt.timedelta(weeks=wk)
    act = [c for c in chunk if c["kind"] == "actual"]
    tix = [o["ticket_min"] for o in orders if ws <= o["date_d"] < ws + dt.timedelta(days=7)]
    peak = [o["ticket_min"] for o in orders if ws <= o["date_d"] < ws + dt.timedelta(days=7) and o["date_d"].weekday() in (4, 5) and 18 <= o["hour"] <= 20]
    rv = sum(c["revenue"] for c in act); fc_ = sum(c["food_cost"] for c in act)
    weekly.append(dict(week_start=ws.isoformat(), week=wk + 1, period="forecast" if not act else period(ws), kind="forecast" if not act else "actual",
        covers=sum(c["covers"] for c in act) if act else None, forecast=round(sum(c["forecast"] for c in chunk if c["forecast"] is not None)) if chunk[-1]["forecast"] is not None else None,
        lo=round(sum(c["lo"] for c in chunk)) if chunk[-1]["lo"] is not None and not act else None, hi=round(sum(c["hi"] for c in chunk)) if chunk[-1]["hi"] is not None and not act else None,
        revenue=r2(rv) if act else None, food_cost=r2(fc_) if act else None, food_cost_pct=r2(100 * fc_ / rv) if act else None,
        waste_cost=r2(sum(c["waste_cost"] for c in act)) if act else None,
        labour_cost=r2(weekly_labour(period(ws))) if act else None, labour_pct=r2(100 * weekly_labour(period(ws)) / rv) if act else None,
        median_ticket=r2(st.median(tix)) if tix else None, p90_ticket=r2(pct(tix, .9)) if tix else None, peak_median_ticket=r2(st.median(peak)) if peak else None))
write("weekly", weekly)

# ------------------------------------------------------------------ KPIs, before vs after
def agg(rowset):
    cov = sum(r["covers"] for r in rowset); rev = sum(r["revenue"] for r in rowset); fcst = sum(r["food_cost"] for r in rowset)
    return cov, rev, fcst, sum(r["waste_cost"] for r in rowset)
act_rows = [r for r in daily_rows]
bef = [r for r in act_rows if r["period"] == "before"]; aft = [r for r in act_rows if r["period"] == "after"]
cov, rev, fcst, wst = agg(act_rows); cb, rb, fb, wb = agg(bef); ca, ra, fa, wa = agg(aft)
tix_b = [o["ticket_min"] for o in orders if o["date_d"] < CHANGE]; tix_a = [o["ticket_min"] for o in orders if o["date_d"] >= CHANGE]
peak_b = [o["ticket_min"] for o in orders if o["date_d"] < CHANGE and o["date_d"].weekday() in (4, 5) and 18 <= o["hour"] <= 20]
peak_a = [o["ticket_min"] for o in orders if o["date_d"] >= CHANGE and o["date_d"].weekday() in (4, 5) and 18 <= o["hour"] <= 20]
rat_b = [int(r["rating"]) for r in reviews if dt.date.fromisoformat(r["date"]) < CHANGE]; rat_a = [int(r["rating"]) for r in reviews if dt.date.fromisoformat(r["date"]) >= CHANGE]
lab_b, lab_a = weekly_labour("before"), weekly_labour("after")
turn_b = st.mean(o["turn_min"] for o in orders if o["date_d"] < CHANGE); turn_a = st.mean(o["turn_min"] for o in orders if o["date_d"] >= CHANGE)
kpis = [
 dict(metric="covers", label="Covers served", unit="", value=cov, before=round(cb / WEEKS_BEFORE), after=round(ca / WEEKS_AFTER), basis="per week", good="up"),
 dict(metric="revenue", label="Revenue", unit="$", value=r2(rev), before=round(rb / WEEKS_BEFORE), after=round(ra / WEEKS_AFTER), basis="per week", good="up"),
 dict(metric="avg_check", label="Average check per guest", unit="$", value=r2(rev / cov), before=r2(rb / cb), after=r2(ra / ca), basis="per guest", good="up"),
 dict(metric="food_cost_pct", label="Food cost", unit="%", value=r2(100 * fcst / rev), before=r2(100 * fb / rb), after=r2(100 * fa / ra), basis="of revenue", good="down"),
 dict(metric="labour_pct", label="Labour cost", unit="%", value=r2(100 * (lab_b * WEEKS_BEFORE + lab_a * WEEKS_AFTER) / rev), before=r2(100 * lab_b * WEEKS_BEFORE / rb), after=r2(100 * lab_a * WEEKS_AFTER / ra), basis="of revenue", good="down"),
 dict(metric="waste_cost", label="Food waste", unit="$", value=r2(wst), before=round(wb / WEEKS_BEFORE), after=round(wa / WEEKS_AFTER), basis="per week", good="down"),
 dict(metric="waste_pct", label="Waste share of food cost", unit="%", value=r2(100 * wst / fcst), before=r2(100 * wb / fb), after=r2(100 * wa / fa), basis="of food cost", good="down"),
 dict(metric="median_ticket", label="Median ticket time", unit="min", value=r2(st.median(tix_b + tix_a)), before=r2(st.median(tix_b)), after=r2(st.median(tix_a)), basis="order fired to served", good="down"),
 dict(metric="peak_ticket", label="Fri/Sat 6–9pm ticket time (p90)", unit="min", value=r2(pct(peak_b + peak_a, .9)), before=r2(pct(peak_b, .9)), after=r2(pct(peak_a, .9)), basis="90th percentile", good="down"),
 dict(metric="avg_turn", label="Table turn time", unit="min", value=r2(st.mean(o["turn_min"] for o in orders)), before=r2(turn_b), after=r2(turn_a), basis="seated to paid", good="down"),
 dict(metric="rating", label="Average review rating", unit="★", value=r2(st.mean(rat_b + rat_a)), before=r2(st.mean(rat_b)), after=r2(st.mean(rat_a)), basis=f"{len(rat_b + rat_a)} reviews", good="up"),
 dict(metric="forecast_covers", label="Covers forecast, next 4 weeks", unit="", value=forecast_meta["forecast_4wk_covers"], before=forecast_meta["last_4wk_covers"], after=forecast_meta["forecast_4wk_covers"], basis="vs last 4 weeks", good="up"),
 dict(metric="forecast_mape", label="Forecast error (MAPE, 4 rolling 4-week holdouts)", unit="%", value=forecast_meta["mape_rolling"], before=None, after=None, basis="daily covers", good="down"),
]
for k in kpis:
    k["delta_pct"] = r2(100 * (k["after"] - k["before"]) / k["before"]) if k["before"] else None
    k["weeks_before"], k["weeks_after"] = WEEKS_BEFORE, WEEKS_AFTER
write("kpis", kpis)

# ------------------------------------------------------------------ menu engineering
sales = defaultdict(lambda: dict(before=[0, 0.0, 0.0], after=[0, 0.0, 0.0]))
for l in lines:
    p = period(dt.date.fromisoformat(l["date"])); s = sales[l["item_id"]][p]
    s[0] += 1; s[1] += float(l["price"]); s[2] += float(l["food_cost"])
# sections as they appear on the QR menu
GROUP = {"cobb": "Salads", "caesar": "Salads", "harvest": "Salads",
         **{k: "Burgers & Sandwiches" for k in ("smash", "bacon", "mush", "veggie", "nash", "dip_s", "pork", "blt")},
         **{k: "From the Grill" for k in ("ribeye", "strip", "ribs", "halfrib", "salmon")},
         **{k: "Comfort Plates" for k in ("cfs", "meatloaf", "potpie", "mac", "fish", "grits")}}
menu_rows = []
groups = defaultdict(list)
for iid, m in menu.items():
    b, a = sales[iid]["before"], sales[iid]["after"]
    if b[0] + a[0] == 0: continue                                         # never ordered in this run (removed from the menu)
    row = dict(item=iid, name=m["name"], category=m["category"], group=m.get("section") or GROUP.get(iid, "Mains" if m["category"] == "Entrees" else m["category"]), price=float(m["price"]), price_after=float(m["price_after"]),
               food_cost=float(m["food_cost"]), food_cost_after=float(m["food_cost_after"]), removed=int(m["removed_after"]),
               units=b[0] + a[0], revenue=r2(b[1] + a[1]), cm_total=r2(b[1] + a[1] - b[2] - a[2]),
               units_wk_before=r2(b[0] / WEEKS_BEFORE), units_wk_after=r2(a[0] / WEEKS_AFTER),
               cm_unit_before=r2((b[1] - b[2]) / b[0]) if b[0] else None, cm_unit_after=r2((a[1] - a[2]) / a[0]) if a[0] else None,
               cm_wk_before=r2((b[1] - b[2]) / WEEKS_BEFORE), cm_wk_after=r2((a[1] - a[2]) / WEEKS_AFTER),
               food_cost_pct=r2(100 * (b[2] + a[2]) / (b[1] + a[1])) if b[1] + a[1] else None)
    row["cm_unit"] = r2((b[1] + a[1] - b[2] - a[2]) / row["units"])
    menu_rows.append(row); groups[row["group"]].append(row)
def classify(rws, units_key, cm_key):
    n = len([r for r in rws if r[units_key] > 0]); tot = sum(r[units_key] for r in rws)
    avg_cm = sum(r[units_key] * (r[cm_key] or 0) for r in rws) / tot
    for r in rws:
        share = r[units_key] / tot if tot else 0
        pop = share >= 0.7 / n; prof = (r[cm_key] or 0) >= avg_cm
        r["pop_share_" + units_key[9:]] = r2(100 * share); r["avg_cm_" + units_key[9:]] = r2(avg_cm); r["pop_threshold_" + units_key[9:]] = r2(100 * 0.7 / n)
        r["class_" + units_key[9:]] = "Star" if pop and prof else "Plowhorse" if pop else "Puzzle" if prof else "Dog"
        if r[units_key] == 0: r["class_" + units_key[9:]] = "Removed"
for cat, rws in groups.items():
    classify(rws, "units_wk_before", "cm_unit_before"); classify(rws, "units_wk_after", "cm_unit_after")
menu_rows.sort(key=lambda r: -r["cm_total"])
write("menu", menu_rows)

# ------------------------------------------------------------------ hourly profile, staffing need vs schedule
def sched(periodname, wd, h, role):
    return sum(int(r["headcount"]) for r in rota if r["period"] == periodname and int(r["weekday"]) == wd and r["role"] == role and float(r["start_hour"]) <= h < float(r["end_hour"]))
hp = defaultdict(lambda: dict(parties=0, covers=0, plates=0, tix_b=[], tix_a=[], days=set()))
hot_by_order = Counter(l["order_id"] for l in lines if menu[l["item_id"]]["category"] in ("Starters", "Entrees"))
for o in orders:
    k = (o["date_d"].weekday(), o["hour"]); x = hp[k]
    if o["date_d"] >= CHANGE:
        x["parties"] += 1; x["covers"] += o["party_size"]; x["plates"] += hot_by_order[o["order_id"]]; x["days"].add(o["date"]); x["tix_a"].append(o["ticket_min"])
    else: x["tix_b"].append(o["ticket_min"])
hourly = []
for wd in range(7):
    for h in range(9, 24):
        x = hp.get((wd, h))
        if not x or not x["days"]: continue
        nd = len(x["days"]); parties = x["parties"] / nd; plates = x["plates"] / nd
        concurrent = min(22, parties * 1.15)                # average turn is a little over an hour; 22 tables is the ceiling (a wait list forms)
        srv_need = max(1, math.ceil(concurrent / 4.5)); cook_need = max(1, math.ceil(plates / 16))
        hourly.append(dict(weekday=WD[wd], wd=wd, hour=h, label=f"{h}:00", parties=r2(parties), covers=r2(x["covers"] / nd), plates=r2(plates),
            median_ticket_before=r2(st.median(x["tix_b"])) if x["tix_b"] else None, median_ticket_after=r2(st.median(x["tix_a"])),
            servers_needed=srv_need, servers_scheduled=sched("after", wd, h, "Server"), cooks_needed=cook_need, cooks_scheduled=sched("after", wd, h, "Line cook")))
        hourly[-1]["server_gap"] = hourly[-1]["servers_scheduled"] - srv_need; hourly[-1]["cook_gap"] = hourly[-1]["cooks_scheduled"] - cook_need
write("hourly", hourly)

# proposed rota: lunch/dinner headcount from the hour of highest need per block, with a one-hour setup/close pad
rota_rows = []
for r in rota:
    if r["period"] != "after": continue
    hrs = float(r["end_hour"]) - float(r["start_hour"])
    rota_rows.append(dict(plan="current", shift="lunch" if float(r["start_hour"]) < 15 else "dinner", weekday=WD[int(r["weekday"])], wd=int(r["weekday"]), role=r["role"], start=float(r["start_hour"]), end=float(r["end_hour"]),
                          headcount=int(r["headcount"]), hours=r2(hrs * int(r["headcount"])), cost=r2(hrs * int(r["headcount"]) * float(r["hourly_rate"]))))
by_wd = defaultdict(list)
for h in hourly: by_wd[h["wd"]].append(h)
for wd in range(7):
    hs = by_wd[wd]; close = 23 if wd in (4, 5) else 21 if wd == 6 else 22; open_ = 10 if wd == 6 else 11
    lunch = [h for h in hs if h["hour"] < 16]; dinner = [h for h in hs if h["hour"] >= 16]
    for role, key, rate in (("Server", "servers_needed", rate_by_role["Server"]), ("Line cook", "cooks_needed", rate_by_role["Line cook"])):
        floor = 2
        l_start, l_end = (open_ - 0.5 if role == "Server" else open_ - 2), 15.5
        d_start, d_end = 15.5, (close + 0.5 if role == "Server" else close + 1)
        for blk, s, e, kind in ((lunch, l_start, l_end, "lunch"), (dinner, d_start, d_end, "dinner")):
            core = [h for h in blk if h["parties"] >= 3]                       # hours with real trade decide the base; the shoulders ride along
            base = max(floor, min(h[key] for h in core)); peak = max(h[key] for h in core)
            rota_rows.append(dict(plan="proposed", shift=kind, weekday=WD[wd], wd=wd, role=role, start=s, end=e, headcount=base, hours=r2((e - s) * base), cost=r2((e - s) * base * rate)))
            if peak > base:
                busy = [h["hour"] for h in core if h[key] > base]
                ps, pe = min(busy) - 0.5, max(busy) + 1
                if pe - ps < 4: pe = ps + 4                                     # nobody is called in for less than four hours
                rota_rows.append(dict(plan="proposed", shift=kind + " peak", weekday=WD[wd], wd=wd, role=role, start=ps, end=pe, headcount=peak - base, hours=r2((pe - ps) * (peak - base)), cost=r2((pe - ps) * (peak - base) * rate)))
    # the shifts that this rota only needs if the volume is there: a mid-afternoon bridge is a split, so we don't add one
    for r in rota_rows[:]:
        if r["plan"] == "current" and r["wd"] == wd and r["role"] not in ("Server", "Line cook"):
            rota_rows.append(dict(r, plan="proposed"))
for r in rota_rows: r["key"] = f"{r['plan']}|{r['wd']}|{r['role']}|{r['start']}|{r['end']}"
write("rota", rota_rows)
weekly_sales_after = ra / WEEKS_AFTER
labour = []
for plan in ("current", "proposed"):
    for wd in range(7):
        rs = [r for r in rota_rows if r["plan"] == plan and r["wd"] == wd]
        day_sales = sum(r["revenue"] for r in aft if r["weekday"] == WD[wd]) / len([r for r in aft if r["weekday"] == WD[wd]])
        labour.append(dict(plan=plan, weekday=WD[wd], wd=wd, hours=r2(sum(r["hours"] for r in rs)), cost=r2(sum(r["cost"] for r in rs)), sales=r2(day_sales),
                           labour_pct=r2(100 * sum(r["cost"] for r in rs) / day_sales)))
labour.append(dict(plan="before", weekday="Week", wd=7, hours=r2(sum((float(r["end_hour"]) - float(r["start_hour"])) * int(r["headcount"]) for r in rota if r["period"] == "before")),
                   cost=r2(lab_b), sales=r2(rb / WEEKS_BEFORE), labour_pct=r2(100 * lab_b / (rb / WEEKS_BEFORE))))
for plan in ("current", "proposed"):
    rs = [l for l in labour if l["plan"] == plan]
    labour.append(dict(plan=plan, weekday="Week", wd=7, hours=r2(sum(l["hours"] for l in rs)), cost=r2(sum(l["cost"] for l in rs)), sales=r2(sum(l["sales"] for l in rs)),
                       labour_pct=r2(100 * sum(l["cost"] for l in rs) / sum(l["sales"] for l in rs))))
for l in labour: l["key"] = f"{l['plan']}|{l['weekday']}"
write("labour", labour)

# ------------------------------------------------------------------ item-level forecast and par levels for the next 4 weeks
units_wd = defaultdict(list)
for l in lines:
    d = dt.date.fromisoformat(l["date"])
    if d >= END - dt.timedelta(weeks=8) + dt.timedelta(days=1): units_wd[(l["item_id"], d.weekday())].append(d)
cnt = Counter((iid, d.weekday()) for (iid, wdd), ds in units_wd.items() for d in ds)
growth = forecast_meta["forecast_4wk_covers"] / forecast_meta["last_4wk_covers"]
par_rows = []
for iid, m in menu.items():
    if m["category"] not in ("Starters", "Entrees") or int(m["removed_after"]): continue
    per_wd = {WD[wd]: cnt[(iid, wd)] / 8 for wd in range(7)}
    wk = sum(per_wd.values())
    row = dict(item=iid, name=m["name"], category=m["category"], avg_weekly_units_8wk=r2(wk), forecast_weekly_units=round(wk * growth))
    for wd in range(7): row["par_" + WD[wd].lower()] = math.ceil(per_wd[WD[wd]] * growth * 1.12 + 0.5)
    par_rows.append(row)
par_rows.sort(key=lambda r: -r["forecast_weekly_units"])
write("item_forecast", par_rows)

# ------------------------------------------------------------------ suppliers
q_rows = []
for q in quotes:
    q_rows.append(dict(week_start=q["week_start"], ingredient=q["ingredient_id"], name=q["ingredient"], unit=q["unit"], supplier=q["supplier"],
                       is_current=int(q["is_current"]), price=float(q["price"]), units=float(q["units_used"]), spend=r2(float(q["price"]) * float(q["units_used"]))))
for q in q_rows: q["key"] = f"{q['week_start']}|{q['ingredient']}|{q['supplier']}"
write("supplier_quotes", q_rows)
summ = []
for ing in sorted({q["ingredient"] for q in q_rows}, key=lambda i: -sum(q["spend"] for q in q_rows if q["ingredient"] == i and q["is_current"])):
    cur = [q for q in q_rows if q["ingredient"] == ing and q["is_current"]]; alt = [q for q in q_rows if q["ingredient"] == ing and not q["is_current"]]
    cur.sort(key=lambda q: q["week_start"]); alt.sort(key=lambda q: q["week_start"])
    cur_spend = sum(q["spend"] for q in cur); alt_spend = sum(q["spend"] for q in alt)
    if cur_spend == 0: continue                                       # this menu never uses the ingredient
    best_spend = sum(min(c["spend"], a["spend"]) for c, a in zip(cur, alt))
    first = st.mean(q["price"] for q in cur[:4]); last = st.mean(q["price"] for q in cur[-4:])
    summ.append(dict(ingredient=ing, name=cur[0]["name"], unit=cur[0]["unit"], units_year=r2(sum(q["units"] for q in cur)),
        current_supplier=cur[0]["supplier"], current_spend=r2(cur_spend), alt_supplier=alt[0]["supplier"], alt_spend=r2(alt_spend),
        saving_switch=r2(cur_spend - alt_spend), saving_switch_pct=r2(100 * (cur_spend - alt_spend) / cur_spend),
        saving_best_weekly=r2(cur_spend - best_spend), price_first4=r2(first), price_last4=r2(last), price_change_pct=r2(100 * (last - first) / first),
        recommendation=("Switch" if cur_spend - alt_spend > 0.04 * cur_spend else "Negotiate" if cur_spend - alt_spend > 0 else "Keep")))
write("supplier_summary", summ)

# ------------------------------------------------------------------ reviews
months = defaultdict(list)
for r in reviews: months[r["date"][:7]].append(r)
rev_month = []
for mth in sorted(months):
    rs = months[mth]; ratings = [int(r["rating"]) for r in rs]
    rev_month.append(dict(month=mth, month_start=mth + "-01", n=len(rs), avg_rating=r2(st.mean(ratings)), pct_5=r2(100 * sum(1 for x in ratings if x == 5) / len(rs)),
        pct_1_2=r2(100 * sum(1 for x in ratings if x <= 2) / len(rs)), wait_neg_pct=r2(100 * sum(1 for r in rs if "wait:-" in r["themes"]) / len(rs)),
        period="after" if dt.date.fromisoformat(mth + "-01") >= CHANGE.replace(day=1) else "before"))
write("reviews_monthly", rev_month)
theme_rows = []
for th in ["food", "wait", "service", "value", "qr menu", "portions", "atmosphere"]:
    for p in ("before", "after"):
        rs = [r for r in reviews if period(dt.date.fromisoformat(r["date"])) == p]
        posn = sum(1 for r in rs if f"{th}:+" in r["themes"]); negn = sum(1 for r in rs if f"{th}:-" in r["themes"])
        theme_rows.append(dict(theme=th.capitalize() if th != "qr menu" else "QR menu", key=th, period=p, reviews=len(rs), mentions=posn + negn, positive=posn, negative=negn,
            mention_pct=r2(100 * (posn + negn) / len(rs)), negative_pct=r2(100 * negn / len(rs)), net=r2(100 * (posn - negn) / (posn + negn)) if posn + negn else 0))
for t in theme_rows: t["id"] = f"{t['key']}|{t['period']}"
write("review_themes", theme_rows)
# ticket time vs rating: bucket the reviewer's own ticket time
buckets = [(0, 15, "under 15"), (15, 20, "15–20"), (20, 25, "20–25"), (25, 30, "25–30"), (30, 40, "30–40"), (40, 999, "over 40")]
tr = []
for lo, hi, lab in buckets:
    rs = [r for r in reviews if lo <= float(r["ticket_min"]) < hi]
    if rs: tr.append(dict(bucket=lab, lo=lo, n=len(rs), avg_rating=r2(st.mean(int(r["rating"]) for r in rs)), pct_1_2=r2(100 * sum(1 for r in rs if int(r["rating"]) <= 2) / len(rs))))
write("ticket_vs_rating", tr)
quotes_out = []
import random; random.seed(7)
for p in ("before", "after"):
    pool = [r for r in reviews if period(dt.date.fromisoformat(r["date"])) == p and len(r["text"]) > 40]
    for r in random.sample(pool, 5):
        quotes_out.append(dict(id=int(r["review_id"]), date=r["date"], period=p, platform=r["platform"], rating=int(r["rating"]), text=r["text"], themes=r["themes"].replace(";", ", ")))
write("review_quotes", quotes_out)

# ------------------------------------------------------------------ waste
wk_waste = defaultdict(lambda: dict(over=0.0, plate=0.0, over_units=0, plate_units=0))
item_waste = defaultdict(lambda: dict(before=[0, 0.0], after=[0, 0.0]))
for w in waste:
    d = dt.date.fromisoformat(w["date"]); wk = (d - START).days // 7; x = wk_waste[wk]
    if w["kind"] == "over-prep": x["over"] += float(w["waste_cost"]); x["over_units"] += int(w["wasted_units"])
    else: x["plate"] += float(w["waste_cost"]); x["plate_units"] += int(w["wasted_units"])
    iw = item_waste[(w["item_id"], w["kind"])][period(d)]; iw[0] += int(w["wasted_units"]); iw[1] += float(w["waste_cost"])
waste_weekly = [dict(week_start=(START + dt.timedelta(weeks=wk)).isoformat(), week=wk + 1, period=period(START + dt.timedelta(weeks=wk)),
                     over_prep_cost=r2(x["over"]), plate_cost=r2(x["plate"]), total_cost=r2(x["over"] + x["plate"]), over_prep_units=x["over_units"], plate_units=x["plate_units"])
                for wk, x in sorted(wk_waste.items())]
write("waste_weekly", waste_weekly)
waste_items = []
for (iid, kind), v in item_waste.items():
    waste_items.append(dict(item=iid, name=menu[iid]["name"], kind=kind, key=f"{iid}|{kind}", cost_wk_before=r2(v["before"][1] / WEEKS_BEFORE), cost_wk_after=r2(v["after"][1] / WEEKS_AFTER),
        units_wk_before=r2(v["before"][0] / WEEKS_BEFORE), units_wk_after=r2(v["after"][0] / WEEKS_AFTER), cost_year=r2(v["before"][1] + v["after"][1]),
        change_pct=r2(100 * (v["after"][1] / WEEKS_AFTER - v["before"][1] / WEEKS_BEFORE) / (v["before"][1] / WEEKS_BEFORE)) if v["before"][1] else None))
waste_items.sort(key=lambda r: -r["cost_year"])
write("waste_items", waste_items)

# ------------------------------------------------------------------ the change log and what moved
def item_row(iid): return next((r for r in menu_rows if r["item"] == iid), {"units_wk_before": 0, "units_wk_after": 0, "cm_unit_before": 0, "cm_unit_after": 0, "cm_wk_before": 0, "cm_wk_after": 0})
def wi(iid, kind): return next((r for r in waste_items if r["item"] == iid and r["kind"] == kind), None)
actions = [
 dict(n=1, area="Menu", date=CHANGE.isoformat(), action="Ribeye moved to the top of the QR entrée screen with a photo", why="Puzzle: highest margin on the menu but 1.6% of entrée orders",
      metric="Ribeye orders per week", before=item_row("ribeye")["units_wk_before"], after=item_row("ribeye")["units_wk_after"], unit="/wk", good="up"),
 dict(n=2, area="Menu", date=CHANGE.isoformat(), action="Shrimp & Grits removed", why="Dog: low popularity, below-average margin, longest ticket time on the sauté station",
      metric="Weekly margin, Shrimp & Grits + Ribeye together", before=r2(item_row("grits")["cm_wk_before"] + item_row("ribeye")["cm_wk_before"]), after=r2(item_row("grits")["cm_wk_after"] + item_row("ribeye")["cm_wk_after"]), unit="$/wk", good="up"),
 dict(n=3, area="Menu", date=CHANGE.isoformat(), action="Crab Cakes repriced $16 → $17.50 and moved to Bayline Seafood for crab", why="Dog at 50% food cost; the review data said 'filler', so portion stayed and crab quality went up",
      metric="Crab Cakes contribution margin per plate", before=item_row("crab")["cm_unit_before"], after=item_row("crab")["cm_unit_after"], unit="$", good="up"),
 dict(n=4, area="Menu", date=CHANGE.isoformat(), action="Mac & Cheese $13.50 → $14.50", why="Plowhorse: top-5 seller at the lowest entrée margin; demand curve from the QR data showed little price sensitivity",
      metric="Mac & Cheese margin per week", before=item_row("mac")["cm_wk_before"], after=item_row("mac")["cm_wk_after"], unit="$/wk", good="up"),
 dict(n=5, area="Waste", date=CHANGE.isoformat(), action="Prep par sheet replaced with forecast-driven pars (last 4 same weekdays × 1.12)", why="Fixed pars over-prepped by ~38% on quiet days",
      metric="Over-prep waste per week", before=r2(sum(x["over"] for wk, x in wk_waste.items() if wk < WEEKS_BEFORE) / WEEKS_BEFORE), after=r2(sum(x["over"] for wk, x in wk_waste.items() if wk >= WEEKS_BEFORE) / WEEKS_AFTER), unit="$/wk", good="down"),
 dict(n=6, area="Waste", date=CHANGE.isoformat(), action="Coleslaw portion cut and served on request; Chicken Fried Steak portion trimmed", why="Bus-station data: 38% of coleslaw cups came back untouched",
      metric="Plate waste per 1,000 covers", before=r2(1000 * sum(x["plate"] for wk, x in wk_waste.items() if wk < WEEKS_BEFORE) / cb), after=r2(1000 * sum(x["plate"] for wk, x in wk_waste.items() if wk >= WEEKS_BEFORE) / ca), unit="$", good="down"),
 dict(n=7, area="Rota", date=CHANGE.isoformat(), action="Fourth line cook on Fri/Sat 15:00–close; one Monday lunch server moved to Friday dinner", why="Ticket times broke 30 min whenever Fri/Sat dinner passed 48 plates an hour; Monday lunch ran 1 server over need",
      metric="Fri/Sat 6–9pm p90 ticket time", before=r2(pct(peak_b, .9)), after=r2(pct(peak_a, .9)), unit="min", good="down"),
 dict(n=8, area="Reviews", date=CHANGE.isoformat(), action="QR menu: photos added, pay-at-table promoted on the landing screen", why="'QR menu' was the 3rd most-mentioned theme and 28% of mentions were negative",
      metric="Reviews with a QR-menu complaint", before=next(t["negative_pct"] for t in theme_rows if t["key"] == "qr menu" and t["period"] == "before"), after=next(t["negative_pct"] for t in theme_rows if t["key"] == "qr menu" and t["period"] == "after"), unit="%", good="down"),
]
for a_ in actions: a_["delta_pct"] = r2(100 * (a_["after"] - a_["before"]) / a_["before"]) if a_["before"] else None
write("actions", actions)
write("forecast_meta", [forecast_meta])
print(json.dumps(forecast_meta))
print("KPIs:"); [print(f"  {k['label']:40s} {k['value']!s:>12} before {k['before']!s:>9} after {k['after']!s:>9}  ({k['delta_pct']}%)") for k in kpis]
print("Menu classes (before):", Counter(r["class_before"] for r in menu_rows))
for r in menu_rows:
    if r["category"] in ("Entrees", "Starters"): print(f"  {r['group'][:8]:8s} {r['name']:28s} {r['class_before']:10s}->{r['class_after']:10s} units/wk {r['units_wk_before']:6.1f}->{r['units_wk_after']:6.1f} cm/unit {r['cm_unit_before']} share {r['pop_share_before']}% thr {r['pop_threshold_before']}% avgcm {r['avg_cm_before']}")
print("Suppliers:"); [print(f"  {s['name']:26s} {s['current_supplier']:20s} ${s['current_spend']:>10,.0f} alt ${s['alt_spend']:>10,.0f} save {s['saving_switch_pct']:>6}%  drift {s['price_change_pct']}%  {s['recommendation']}") for s in summ]
print("Labour:"); [print(f"  {l['plan']:9s} {l['weekday']:5s} hrs {l['hours']:7} cost {l['cost']:9} sales {l['sales']:9} {l['labour_pct']}%") for l in labour]
print("Proposed rota Fri:"); [print(f"  {r['role']:10s} {r['shift']:12s} {r['start']:5}-{r['end']:5} x{r['headcount']}") for r in rota_rows if r['plan']=='proposed' and r['wd']==4]
print("Hourly Fri/Sat need:"); [print(f"  {h['weekday']} {h['hour']:2d}h parties {h['parties']:5} plates {h['plates']:5} srv {h['servers_scheduled']}/{h['servers_needed']} cook {h['cooks_scheduled']}/{h['cooks_needed']} tix {h['median_ticket_before']}->{h['median_ticket_after']}") for h in hourly if h['wd'] in (0,4,5)]
print("Themes:"); [print(f"  {t['theme']:11s} {t['period']:6s} mentions {t['mentions']:4d} neg% {t['negative_pct']:5} net {t['net']}") for t in theme_rows]
print("Ticket vs rating:", [(t['bucket'], t['n'], t['avg_rating']) for t in tr])
print("Actions:"); [print(f"  {a_['action'][:60]:60s} {a_['before']} -> {a_['after']} ({a_['delta_pct']}%)") for a_ in actions]

# ------------------------------------------------------------------ effect of the changes, and the projected second round
# Same simulator, same seed and demand: "nochange" never makes the 6 July changes, "plan" adds the recommended fixes.
# Comparing like-for-like weeks removes the season from the before/after reading.
def scenario(dirname, labour_wk):
    base = os.path.join(HERE, dirname)
    def rd(n):
        with open(os.path.join(base, n + ".csv"), newline="") as f: return list(csv.DictReader(f))
    os_ = [o for o in rd("orders") if o["date"] >= CHANGE.isoformat()]
    ws = [w for w in rd("waste") if w["date"] >= CHANGE.isoformat()]
    rv = [r for r in rd("reviews") if r["date"] >= CHANGE.isoformat()]
    for r in rv: r["themes"] = tag_text(r["text"])
    rev = sum(float(o["revenue"]) for o in os_); food = sum(float(o["food_cost"]) for o in os_); wst = sum(float(w["waste_cost"]) for w in ws)
    def tix(f): return [float(o["ticket_min"]) for o in os_ if f(dt.date.fromisoformat(o["date"]).weekday(), int(o["hour"]))]
    dinner = tix(lambda wd, h: wd in (4, 5) and 18 <= h <= 20); lunch = tix(lambda wd, h: wd in (4, 5) and 11 <= h <= 13)
    pn = sum(1 for r in rv if "portions:+" in r["themes"]); nn = sum(1 for r in rv if "portions:-" in r["themes"])
    wk = WEEKS_AFTER
    return dict(revenue=rev / wk, covers=sum(int(o["party_size"]) for o in os_) / wk, food_pct=100 * food / rev, waste=wst / wk, labour_pct=100 * labour_wk / (rev / wk),
                profit=(rev - food - wst) / wk - labour_wk, dinner_p90=pct(dinner, .9), lunch_med=st.median(lunch), lunch_p90=pct(lunch, .9),
                rating=st.mean(int(r["rating"]) for r in rv), slow_share=100 * sum(1 for o in os_ if float(o["ticket_min"]) > 30) / len(os_),
                portions_net=100 * (pn - nn) / (pn + nn) if pn + nn else 0)
lab = {l["plan"]: l["cost"] for l in labour if l["weekday"] == "Week"}
S = {"actual": scenario("data", lab["current"]), "nochange": scenario("data_nochange", lab["before"]), "plan": scenario("data_plan", lab["proposed"])}
EFFECT_ROWS = [("revenue", "Revenue per week", "$", "up"), ("covers", "Covers per week", "", "up"), ("profit", "Profit after food, waste and labour, per week", "$", "up"),
               ("food_pct", "Food cost", "%", "down"), ("labour_pct", "Labour cost", "%", "down"), ("waste", "Food waste per week", "$", "down"),
               ("dinner_p90", "Fri/Sat 6–9pm ticket time, p90", "min", "down"), ("lunch_med", "Fri/Sat 11am–2pm ticket time, median", "min", "down"),
               ("lunch_p90", "Fri/Sat 11am–2pm ticket time, p90", "min", "down"), ("slow_share", "Tickets over 30 minutes", "%", "down"),
               ("rating", "Average review rating", "★", "up"), ("portions_net", "Portion-size sentiment, net", "", "up")]
effects = []
for k, label, unit, good in EFFECT_ROWS:
    a, n, p = S["actual"][k], S["nochange"][k], S["plan"][k]
    effects.append(dict(metric=k, label=label, unit=unit, good=good, nochange=r2(n), actual=r2(a), plan=r2(p),
                        effect_pct=r2(100 * (a - n) / abs(n)) if n else None, plan_pct=r2(100 * (p - a) / abs(a)) if a else None))
write("effects", effects)
plan_items = [
 dict(n=1, area="Rota", change="Adopt the proposed rota: two extra line cooks 11:30–15:30 on Fri and Sat, base shifts sized to quiet-hour need", metric="lunch_p90"),
 dict(n=2, area="Suppliers", change="Move ribs, beef, steak, salmon, buns, shrimp, potatoes and fry oil to the cheaper quote", metric="food_pct"),
 dict(n=3, area="Menu", change="French Dip and Turkey BLT +$1; Harvest Salad removed", metric="profit"),
 dict(n=4, area="Menu", change="Cobb and Caesar +$0.50 to absorb greens inflation", metric="food_pct"),
 dict(n=5, area="Menu", change="Chicken Fried Steak back to its full portion", metric="portions_net", note="Each run has only a few dozen portion mentions after July, so this figure moves with chance more than with the fix."),
]
eff = {e["metric"]: e for e in effects}
for p_ in plan_items: p_.setdefault("note", ""); p_.update(label=eff[p_["metric"]]["label"], unit=eff[p_["metric"]]["unit"], actual=eff[p_["metric"]]["actual"], plan=eff[p_["metric"]]["plan"], good=eff[p_["metric"]]["good"])
write("plan", plan_items)
print("Effects (nochange -> actual -> plan):"); [print(f"  {e['label']:46s} {e['nochange']!s:>10} {e['actual']!s:>10} {e['plan']!s:>10}  effect {e['effect_pct']}%  plan {e['plan_pct']}%") for e in effects]
k_extra = [dict(metric="tagger_agreement", label="Review tagger agreement", unit="%", value=TAGGER_AGREEMENT, before=None, after=None, basis="vs simulator tags", good="up", delta_pct=None, weeks_before=WEEKS_BEFORE, weeks_after=WEEKS_AFTER),
           dict(metric="forecast_model", label="Forecast model", unit="", value=forecast_meta["model"], before=None, after=None, basis="best of 3 on rolling holdouts", good="up", delta_pct=None, weeks_before=WEEKS_BEFORE, weeks_after=WEEKS_AFTER)]
write("kpis", kpis + k_extra)

# ------------------------------------------------------------------ orders from the live QR menu (data/qr_order_lines.csv, written by sync_qr_orders.py)
qr_path = os.path.join(DATA, "qr_order_lines.csv")
qr_lines = []
if os.path.exists(qr_path):
    with open(qr_path, newline="") as f: qr_lines = list(csv.DictReader(f))
by_item = defaultdict(lambda: [0, 0.0])
for l in qr_lines:
    by_item[l["item_id"]][0] += int(l["qty"]); by_item[l["item_id"]][1] += int(l["qty"]) * float(l["price"])
write("qr_live_items", sorted([dict(item=k, name=menu[k]["name"] if k in menu else k, qty=v[0], revenue=r2(v[1])) for k, v in by_item.items()], key=lambda r: -r["qty"]))
write("qr_live_summary", [dict(id="all", rounds=len({l["round_id"] for l in qr_lines}), items=sum(int(l["qty"]) for l in qr_lines),
      revenue=r2(sum(int(l["qty"]) * float(l["price"]) for l in qr_lines)), tables=len({l["table"] for l in qr_lines}),
      last_order=max((l["date"] for l in qr_lines), default=""), synced=dt.datetime.now().strftime("%Y-%m-%d %H:%M"))])
