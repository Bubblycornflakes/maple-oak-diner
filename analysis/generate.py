#!/usr/bin/env python3
"""Simulate one year of operations for Maple & Oak, an 84-seat sit-down American grill.

The QR-code menu system logs every party: seating time, when the order was placed from the
phone, when the kitchen fired it, when it was served, and when the table paid. Separate logs
hold prep/plate waste (measured at the bus station), the staff rota, weekly supplier quotes
and online reviews. Everything is written as CSV into ./data for analyze.py.

Pure standard library: no numpy or pandas needed.

Usage, from the repository root:

    python3 analysis/generate.py actual      # the year with the 6 July changes  -> analysis/data/
    python3 analysis/generate.py nochange    # same weeks, no changes            -> analysis/data_nochange/
    python3 analysis/generate.py plan        # changes plus the next round       -> analysis/data_plan/

A restaurant.json at the repository root replaces the sample's size and menu (see README.md).
"""
import csv, json, math, os, random, sys, datetime as dt
from collections import defaultdict

random.seed(20261101)
HERE = os.path.dirname(os.path.abspath(__file__))

TODAY = dt.date(2026, 11, 1)
END = TODAY - dt.timedelta(days=((TODAY.weekday() + 1) % 7) or 7)   # last full Sunday: 2026-10-25
START = END - dt.timedelta(days=363)                                  # Monday 2025-10-27
CHANGE = START + dt.timedelta(weeks=36)                               # Monday 2026-07-06: menu, par and rota changes go live
REAL_CHANGE = CHANGE

# Three runs share demand, seed and calendar:
#   actual   - what happened: the 6 July changes go live
#   nochange - counterfactual: the same year with no changes, so the effect of the changes can be read without the season mixed in
#   plan     - projection: the 6 July changes plus the second round of fixes the analysis recommends (see PLAN_* below)
MODE = sys.argv[1] if len(sys.argv) > 1 else "actual"
assert MODE in ("actual", "nochange", "plan"), "mode must be actual, nochange or plan"
DATA = os.path.join(HERE, {"actual": "data", "nochange": "data_nochange", "plan": "data_plan"}[MODE])
os.makedirs(DATA, exist_ok=True)
if MODE == "nochange": CHANGE = dt.date(2100, 1, 1)
PLAN = MODE == "plan"
SEATS, TABLES = 84, 22

# ---------------------------------------------------------------- menu
# id, name, category, station, price, food_cost, prep_min, base weight, lunch mult, season tag, post-change overrides
MENU = [
 # starters
 ("wings",   "Buffalo Wings",            "Starters", "fry",   13.50, 4.20, 14, 10, 1.0, None, {}),
 ("skins",   "Loaded Potato Skins",      "Starters", "fry",   11.00, 2.60, 12,  7, 1.0, None, {}),
 ("pickles", "Fried Pickles",            "Starters", "fry",    9.00, 1.80, 10,  5, 1.0, None, {}),
 ("dip",     "Spinach Artichoke Dip",    "Starters", "saute", 12.00, 3.10, 11,  6, 1.0, None, {}),
 ("shrimp",  "Shrimp Cocktail",          "Starters", "cold",  15.00, 6.90,  6,  2, 1.0, None, {}),
 ("crab",    "Crab Cakes",               "Starters", "saute", 16.00, 8.10, 13,  3, 1.0, None, {"price": 17.50, "food_cost": 6.40, "w": 3.3}),
 # entrees
 ("cobb",    "Cobb Salad",               "Entrees",  "cold",  15.50, 5.00,  8,  6, 1.6, "summer", {}),
 ("caesar",  "Chicken Caesar Salad",     "Entrees",  "cold",  13.50, 3.60,  8,  6, 1.6, "summer", {}),
 ("harvest", "Harvest Salad",            "Entrees",  "cold",  14.00, 4.40,  8,  2, 1.5, "winter", {}),
 ("smash",   "Classic Smash Burger",     "Entrees",  "grill", 15.00, 4.30, 13, 16, 1.2, None, {}),
 ("bacon",   "Bacon Cheddar Burger",     "Entrees",  "grill", 17.00, 5.40, 14, 12, 1.1, None, {}),
 ("mush",    "Mushroom Swiss Burger",    "Entrees",  "grill", 16.50, 5.10, 14,  6, 1.1, None, {}),
 ("veggie",  "Veggie Burger",            "Entrees",  "grill", 14.50, 4.90, 13,  2, 1.2, None, {}),
 ("nash",    "Nashville Hot Chicken",    "Entrees",  "fry",   15.50, 4.00, 15, 11, 1.1, None, {}),
 ("dip_s",   "French Dip",               "Entrees",  "grill", 16.00, 6.20, 12,  5, 1.3, None, {}),
 ("pork",    "Pulled Pork Sandwich",     "Entrees",  "grill", 14.50, 3.90, 10,  6, 1.3, None, {}),
 ("blt",     "Turkey BLT",               "Entrees",  "cold",  12.00, 2.90,  9,  4, 1.8, None, {}),
 ("ribeye",  "12 oz Ribeye",             "Entrees",  "grill", 34.00, 15.80, 22, 2.2, 0.3, None, {"w": 3.8}),
 ("strip",   "NY Strip",                 "Entrees",  "grill", 29.00, 12.40, 20,  3, 0.3, None, {}),
 ("ribs",    "Baby Back Ribs, full rack",    "Entrees",  "grill", 27.00, 11.20, 16,  7, 0.5, None, {}),
 ("halfrib", "Baby Back Ribs, half rack",    "Entrees",  "grill", 19.00, 7.30, 15,  6, 0.7, None, {}),
 ("cfs",     "Chicken Fried Steak",      "Entrees",  "fry",   18.00, 5.60, 16,  6, 0.8, "winter", {}),
 ("salmon",  "Grilled Salmon",           "Entrees",  "saute", 24.00, 9.80, 17,  5, 0.6, None, {}),
 ("fish",    "Fish & Chips",             "Entrees",  "fry",   17.00, 5.40, 14,  7, 1.0, None, {}),
 ("meatloaf","Mom's Meatloaf",           "Entrees",  "saute", 16.50, 4.10, 12,  7, 0.8, "winter", {}),
 ("mac",     "Baked Mac & Cheese",       "Entrees",  "saute", 13.50, 2.90, 12,  6, 1.0, None, {"price": 14.50}),
 ("potpie",  "Chicken Pot Pie",          "Entrees",  "saute", 16.00, 4.40, 15,  4, 0.8, "winter", {}),
 ("grits",   "Shrimp & Grits",           "Entrees",  "saute", 21.00, 8.90, 18, 1.4, 0.6, None, {"w": 0}),
 # sides
 ("fries",   "Fries",                    "Sides",    "fry",    5.00, 0.80,  6, 14, 1.0, None, {}),
 ("swfries", "Sweet Potato Fries",       "Sides",    "fry",    6.00, 1.10,  6,  6, 1.0, None, {}),
 ("rings",   "Onion Rings",              "Sides",    "fry",    6.50, 1.30,  7,  5, 1.0, None, {}),
 ("macside", "Mac & Cheese",      "Sides",    "saute",  6.00, 1.40,  6,  4, 1.0, None, {}),
 ("slaw",    "Coleslaw",                 "Sides",    "cold",   4.00, 0.50,  2,  5, 1.0, None, {"food_cost": 0.40}),
 ("sidesal", "Side Salad",               "Sides",    "cold",   5.00, 1.20,  3,  4, 1.3, None, {}),
 # desserts
 ("pie",     "Apple Pie à la Mode",      "Desserts", "pastry", 8.00, 1.90,  6,  6, 1.0, "winter", {}),
 ("brownie", "Brownie Sundae",           "Desserts", "pastry", 8.50, 2.10,  7,  7, 1.0, None, {}),
 ("cheese",  "New York Cheesecake",            "Desserts", "pastry", 9.00, 2.60,  4,  5, 1.0, None, {}),
 ("keylime", "Key Lime Pie",             "Desserts", "pastry", 8.00, 2.40,  4,  2, 1.0, "summer", {}),
 # drinks
 ("soda",    "Soft Drink",               "Drinks",   "bar",    3.25, 0.40,  1, 20, 1.0, None, {}),
 ("tea",     "Iced Tea",                 "Drinks",   "bar",    3.00, 0.30,  1, 10, 1.0, "summer", {}),
 ("lemon",   "Lemonade",                 "Drinks",   "bar",    3.50, 0.45,  1,  6, 1.0, "summer", {}),
 ("coffee",  "Coffee",                   "Drinks",   "bar",    3.00, 0.25,  1,  5, 1.4, "winter", {}),
 ("shake",   "Milkshake",                "Drinks",   "bar",    7.00, 1.60,  4,  6, 1.0, "summer", {}),
 ("beer",    "Draft Beer",               "Drinks",   "bar",    6.50, 1.40,  1,  9, 0.4, None, {}),
 ("wine",    "House Wine",               "Drinks",   "bar",    9.00, 2.30,  1,  6, 0.4, None, {}),
]
ITEMS = {}
for r in MENU:
    iid, name, cat, station, price, cost, prep, w, lunch, season, post = r
    ITEMS[iid] = dict(id=iid, name=name, category=cat, station=station, price=price, food_cost=cost,
                      prep_min=prep, w=w, lunch=lunch, season=season, post=post)

# ---- your restaurant: restaurant.json (exported from the QR menu's owner console) replaces the sample's size and menu.
# Dishes the sample knows keep their cost, prep time and popularity; new dishes get the defaults below unless the file
# gives "cost" (ingredient cost per plate), "prepMin" or "popularity" (relative weight, sample dishes run 1 to 20).
# Dishes missing from the file are never ordered. The second-round plan in PLAN_* only touches dishes that still exist.
def category_for(section):
    s = section.lower()
    for words, cat in ((("start", "appet", "share", "snack", "small"), "Starters"), (("side",), "Sides"), (("dessert", "sweet", "pie"), "Desserts"),
                       (("drink", "beverage", "bar", "wine", "beer", "cocktail", "coffee"), "Drinks")):
        if any(w in s for w in words): return cat
    return "Entrees"
CONFIG_PATH = os.path.join(HERE, "..", "restaurant.json")       # at the repository root
CONFIG = json.load(open(CONFIG_PATH)) if os.path.exists(CONFIG_PATH) else None
SECTION_OF = {}
if CONFIG:
    SEATS = int(CONFIG.get("seats", SEATS)); TABLES = int(CONFIG.get("tables", TABLES))
    keep = set()
    for sec in CONFIG.get("sections", []):
        cat = category_for(sec.get("name", ""))
        for it in sec.get("items", []):
            iid = str(it["id"]); keep.add(iid); SECTION_OF[iid] = sec.get("name", cat); price = float(it.get("price", 0))
            if iid in ITEMS:
                old = ITEMS[iid]; old["name"] = it.get("name", old["name"])
                if abs(price - old["post"].get("price", old["price"])) > 0.001:       # owner changed the price: it applies all year
                    ratio = old["food_cost"] / old["price"] if old["price"] else 0.3
                    old["price"] = price; old["food_cost"] = round(float(it.get("cost", price * ratio)), 2); old["post"].pop("price", None); old["post"].pop("food_cost", None)
            else:
                ITEMS[iid] = dict(id=iid, name=it.get("name", iid), category=cat, station={"Drinks": "bar", "Desserts": "pastry", "Sides": "fry"}.get(cat, "grill"),
                                  price=price, food_cost=round(float(it.get("cost", price * 0.3)), 2), prep_min=int(it.get("prepMin", 1 if cat == "Drinks" else 12)),
                                  w=float(it.get("popularity", 5)), lunch=1.0, season=None, post={})
            if it.get("hidden"): ITEMS[iid]["w"] = 0
    for iid, it in ITEMS.items():
        if iid not in keep: it["w"] = 0; it["post"]["w"] = 0
SCALE = TABLES / 22                                                          # demand and kitchen capacity follow the number of tables

# ---- second-round fixes, used only by the plan run. These are assumptions, not observed results.
# Dogs: French Dip and Turkey BLT +$1, Harvest Salad removed. Greens inflation: Cobb and Caesar +$0.50.
# Price response for these plan changes only: demand weight x (new/old)^-1.2.
PLAN_PRICE = {"dip_s": 17.00, "blt": 13.00, "cobb": 16.00, "caesar": 14.00}
PLAN_REMOVE = {"harvest"}
# Supplier switches marked "Switch" (ribs 9%, beef 7%, steak 5%, salmon 6%, buns 10%, shrimp 11%, potatoes 12%, oil 5%),
# turned into a food-cost cut per dish from each dish's rough ingredient mix.
PLAN_COST_CUT = {"smash": .068, "bacon": .066, "mush": .066, "dip_s": .062, "meatloaf": .06, "cfs": .065, "ribeye": .05, "strip": .05,
                 "ribs": .09, "halfrib": .09, "pork": .075, "salmon": .06, "shrimp": .11, "nash": .02, "veggie": .03, "fries": .08,
                 "swfries": .03, "rings": .03, "fish": .05, "skins": .06, "pickles": .03}
if PLAN:
    for iid, it in ITEMS.items():
        if iid in PLAN_PRICE:
            old = it["post"].get("price", it["price"]); it["post"]["price"] = PLAN_PRICE[iid]; it["post"]["w_mult"] = (PLAN_PRICE[iid] / old) ** -1.2
        if iid in PLAN_REMOVE: it["post"]["w"] = 0
        if iid in PLAN_COST_CUT: it["post"]["food_cost"] = round(it["post"].get("food_cost", it["food_cost"]) * (1 - PLAN_COST_CUT[iid]), 2)

def item_attr(it, key, day):
    """Price, food cost or weight, honouring the post-change overrides."""
    if day >= CHANGE and key in it["post"]:
        return it["post"][key]
    return it[key]

# ---------------------------------------------------------------- calendar
HOURS = {
    "weekday": {11:.07,12:.14,13:.11,14:.04,15:.03,16:.04,17:.09,18:.16,19:.17,20:.10,21:.05},
    "fri":     {11:.06,12:.12,13:.10,14:.04,15:.03,16:.04,17:.09,18:.15,19:.16,20:.12,21:.06,22:.03},
    "sat":     {11:.07,12:.12,13:.11,14:.06,15:.04,16:.05,17:.09,18:.14,19:.14,20:.10,21:.05,22:.03},
    "sun":     {10:.08,11:.14,12:.17,13:.13,14:.07,15:.04,16:.05,17:.09,18:.11,19:.08,20:.04},
}
WD_MULT = [0.74, 0.80, 0.86, 0.95, 1.27, 1.42, 1.02]   # Mon..Sun
CLOSED = {dt.date(2025,11,27), dt.date(2025,12,25), dt.date(2026,1,1)}
SPECIAL = {dt.date(2025,12,24):0.6, dt.date(2025,12,31):1.3, dt.date(2026,2,14):1.45, dt.date(2026,2,8):0.72,
           dt.date(2026,5,10):1.6, dt.date(2026,6,21):1.35, dt.date(2026,7,4):0.78, dt.date(2026,9,7):0.85,
           dt.date(2025,11,28):1.2, dt.date(2025,12,20):1.15, dt.date(2026,10,10):1.1}

def hour_profile(d):
    wd = d.weekday()
    return HOURS["sun"] if wd == 6 else HOURS["sat"] if wd == 5 else HOURS["fri"] if wd == 4 else HOURS["weekday"]

def season_mult(d, tag):
    if not tag: return 1.0
    doy = d.timetuple().tm_yday
    s = math.sin(2 * math.pi * (doy - 110) / 365)          # +1 near late July, -1 near late January
    return 1 + 0.45 * s if tag == "summer" else 1 - 0.45 * s

def expected_parties(d):
    if d in CLOSED: return 0
    t = (d - START).days / 364
    base = 88 * SCALE * WD_MULT[d.weekday()]
    seasonal = 1 + 0.07 * math.sin(2 * math.pi * (d.timetuple().tm_yday - 110) / 365)
    trend = 1 + 0.08 * t + (0.04 if d >= CHANGE else 0)
    return base * seasonal * trend * SPECIAL.get(d, 1.0)

PARTY_SIZES = [(1,.08),(2,.44),(3,.15),(4,.22),(5,.05),(6,.04),(8,.02)]
def pick(weighted):
    r = random.random() * sum(w for _, w in weighted)
    for v, w in weighted:
        r -= w
        if r <= 0: return v
    return weighted[-1][0]

def choose_item(cat, d, hour):
    opts = []
    for it in ITEMS.values():
        if it["category"] != cat: continue
        w = item_attr(it, "w", d)
        if w <= 0: continue
        if d >= CHANGE: w *= it["post"].get("w_mult", 1.0)
        if hour < 15: w *= it["lunch"]
        else: w *= (2 - it["lunch"]) if it["lunch"] < 1 else 1
        w *= season_mult(d, it["season"])
        if cat == "Drinks" and it["id"] in ("beer", "wine"):
            w *= 2.2 if hour >= 16 else 0.5
            if d.weekday() >= 4: w *= 1.3
        opts.append((it["id"], w))
    return pick(opts) if opts else None             # a menu may have no dishes in this category

def kitchen_capacity(d, hour):
    """Plates per hour the line can push without the ticket times stretching."""
    cap = 48.0
    if d >= CHANGE and d.weekday() in (4, 5) and 17 <= hour <= 21:
        cap = 64.0                                             # the fourth line cook added on Fri/Sat dinner
    if PLAN and d >= CHANGE and d.weekday() in (4, 5) and 11 <= hour <= 14:
        cap = 64.0                                             # plan: proposed rota's lunch peak cooks on Fri/Sat
    return cap * SCALE

# ---------------------------------------------------------------- simulate orders
orders, lines = [], []
oid = 0
day = START
while day <= END:
    exp = expected_parties(day)
    n_parties = 0 if exp == 0 else max(0, int(round(random.gauss(exp, exp * 0.09))))
    prof = hour_profile(day)
    day_orders = []
    for _ in range(n_parties):
        hour = pick(list(prof.items()))
        seated = dt.datetime.combine(day, dt.time(hour)) + dt.timedelta(minutes=random.randint(0, 59))
        size = pick(PARTY_SIZES)
        items = []
        for g in range(size):
            if random.random() < 0.86: items.append(choose_item("Drinks", day, hour))
            if random.random() < 0.93: items.append(choose_item("Entrees", day, hour))
            if random.random() < 0.30: items.append(choose_item("Sides", day, hour))
            if random.random() < 0.27: items.append(choose_item("Desserts", day, hour))
        n_start = pick([(0,.45),(1,.42),(2,.13)]) + (1 if size >= 5 and random.random() < .5 else 0)
        for _ in range(n_start): items.append(choose_item("Starters", day, hour))
        items = [i for i in items if i]
        for c in ("Entrees", "Starters", "Drinks"):                       # nothing picked: fall back to one dish, first category that has any
            if items: break
            pick_ = choose_item(c, day, hour)
            if pick_: items.append(pick_)
        oid += 1
        day_orders.append(dict(order_id=oid, date=day, hour=hour, seated=seated, size=size, items=items))
    # kitchen load per hour -> ticket times
    plates_by_hour = defaultdict(int)
    for o in day_orders:
        plates_by_hour[o["hour"]] += sum(1 for i in o["items"] if ITEMS[i]["category"] in ("Starters", "Entrees"))
    for o in day_orders:
        hot = [i for i in o["items"] if ITEMS[i]["category"] in ("Starters", "Entrees", "Sides")]
        prep = max([ITEMS[i]["prep_min"] for i in hot], default=4)
        load = plates_by_hour[o["hour"]] / kitchen_capacity(day, o["hour"])
        ticket = prep + random.gauss(3.0, 2.2) + max(0.0, load - 1.0) * 18 + max(0.0, load - 0.8) * 7
        ticket = max(4.0, ticket)
        order_lag = max(1.0, random.lognormvariate(math.log(4.5), 0.45))          # minutes from seating to tapping "order"
        fire_lag = max(0.3, random.gauss(1.2, 0.4))                                # KDS acknowledges the ticket
        ordered = o["seated"] + dt.timedelta(minutes=order_lag)
        fired = ordered + dt.timedelta(minutes=fire_lag)
        served = fired + dt.timedelta(minutes=ticket)
        eat = max(15.0, 26 + 5.5 * o["size"] + random.gauss(0, 6))
        paid = served + dt.timedelta(minutes=eat + max(1.0, random.gauss(3.0, 1.2)))   # pay from the phone
        o.update(ordered=ordered, fired=fired, served=served, paid=paid, ticket_min=round(ticket, 1))
        revenue = 0.0; cost = 0.0
        for i in o["items"]:
            it = ITEMS[i]
            p = item_attr(it, "price", day); c = item_attr(it, "food_cost", day)
            revenue += p; cost += c
            lines.append((o["order_id"], day.isoformat(), o["hour"], i, 1, f"{p:.2f}", f"{c:.2f}"))
        orders.append(dict(order_id=o["order_id"], date=day.isoformat(), weekday=day.weekday(), hour=o["hour"],
                           party_size=o["size"], seated_at=o["seated"].isoformat(timespec="minutes"),
                           ordered_at=ordered.isoformat(timespec="minutes"), fired_at=fired.isoformat(timespec="minutes"),
                           served_at=served.isoformat(timespec="minutes"), paid_at=paid.isoformat(timespec="minutes"),
                           ticket_min=round(ticket, 1), turn_min=round((paid - o["seated"]).total_seconds() / 60, 1),
                           revenue=round(revenue, 2), food_cost=round(cost, 2)))
    day += dt.timedelta(days=1)

with open(os.path.join(DATA, "orders.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(orders[0].keys())); w.writeheader(); w.writerows(orders)
with open(os.path.join(DATA, "order_lines.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["order_id", "date", "hour", "item_id", "qty", "price", "food_cost"]); w.writerows(lines)
with open(os.path.join(DATA, "menu.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["item_id", "name", "category", "station", "price", "food_cost", "prep_min", "price_after", "food_cost_after", "removed_after", "section"])
    for it in ITEMS.values():
        w.writerow([it["id"], it["name"], it["category"], it["station"], it["price"], it["food_cost"], it["prep_min"],
                    it["post"].get("price", it["price"]), it["post"].get("food_cost", it["food_cost"]), int(it["post"].get("w", 1) == 0), SECTION_OF.get(it["id"], "")])

# ---------------------------------------------------------------- waste log (prep over-production + plate waste)
sold = defaultdict(int)                      # (date, item) -> units
for l in lines: sold[(l[1], l[3])] += 1
PREP_ITEMS = ["wings", "ribs", "halfrib", "pork", "salmon", "meatloaf", "potpie", "crab", "grits", "cobb", "caesar", "mac", "cfs", "fish"]
PLATE_RATE = {"fries": .16, "slaw": .38, "sidesal": .25, "rings": .12, "swfries": .12, "ribs": .10, "cfs": .15, "mac": .08, "skins": .09, "wings": .06}
PLATE_RATE_POST = {"slaw": .15, "cfs": .07, "fries": .11, "ribs": .06, "sidesal": .18}
if PLAN: PLATE_RATE_POST["cfs"] = .11          # plan: steak portion put back, so a little more comes back uneaten
# pre-change par levels: a fixed sheet per weekday, set generously by the chef
avg_by_wd = defaultdict(list)
d = START
while d < REAL_CHANGE:
    for i in PREP_ITEMS: avg_by_wd[(d.weekday(), i)].append(sold[(d.isoformat(), i)])
    d += dt.timedelta(days=1)
par_sheet = {k: max(2, round(sum(v) / len(v) * 1.38)) for k, v in avg_by_wd.items()}
waste_rows = []
d = START
while d <= END:
    if d in CLOSED: d += dt.timedelta(days=1); continue
    for i in PREP_ITEMS:
        it = ITEMS[i]
        s = sold[(d.isoformat(), i)]
        if d < CHANGE:
            par = par_sheet[(d.weekday(), i)]
        else:
            # forecast-driven par: last-4-same-weekday mean * 1.12, rounded up
            hist = [sold[((d - dt.timedelta(weeks=k)).isoformat(), i)] for k in range(1, 5)]
            par = math.ceil(sum(hist) / 4 * 1.12 + 0.5)
        if item_attr(it, "w", d) <= 0: par = 0
        prepped = max(par, s) if random.random() < 0.9 else par
        over = max(0, prepped - s)
        spoil = sum(1 for _ in range(over) if random.random() < 0.72)       # most over-prep can't be carried over
        if spoil:
            waste_rows.append([d.isoformat(), i, "over-prep", prepped, s, spoil, round(spoil * item_attr(it, "food_cost", d) * 0.62, 2)])
    for i, rate in PLATE_RATE.items():
        s = sold[(d.isoformat(), i)]
        if not s: continue
        r = PLATE_RATE_POST.get(i, rate) if d >= CHANGE else rate
        units = sum(1 for _ in range(s) if random.random() < r)
        if units:
            waste_rows.append([d.isoformat(), i, "plate", s, s, units, round(units * item_attr(ITEMS[i], "food_cost", d) * 0.5, 2)])
    d += dt.timedelta(days=1)
with open(os.path.join(DATA, "waste.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["date", "item_id", "kind", "prepped", "sold", "wasted_units", "waste_cost"]); w.writerows(waste_rows)

# ---------------------------------------------------------------- staff rota (the template actually worked)
RATES = {"Server": 15.0, "Line cook": 21.0, "Prep cook": 17.5, "Dishwasher": 15.5, "Host": 14.5, "Bartender": 18.0}
def rota_template(after):
    """(weekday, role, start_hour, end_hour, headcount). Hours are decimals, 22.5 = 22:30."""
    rows = []
    for wd in range(7):
        close = 23 if wd in (4, 5) else 21 if wd == 6 else 22
        if wd == 6:
            rows += [(wd, "Server", 9.5, 15, 5), (wd, "Server", 15, close + .5, 4)]
        elif wd == 5:
            rows += [(wd, "Server", 10.5, 15, 4), (wd, "Server", 16, close + .5, 6)]
        elif wd == 4:
            rows += [(wd, "Server", 10.5, 15, 3), (wd, "Server", 16, close + .5, 6 if after else 5)]
        else:
            rows += [(wd, "Server", 10.5, 15, 3 if not (after and wd == 0) else 2), (wd, "Server", 16, close + .5, 4)]
        rows += [(wd, "Line cook", 9, 16, 2), (wd, "Line cook", 15, close + 1, 3 + (1 if after and wd in (4, 5) else 0))]
        rows += [(wd, "Prep cook", 7, 15, 1), (wd, "Dishwasher", 10, 16, 1), (wd, "Dishwasher", 16, close + 1, 1)]
        rows += [(wd, "Bartender", 16, close + .5, 2 if wd in (4, 5) else 1)]
        if wd >= 4: rows += [(wd, "Host", 17, 21.5, 1)]
    return rows
with open(os.path.join(DATA, "rota.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["period", "weekday", "role", "start_hour", "end_hour", "headcount", "hourly_rate"])
    for period, after in (("before", False), ("after", True)):
        for r in rota_template(after): w.writerow([period, *r[:4], max(1, round(r[4] * SCALE)) if SCALE != 1 else r[4], RATES[r[1]]])   # staff scale with tables

# ---------------------------------------------------------------- supplier quotes (weekly, two quotes per ingredient)
INGREDIENTS = [
 # id, name, unit, current supplier, alt supplier, base price, yearly drift, volatility, seasonal amp
 ("beef",    "Ground beef (80/20)",  "lb",   "Northline Foods",    "Hometown Butcher",   4.35, 0.14, 0.03, 0.00),
 ("steak",   "Ribeye / strip, choice","lb",  "Northline Foods",    "Hometown Butcher",  14.20, 0.11, 0.04, 0.00),
 ("wings",   "Chicken wings",        "lb",   "Prairie Provisions", "Northline Foods",    2.95, 0.06, 0.08, 0.10),
 ("chicken", "Chicken breast",       "lb",   "Prairie Provisions", "Northline Foods",    2.60, 0.03, 0.03, 0.00),
 ("ribs",    "Pork back ribs",       "lb",   "Northline Foods",    "Hometown Butcher",   3.90, 0.05, 0.04, 0.05),
 ("salmon",  "Atlantic salmon",      "lb",   "Prairie Provisions", "Bayline Seafood",   10.80, 0.02, 0.09, 0.00),
 ("shrimp",  "Shrimp 16/20",         "lb",   "Prairie Provisions", "Bayline Seafood",    9.40, -0.04, 0.06, 0.00),
 ("potato",  "Russet potatoes",      "50 lb","Northline Foods",    "Greenfield Produce", 24.00, 0.04, 0.05, 0.12),
 ("greens",  "Romaine & mixed greens","case","Greenfield Produce", "Northline Foods",   31.00, 0.03, 0.12, 0.25),
 ("cheese",  "Cheddar & Swiss",      "lb",   "Northline Foods",    "Prairie Provisions", 3.75, 0.05, 0.02, 0.00),
 ("buns",    "Brioche buns",         "dozen","Northline Foods",    "Sunrise Bakery",     5.10, 0.07, 0.01, 0.00),
 ("oil",     "Fry oil",              "35 lb","Northline Foods",    "Prairie Provisions", 38.00, -0.09, 0.03, 0.00),
]
ALT_SPREAD = {"beef": -0.07, "steak": -0.05, "wings": 0.04, "chicken": 0.03, "ribs": -0.09, "salmon": -0.06, "shrimp": -0.11,
              "potato": -0.12, "greens": 0.08, "cheese": 0.02, "buns": -0.10, "oil": -0.05}
# usage per unit sold, by item -> (ingredient, qty)
RECIPE = {"smash": [("beef", .33), ("buns", 1/12), ("cheese", .08)], "bacon": [("beef", .40), ("buns", 1/12), ("cheese", .1)],
          "mush": [("beef", .40), ("buns", 1/12), ("cheese", .1)], "meatloaf": [("beef", .5)], "dip_s": [("beef", .35), ("buns", 1/12)],
          "ribeye": [("steak", .9)], "strip": [("steak", .8)], "cfs": [("beef", .45), ("potato", .006)],
          "wings": [("wings", 1.1)], "nash": [("chicken", .4), ("buns", 1/12)], "cobb": [("chicken", .25), ("greens", .04)],
          "caesar": [("chicken", .2), ("greens", .05)], "harvest": [("greens", .05)], "sidesal": [("greens", .02)], "potpie": [("chicken", .3)],
          "ribs": [("ribs", 2.2)], "halfrib": [("ribs", 1.1)], "pork": [("ribs", .35), ("buns", 1/12)], "salmon": [("salmon", .45)],
          "shrimp": [("shrimp", .35)], "grits": [("shrimp", .3)], "fries": [("potato", .01), ("oil", .004)], "skins": [("potato", .012), ("cheese", .1)],
          "fish": [("potato", .008), ("oil", .006)], "rings": [("oil", .005)], "pickles": [("oil", .003)], "swfries": [("oil", .004)],
          "mac": [("cheese", .3)], "macside": [("cheese", .15)], "blt": [("greens", .01)]}
weekly_usage = defaultdict(float)
for l in lines:
    wk = (dt.date.fromisoformat(l[1]) - START).days // 7
    for ing, q in RECIPE.get(l[3], []): weekly_usage[(wk, ing)] += q
with open(os.path.join(DATA, "supplier_quotes.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["week_start", "ingredient_id", "ingredient", "unit", "supplier", "is_current", "price", "units_used"])
    for ing_id, name, unit, cur, alt, base, drift, vol, samp in INGREDIENTS:
        shock = 0.0
        for wk in range(52):
            ws = START + dt.timedelta(weeks=wk)
            t = wk / 51
            shock = shock * 0.7 + random.gauss(0, vol)
            seasonal = 1 + samp * math.sin(2 * math.pi * (ws.timetuple().tm_yday - 20) / 365)
            p = base * (1 + drift * t) * seasonal * (1 + shock)
            # the alternative supplier moves with the market but at its own spread
            alt_p = p * (1 + ALT_SPREAD[ing_id] + random.gauss(0, vol * 0.6))
            # from the change date Crab Cakes/shrimp moved to Bayline; everything else stays with the incumbent
            used = round(weekly_usage[(wk, ing_id)], 1)
            w.writerow([ws.isoformat(), ing_id, name, unit, cur, 1, round(p, 3), used])
            w.writerow([ws.isoformat(), ing_id, name, unit, alt, 0, round(alt_p, 3), used])

# ---------------------------------------------------------------- reviews
THEMES = ["food", "wait", "service", "value", "qr menu", "portions", "atmosphere"]
POS = {"food": ["smash burger was outstanding", "ribs fell off the bone", "best wings in town", "salmon cooked perfectly", "hot chicken sandwich is a must"],
       "wait": ["food came out fast", "quick turnaround even on a Saturday", "ordered and ate within 25 minutes"],
       "service": ["server checked in at the right moments", "staff were warm and attentive", "manager came by to say hi"],
       "value": ["fair prices for the portions", "great value lunch", "left full for under $25"],
       "qr menu": ["QR menu was easy and had photos", "loved paying from my phone", "ordering from the table was seamless"],
       "portions": ["huge portions", "generous sides"],
       "atmosphere": ["cozy booths", "great playlist", "nice patio in summer"]}
NEG = {"food": ["burger came out overcooked", "fries were lukewarm", "crab cakes were mostly filler", "shrimp and grits were bland"],
       "wait": ["waited 40 minutes for entrees", "kitchen was clearly slammed", "appetizers arrived with the mains", "long wait on a Friday night"],
       "service": ["hard to flag anyone down", "nobody checked on us after the food arrived"],
       "value": ["pricey for what you get", "$34 for the ribeye felt steep"],
       "qr menu": ["QR code didn't scan on my phone", "miss having a paper menu", "menu site was slow to load"],
       "portions": ["smaller portions than before", "coleslaw was a tiny cup"],
       "atmosphere": ["too loud at dinner", "booth seats are worn"]}
by_date = defaultdict(list)
for o in orders: by_date[o["date"]].append(o)
review_rows = []; rid = 0
for dkey, os_ in by_date.items():
    d = dt.date.fromisoformat(dkey)
    n = sum(1 for _ in range(len(os_)) if random.random() < 0.018)
    tickets = sorted(o["ticket_min"] for o in os_)
    p90 = tickets[int(len(tickets) * 0.9)] if tickets else 15
    for _ in range(n):
        o = random.choice(os_)
        slow = o["ticket_min"] > 28
        base = 4.25 + (0.18 if d >= CHANGE else 0) - (1.6 if slow else 0) + random.gauss(0, 0.75)
        rating = min(5, max(1, int(round(base))))
        k = pick([(1, .55), (2, .33), (3, .12)])
        chosen = set()
        weights = {"food": 5, "wait": 3 + (3 if slow else 0), "service": 3, "value": 2, "qr menu": 2 if d < CHANGE + dt.timedelta(weeks=8) else 1, "portions": 1, "atmosphere": 1.5}
        while len(chosen) < k: chosen.add(pick(list(weights.items())))
        parts = []; themes = []
        for th in sorted(chosen):                 # sorted: set order changes between runs
            if th == "wait": pos = not slow and random.random() < 0.8
            elif th == "qr menu": pos = random.random() < (0.72 if d < CHANGE else 0.88)
            elif th == "portions": pos = random.random() < (0.8 if d < CHANGE else 0.74 if PLAN else 0.55)
            else: pos = random.random() < (0.3 + 0.16 * rating)
            parts.append(random.choice(POS[th] if pos else NEG[th])); themes.append(f"{th}:{'+' if pos else '-'}")
        rid += 1
        review_rows.append([rid, dkey, random.choice(["Google", "Google", "Yelp", "TripAdvisor"]), rating, o["party_size"], o["ticket_min"],
                            ";".join(themes), (". ".join(p[0].upper() + p[1:] for p in parts) + ".")])
with open(os.path.join(DATA, "reviews.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["review_id", "date", "platform", "rating", "party_size", "ticket_min", "themes", "text"]); w.writerows(review_rows)

json.dump({"restaurant": (CONFIG or {}).get("name", "Maple & Oak"), "start": START.isoformat(), "end": END.isoformat(), "change_date": REAL_CHANGE.isoformat(), "mode": MODE, "seats": SEATS, "tables": TABLES,
           "closed": sorted(x.isoformat() for x in CLOSED)}, open(os.path.join(DATA, "meta.json"), "w"), indent=1)
print(f"[{MODE}] {len(orders):,} parties, {len(lines):,} order lines, {len(waste_rows):,} waste rows, {len(review_rows):,} reviews; {START} to {END}, change on {CHANGE}")
