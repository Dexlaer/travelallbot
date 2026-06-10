#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
travel-hunter — охотник за дешёвыми и КОМФОРТНЫМИ поездками в Азию.
Перелёты (Aviasales/Travelpayouts) + пакетные туры (Travelata) + составные
маршруты по плечам. История цен, сезонность, поездка целиком.
Запуск:  python3 hunter.py
"""

import json, os, sys, socket, sqlite3, urllib.request, urllib.parse, datetime, statistics

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "prices.db")
socket.setdefaulttimeout(30)

def load_json(name):
    with open(os.path.join(BASE, name), encoding="utf-8") as f:
        return json.load(f)

CFG = load_json("config.json")
SEC = load_json("secrets.json")
TP_TOKEN = SEC.get("travelpayouts_token", "")
TG_TOKEN = SEC.get("telegram_token", "")
TG_CHAT  = SEC.get("telegram_chat_id", "")

def save_cfg():
    with open(os.path.join(BASE, "config.json"), "w", encoding="utf-8") as f:
        json.dump(CFG, f, ensure_ascii=False, indent=2)

DEST_NAMES = {
    "BKK": "Бангкок", "HKT": "Пхукет", "KBV": "Краби", "USM": "Самуи",
    "UTP": "Паттайя", "CNX": "Чиангмай", "DPS": "Бали", "CXR": "Нячанг",
    "SGN": "Хошимин", "HAN": "Ханой", "PQC": "Фукуок", "GOI": "Гоа",
    "GOX": "Гоа", "DEL": "Дели", "CMB": "Шри-Ланка", "MLE": "Мальдивы",
    "KUL": "Куала-Лумпур", "REP": "Сием-Реап", "MNL": "Манила", "KTM": "Катманду",
    "AUH": "Абу-Даби", "DXB": "Дубай", "IST": "Стамбул",
}
ORIGIN_NAMES = {"AER": "Сочи", "MRV": "Минводы", "MOW": "Москва"}

# сезоны: когда в направлении хорошая погода (месяцы) + подпись
SEASONS = {
    "BKK": ({11, 12, 1, 2, 3}, "ноя–мар"),
    "HKT": ({11, 12, 1, 2, 3, 4}, "ноя–апр"),
    "KBV": ({11, 12, 1, 2, 3, 4}, "ноя–апр"),
    "USM": ({1, 2, 3, 4, 5, 6, 7, 8}, "янв–авг"),
    "UTP": ({11, 12, 1, 2, 3}, "ноя–мар"),
    "CNX": ({11, 12, 1, 2}, "ноя–фев"),
    "DPS": ({4, 5, 6, 7, 8, 9, 10}, "апр–окт"),
    "CXR": ({2, 3, 4, 5, 6, 7, 8, 9}, "фев–сен"),
    "SGN": ({12, 1, 2, 3, 4}, "дек–апр"),
    "HAN": ({10, 11, 12, 1, 2, 3, 4}, "окт–апр"),
    "PQC": ({11, 12, 1, 2, 3, 4}, "ноя–апр"),
    "GOI": ({11, 12, 1, 2, 3}, "ноя–мар"),
    "GOX": ({11, 12, 1, 2, 3}, "ноя–мар"),
    "DEL": ({10, 11, 12, 1, 2, 3}, "окт–мар"),
    "CMB": ({12, 1, 2, 3}, "дек–мар"),
    "MLE": ({11, 12, 1, 2, 3, 4}, "ноя–апр"),
    "KUL": (set(range(1, 13)), "круглый год"),
    "MNL": ({12, 1, 2, 3, 4}, "дек–апр"),
    "KTM": ({10, 11, 3, 4}, "окт–ноя, мар–апр"),
}
TOUR_DEST = {"Таиланд": "BKK", "Бали": "DPS", "Индия/Гоа": "GOI",
             "Мальдивы": "MLE", "Шри-Ланка": "CMB", "Филиппины": "MNL",
             "Вьетнам": "CXR"}

def dname(code): return DEST_NAMES.get(code, code)
def oname(code): return ORIGIN_NAMES.get(code, code)
def rub(n): return f"{int(round(n)):,}".replace(",", " ") + "₽"

def season_state(code):
    s = SEASONS.get(code)
    if not s: return ("", "", False)
    months, label = s
    m = datetime.date.today().month
    if m in months: return ("☀️", label, True)
    for k in range(1, 7):
        if ((m + k - 1) % 12) + 1 in months:
            return (("🌤" if k <= 2 else "🌧"), label, False)
    return ("🌧", label, False)

def trip_total(flight_price_two):
    return flight_price_two + CFG.get("hotel_per_night_two", 4500) * CFG.get("est_nights", 10)

def when_window():
    w = CFG.get("when", {}) or {}
    return (w.get("from") or "", w.get("to") or "")

def in_window(date_str):
    wf, wt = when_window()
    if wf and date_str < wf: return False
    if wt and date_str > wt: return False
    return True

def row_is_comfort(row):
    """по строке из базы: '1+1 перес., EY' -> обе части <=1"""
    try:
        a, b = row["info"].split(" перес")[0].split("+")
        return int(a) <= 1 and int(b) <= 1
    except Exception:
        return False

# ---- HTTP ----
def http_get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)

# ---- база ----
def db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS prices(
        ts TEXT, kind TEXT, origin TEXT, dest TEXT, label TEXT,
        price_two INTEGER, transfers INTEGER,
        depart TEXT, ret TEXT, info TEXT, url TEXT)""")
    return con

def median_before(con, kind, origin, dest):
    rows = [r[0] for r in con.execute(
        "SELECT price_two FROM prices WHERE kind=? AND origin=? AND dest=?",
        (kind, origin, dest))]
    return statistics.median(rows) if rows else None

def _insert(con, kind, o, d, label, price_two, transfers, depart, ret, info, url):
    con.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (datetime.datetime.now().isoformat(timespec="seconds"), kind, o, d, label,
         price_two, transfers, depart, ret, info, url))

def _entry(kind, o, d, label, x, price_two, info, url, med):
    return dict(kind=kind, origin=o, dest=d, label=label, price_two=price_two,
        transfers=x.get("transfers", 0), depart=str(x.get("departure_at"))[:10],
        ret=str(x.get("return_at"))[:10], info=info, url=url, median=med)

def _link_of(x):
    marker = CFG.get("marker", "")
    link = x.get("link", "")
    full = ("https://www.aviasales.ru" + link) if link else ""
    if full and marker:
        full += ("&" if "?" in full else "?") + "marker=" + marker
    return full

def search_url(a, b, date, pax):
    """прямая ссылка на поиск Aviasales: a->b на дату date (YYYY-MM-DD)"""
    dd, mm = date[8:10], date[5:7]
    u = f"https://www.aviasales.ru/search/{a}{dd}{mm}{b}{pax}"
    m = CFG.get("marker", "")
    return u + (f"?marker={m}" if m else "")

def _info_of(x):
    return (f"{x.get('transfers',0)}+{x.get('return_transfers',0)} перес., "
            f"{x.get('airline','')}")

def _is_comfort_offer(x):
    """<=1 пересадки в каждую сторону и плечо не дольше max_leg_hours
       (длинное плечо = почти наверняка ночёвка в аэропорту)"""
    if x.get("transfers", 0) > 1 or x.get("return_transfers", 0) > 1:
        return False
    cap = CFG.get("max_leg_hours", 16) * 60
    for k in ("duration_to", "duration_back", "duration"):
        v = x.get(k)
        if v and v > cap * (2 if k == "duration" else 1):
            return False
    return True

# ---- скан перелётов: дешёвый + комфортный ----
def scan_flights(con):
    found = []
    pax = CFG["passengers"]
    max_tr = CFG.get("max_transfers", 3)
    for o in CFG["origins"]:
        for d in CFG["destinations"]:
            url = ("https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
                   f"?origin={o}&destination={d}&currency={CFG.get('currency','rub')}"
                   f"&one_way=false&sorting=price&limit=30&token={TP_TOKEN}")
            try:
                data = http_get(url).get("data", [])
            except Exception as e:
                print(f"  ! {o}->{d}: ошибка запроса ({e})"); continue
            cand = [x for x in data if x.get("price")
                    and (x.get("transfers", 0) <= max_tr)
                    and (x.get("return_transfers", 0) <= max_tr)
                    and in_window(str(x.get("departure_at"))[:10])]
            if not cand:
                continue
            best = min(cand, key=lambda x: x["price"])
            med = median_before(con, "flight", o, d)
            _insert(con, "flight", o, d, dname(d), best["price"] * pax,
                    best.get("transfers", 0), str(best.get("departure_at"))[:10],
                    str(best.get("return_at"))[:10], _info_of(best), _link_of(best))
            found.append(_entry("flight", o, d, dname(d), best,
                                best["price"] * pax, _info_of(best), _link_of(best), med))
            comf = [x for x in cand if _is_comfort_offer(x)]
            if comf:
                bc = min(comf, key=lambda x: x["price"])
                if bc is not best and bc["price"] > best["price"]:
                    medc = median_before(con, "flightc", o, d)
                    _insert(con, "flightc", o, d, dname(d), bc["price"] * pax,
                            bc.get("transfers", 0), str(bc.get("departure_at"))[:10],
                            str(bc.get("return_at"))[:10], _info_of(bc), _link_of(bc))
                    found.append(_entry("flightc", o, d, dname(d), bc,
                                        bc["price"] * pax, _info_of(bc), _link_of(bc), medc))
    con.commit()
    return found

# ---- реальный подлёт из дома до Москвы под даты тура ----
def feeder_flight(checkin_str, nights, pax):
    """ищет реальные билеты home->MOW->home вокруг дат тура.
       возвращает (цена_на_всех, 'инфо с датами/временем') или (None, '')"""
    home = CFG.get("home", "AER")
    try:
        ci = datetime.date.fromisoformat(str(checkin_str))
    except (ValueError, TypeError):
        return None, ""
    ret = ci + datetime.timedelta(days=int(nights or 7))
    url = ("https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
           f"?origin={home}&destination=MOW&currency={CFG.get('currency','rub')}"
           f"&departure_at={ci.isoformat()[:7]}&return_at={ret.isoformat()[:7]}"
           f"&one_way=false&sorting=price&limit=30&token={TP_TOKEN}")
    try:
        data = http_get(url).get("data", [])
    except Exception:
        return None, ""
    lo_dep, hi_dep = (ci - datetime.timedelta(days=2)).isoformat(), ci.isoformat()
    lo_ret, hi_ret = ret.isoformat(), (ret + datetime.timedelta(days=2)).isoformat()
    def fits(x, relax=0):
        d = str(x.get("departure_at"))[:10]
        r = str(x.get("return_at"))[:10]
        lo_d = (ci - datetime.timedelta(days=2 + relax)).isoformat()
        hi_r = (ret + datetime.timedelta(days=2 + relax)).isoformat()
        return (x.get("price") and x.get("transfers", 0) <= 1
                and x.get("return_transfers", 0) <= 1
                and lo_d <= d <= hi_dep and lo_ret <= r <= hi_r)
    cand = [x for x in data if fits(x)] or [x for x in data if fits(x, relax=2)]
    if not cand:
        return None, ""
    best = min(cand, key=lambda x: x["price"])
    price = best["price"] * pax
    d = str(best.get("departure_at"))[:10]
    r = str(best.get("return_at"))[:10]
    dur = best.get("duration_to") or best.get("duration")
    dur_s = f", {int(dur)//60}ч в пути" if dur else ""
    info = f"{rub(price)} ({d[5:]}→{r[5:]}{dur_s})"
    return price, info

def recheck_tour(cid, t, pax, offer):
    """перепроверка подозрительно дешёвого тура: повторяем точечный запрос
       (тот же отель, дата заезда, ночи) — повторилось свежим = похоже на правду"""
    ci = str(offer.get("checkinDate") or "")
    nights = offer.get("nights")
    hid = offer.get("hotelId")
    if not (ci and nights and hid):
        return False
    url = ("https://api-gateway.travelata.ru/statistic/cheapestTours"
           f"?countries[]={cid}&departureCity={t.get('departure_city_id',2)}"
           f"&nightRange[from]={nights}&nightRange[to]={nights}"
           f"&touristGroup[adults]={pax}&touristGroup[kids]=0&touristGroup[infants]=0"
           f"&checkInDateRange[from]={ci}&checkInDateRange[to]={ci}")
    try:
        data = http_get(url).get("data", [])
    except Exception:
        return False
    now = datetime.datetime.now()
    for x in data:
        if x.get("hotelId") != hid or not x.get("price"):
            continue
        if abs(x["price"] - offer["price"]) / offer["price"] > 0.2:
            continue
        pub = x.get("publishedAt")
        try:
            if pub and (now - datetime.datetime.fromisoformat(pub)).total_seconds() <= 6 * 3600:
                return True
        except ValueError:
            pass
    return False

# ---- скан туров: мульти-источник ----
def scan_tours(con):
    """собирает туры со всех подключённых источников.
       сейчас живой источник — Travelata; Level.Travel и Слетать.ру
       включатся автоматически, когда в secrets.json появятся их ключи."""
    t = CFG.get("tours", {})
    if not t.get("enabled"):
        return []
    found = _tours_travelata(con)
    if SEC.get("leveltravel_api_key"):
        print("  · Level.Travel: ключ есть, интеграция в работе — скажи Клоду, он встроит")
    if SEC.get("sletat_key"):
        print("  · Слетать.ру: ключ есть, интеграция в работе — скажи Клоду, он встроит")
    return found

def _tours_travelata(con):
    SRC = "Travelata"
    t = CFG.get("tours", {})
    if not t.get("countries"):
        return []
    found = []
    pax = CFG.get("passengers", 2)
    min_rating = float(t.get("min_rating", 4.0))
    wf, _ = when_window()
    today = datetime.date.today()
    start = today + datetime.timedelta(days=14)
    if wf:
        try:
            wfd = datetime.date.fromisoformat(wf)
            if wfd > start: start = wfd
        except ValueError:
            pass
    d_from = start.isoformat()
    d_to = (start + datetime.timedelta(days=28)).isoformat()   # лимит Travelata: окно <=30 дней
    addon = t.get("transfer_to_moscow_two", 0)
    for cname, cid in t["countries"].items():
        url = ("https://api-gateway.travelata.ru/statistic/cheapestTours"
               f"?countries[]={cid}&departureCity={t.get('departure_city_id',2)}"
               f"&nightRange[from]={t.get('nights_from',7)}&nightRange[to]={t.get('nights_to',12)}"
               f"&touristGroup[adults]={pax}&touristGroup[kids]=0&touristGroup[infants]=0"
               f"&checkInDateRange[from]={d_from}&checkInDateRange[to]={d_to}")
        try:
            data = http_get(url).get("data", [])
        except Exception as e:
            print(f"  ! тур {cname}: ошибка запроса ({e})"); continue
        # NB: поле expired у Travelata-кэша всегда в прошлом (живут ~час) —
        # фильтровать по нему нельзя. Берём свежеопубликованное (<24ч);
        # подозрительно дешёвое не выкидываем, а ПЕРЕПРОВЕРЯЕМ точечным
        # запросом — подтвердилось = горящее ⚡️, нет = фантом.
        now = datetime.datetime.now()
        floor = float(t.get("min_plausible_per_person", 30000)) * pax
        def fresh_pub(x):
            pub = x.get("publishedAt")
            if pub:
                try:
                    if (now - datetime.datetime.fromisoformat(pub)).total_seconds() > 24 * 3600:
                        return False
                except ValueError:
                    pass
            return True
        data = [x for x in data if x.get("price") and fresh_pub(x)]
        if not data:
            continue
        normal = [x for x in data if x["price"] >= floor]
        susp = sorted([x for x in data if x["price"] < floor], key=lambda x: x["price"])
        # горящее = подозрительно дёшево, НО не мусор: не дешевле 40% медианы
        # рынка этой страны (отсекает «только отель» за 3тр) + воспроизводится
        # свежим точечным запросом (отсекает протухшие записи)
        batch_med = statistics.median([x["price"] for x in data])
        hot = [x for x in susp[:2]
               if x["price"] >= batch_med * 0.4 and recheck_tour(cid, t, pax, x)]
        note = ""
        if hot:
            best = min(hot, key=lambda x: x["price"])
            note = " ⚡️ГОРЯЩЕЕ: аномально дёшево, перепроверил — повторяется"
        else:
            if susp:
                print(f"  · {cname}: фантом отсеян ({rub(susp[0]['price'])}, не подтвердился)")
            if not normal:
                continue
            good = [x for x in normal
                    if float(x.get("hotelRating") or 0) >= min_rating
                    and int(x.get("hotelCategory") or 0) >= 3]
            best = min(good or normal, key=lambda x: x["price"])
            if not good:
                note = " ⚠️отель так себе"
        # реальный подлёт из дома (Сочи) под даты этого тура
        fp, finfo = feeder_flight(best.get("checkinDate"), best.get("nights"), pax)
        if fp:
            total = best["price"] + fp
            add_str = f"подлёт {oname(CFG.get('home','AER'))}⇄МСК {finfo}"
        else:
            est = int(addon / 2 * pax)
            total = best["price"] + est
            add_str = f"подлёт ≈{rub(est)} (билеты под даты не нашлись)"
        info = (f"{best.get('hotelCategoryName','')} {(best.get('hotelName') or '')[:20]}"
                f" ★{str(best.get('hotelRating') or '?')[:3]}, {best.get('nights')}н"
                f" (тур {rub(best['price'])} + {add_str}){note}")
        dest_key = f"{cname}#{SRC}"          # история цен — отдельно на источник
        med = median_before(con, "tour", "MOW", dest_key)
        _insert(con, "tour", "MOW", dest_key, cname, total, 0,
                str(best.get("checkinDate")), "", info, best.get("tourPageUrl", ""))
        found.append(dict(kind="tour", origin="MOW", dest=dest_key, label=cname, src=SRC,
            price_two=total, transfers=0, depart=str(best.get("checkinDate")), ret="",
            info=info, url=best.get("tourPageUrl", ""), median=med))
    con.commit()
    return found

# ---- составные маршруты: сумма дешёвых плеч (оценка) ----
def scan_chains(con):
    found = []
    pax = CFG.get("passengers", 2)
    for chain in CFG.get("chains", []):
        legs, total, infos, okat = list(zip(chain, chain[1:])), 0, [], True
        first_date = ""
        leg_urls = []
        for (a, b) in legs:
            url = ("https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
                   f"?origin={a}&destination={b}&currency={CFG.get('currency','rub')}"
                   f"&one_way=true&sorting=price&limit=20&token={TP_TOKEN}")
            try:
                data = http_get(url).get("data", [])
            except Exception as e:
                print(f"  ! плечо {a}->{b}: {e}"); okat = False; break
            cand = [x for x in data if x.get("price")
                    and x.get("transfers", 0) <= 1
                    and in_window(str(x.get("departure_at"))[:10])]
            if not cand:
                okat = False; break
            best = min(cand, key=lambda x: x["price"])
            total += best["price"] * pax
            bdate = str(best.get("departure_at"))[:10]
            if not first_date:
                first_date = bdate
            infos.append(f"{dname(a)}→{dname(b)} {rub(best['price']*pax)} ({bdate[5:]})")
            leg_urls.append(search_url(a, b, bdate, pax))
        if not okat:
            continue
        key = ":".join(chain)
        label = " → ".join(dname(c) for c in chain[1:-1])
        med = median_before(con, "chain", chain[0], key)
        info = " · ".join(infos)
        url = "|".join(leg_urls)
        _insert(con, "chain", chain[0], key, label, total, 0, first_date, "", info, url)
        found.append(dict(kind="chain", origin=chain[0], dest=key, label=label,
            price_two=total, transfers=0, depart=first_date, ret="",
            info=info, url=url, median=med))
    con.commit()
    return found

# ---- визуальные метки ----
def arrow(price, med):
    if not med: return "🆕"
    if price <= med * 0.95: return "🟢 дёшево"
    if price >= med * 1.05: return "🔴 дорого"
    return "⚪️ норма"

def _split_kinds(flights):
    cheap, comf = {}, {}
    for f in flights:
        key = (f["origin"], f["dest"])
        if f["kind"] == "flightc": comf[key] = f
        else: cheap[key] = f
    return cheap, comf

# ---- карта цен ----
def build_map(flights, tours, chains=None):
    cheap, comf = _split_kinds(flights)
    pax = CFG.get("passengers", 2)
    L = [f"🗺 КАРТА ЦЕН · на {pax} чел · туда-обратно"]
    tss = [x.get("ts") for x in list(cheap.values()) + list(tours) if x.get("ts")]
    stamp = max(tss)[:16].replace("T", " ") if tss else f"{datetime.datetime.now():%Y-%m-%d %H:%M}"
    wf, wt = when_window()
    period = f" · вылет {wf or '...'}—{wt or '...'}" if (wf or wt) else ""
    L.append(f"данные от {stamp}{period}")
    L.append("☀️ сезон сейчас · 🌤 скоро сезон · 🌧 не сезон")
    for o in CFG["origins"]:
        rows = sorted([f for (org, _), f in cheap.items() if org == o],
                      key=lambda x: x["price_two"])
        if not rows: continue
        L.append(f"\n━━ ✈️ ТОЛЬКО ПЕРЕЛЁТ из {oname(o)} ━━")
        for f in rows:
            emj, _, _ = season_state(f["dest"])
            tr = f["info"].split(" перес")[0]
            line = (f"{emj} {f['label']:<13}{rub(f['price_two']):>10}  {tr}  "
                    f"{arrow(f['price_two'], f['median'])}")
            c = comf.get((o, f["dest"]))
            if c and c["price_two"] > f["price_two"]:
                line += f"\n   🛋 удобный: {rub(c['price_two'])}"
            L.append(line)
    if tours:
        home = oname(CFG.get("home", "AER"))
        L.append("\n━━ 🏨 ПАКЕТНЫЕ ТУРЫ (перелёт+отель+трансфер) ━━")
        L.append(f"   в цене РЕАЛЬНЫЕ билеты {home}⇄Москва под даты тура")
        best_by = {}                          # min по стране среди источников
        for t in tours:
            if (t["label"] not in best_by
                    or t["price_two"] < best_by[t["label"]]["price_two"]):
                best_by[t["label"]] = t
        for t in sorted(best_by.values(), key=lambda x: x["price_two"]):
            emj, _, _ = season_state(TOUR_DEST.get(t["label"], ""))
            src = f" · {t['src']}" if t.get("src") else ""
            L.append(f"{emj} {t['label']:<13}{rub(t['price_two']):>10}  "
                     f"{arrow(t['price_two'], t['median'])}{src}")
    if chains:
        L.append("\n━━ 🧩 СОСТАВНЫЕ МАРШРУТЫ (оценка по плечам) ━━")
        for c in sorted(chains, key=lambda x: x["price_two"]):
            L.append(f"• {c['label']}: {rub(c['price_two'])} {arrow(c['price_two'], c['median'])}")
    L.append("\n👉 кнопки «смотреть и купить» — в 🎯, 🔥 и 🧩")
    return "\n".join(L)

# ---- 🧩 подробно про составные ----
def chains_text(chains):
    if not chains:
        return "Составных маршрутов пока нет — нажми 🔎 Обновить."
    L = ["🧩 СОСТАВНЫЕ МАРШРУТЫ · перелёты на всех, по плечам", ""]
    for c in sorted(chains, key=lambda x: x["price_two"]):
        L.append(f"✈️ {oname(c['origin'])} → {c['label']} → домой")
        L.append(f"   итого перелёты: {rub(c['price_two'])}  {arrow(c['price_two'], c['median'])}")
        L.append(f"   плечи: {c['info']}")
        L.append("")
    L.append("⚠️ Это ОЦЕНКА: цена каждого плеча — лучшая в своём окне дат,")
    L.append("реальные даты нужно состыковать вручную. Отели не включены.")
    return "\n".join(L)

# ---- 🎯 куда лететь ----
def whereto_core(flights, tours):
    """возвращает (текст, кнопки-ссылки [[(label,url),...],...])"""
    cheap, comf = _split_kinds(flights)
    mode = CFG.get("comfort_mode", "comfort")
    tour_by_code = {}
    for t in tours:
        code = TOUR_DEST.get(t["label"])
        if code and (code not in tour_by_code
                     or t["price_two"] < tour_by_code[code]["price_two"]):
            tour_by_code[code] = t
    entries = []
    home = CFG.get("home", "")
    origin_order = ([home] if home in CFG["origins"] else []) + \
                   [o for o in CFG["origins"] if o != home]
    for d in CFG["destinations"]:
        f = o = None
        for org in origin_order:
            f = cheap.get((org, d))
            if f: o = org; break
        if not f: continue
        c = comf.get((o, d))
        if mode == "comfort":
            fly = c or f
            warn = "" if (c or row_is_comfort(f)) else " ⚠️ только 2+ пересадки"
        else:
            fly = f
            warn = ""
        total_fly = trip_total(fly["price_two"])
        t = tour_by_code.get(d)
        options = [total_fly] + ([t["price_two"]] if t else [])
        emj, slabel, now = season_state(d)
        entries.append(dict(d=d, o=o, fly=fly, t=t, total_fly=total_fly, warn=warn,
                            best=min(options), emj=emj, slabel=slabel, now=now))
    entries.sort(key=lambda e: (not e["now"], e["best"]))
    fire, ok = CFG["budget"]["fire"], CFG["budget"]["ok"]
    pax = CFG.get("passengers", 2)
    mode_lbl = "🛋 удобные перелёты" if mode == "comfort" else "💸 самые дешёвые"
    L = [f"🎯 КУДА ЛЕТЕТЬ · поездка целиком на {pax} чел · {mode_lbl}", ""]
    buttons = []
    for i, e in enumerate(entries[:8], 1):
        season = "сезон СЕЙЧАС" if e["now"] else f"сезон {e['slabel']}"
        L.append(f"{i}. {e['emj']} {dname(e['d'])} — {season}")
        tr = e["fly"]["info"].split(" перес")[0]
        L.append(f"   ✈️ перелёт из {oname(e['o'])}: {rub(e['fly']['price_two'])} ({tr}){e['warn']}")
        L.append(f"      + отель ≈ итого {rub(e['total_fly'])}")
        if e["t"]:
            L.append(f"   🏨 ПАКЕТНЫЙ ТУР: {rub(e['t']['price_two'])} · {e['t']['info'].split(' (')[0]}")
        v = "🔥 огонь" if e["best"] <= fire else ("🟢 в бюджете" if e["best"] <= ok else "🔴 дороговато")
        L.append(f"   {v}: ≈ {rub(e['best'])} за всё")
        L.append("")
        row = []
        if e["fly"].get("url"):
            row.append((f"{i} ✈️ {dname(e['d'])[:12]}", e["fly"]["url"]))
        if e["t"] and e["t"].get("url"):
            row.append((f"{i} 🏨 тур", e["t"]["url"]))
        if row:
            buttons.append(row)
    L.append(f"отель прикинут как {rub(CFG.get('hotel_per_night_two',4500))}/ночь × "
             f"{CFG.get('est_nights',10)} ночей; туры — с подлётом до МСК")
    L.append("👇 кнопки «смотреть» — под сообщением")
    return "\n".join(L), buttons

def whereto_text(flights, tours):
    return whereto_core(flights, tours)[0]

# ---- подсветка стоящего ----
def find_highlights(items):
    fire, ok = CFG["budget"]["fire"], CFG["budget"]["ok"]
    drop = CFG.get("drop_vs_median_pct", 12) / 100.0
    mode = CFG.get("comfort_mode", "comfort")
    best = {}
    for x in items:
        if x["kind"] == "chain":
            continue                       # составные — оценка, не алертим
        if mode == "comfort" and x["kind"].startswith("flight") and not row_is_comfort(x):
            continue
        # туры схлопываем по стране (между источниками), перелёты — по маршруту
        k = ("tour", x["label"]) if x["kind"] == "tour" \
            else ("flight", x["origin"], x["dest"])
        if k not in best or x["price_two"] < best[k]["price_two"]:
            best[k] = x
    hot = []
    for x in best.values():
        total = x["price_two"] if x["kind"] == "tour" else trip_total(x["price_two"])
        below_budget = total <= ok
        below_median = x.get("median") and x["price_two"] <= x["median"] * (1 - drop)
        if below_budget or below_median:
            hot.append(dict(x, tier=("🔥" if total <= fire else "🟢"), total=total))
    hot.sort(key=lambda z: z["total"])
    return hot

def fmt_highlight(x, with_url=True):
    pax = CFG.get("passengers", 2)
    if x["kind"] == "tour":
        what = "🏨 пакетный тур" + (f" ({x['src']})" if x.get("src") else "")
    else:
        what = "✈️ перелёт туда-обратно"
    head = f"{x.get('tier','🟢')} {oname(x['origin'])} → {x['label']} · {what}"
    price = f"{rub(x['price_two'])} на {pax} чел"
    med = x.get("median")
    if med and abs(x["price_two"] - med) / med >= 0.03:
        price += f"  (обычно ~{rub(med)})"
    if x["kind"] == "tour":
        when = f"заезд {x['depart']}"
    else:
        when = f"туда {x['depart']}" + (f" · обратно {x['ret']}" if x.get("ret") else "")
    parts = [head, price, f"📅 {when} · {x['info']}"]
    if x["kind"] != "tour":
        parts.append(f"≈ поездка целиком с отелем: {rub(trip_total(x['price_two']))}")
    else:
        parts.append("⏳ цены туров живут считанные часы — открывай и проверяй сразу")
    emj, slabel, now = season_state(x["dest"] if x["kind"] != "tour"
                                    else TOUR_DEST.get(x["label"], ""))
    if slabel:
        parts.append(f"{emj} сезон: {'сейчас' if now else slabel}")
    if with_url and x.get("url"):
        parts.append(x["url"])
    return "\n".join(parts)

# ---- Telegram ----
def tg_send(text):
    if not (TG_TOKEN and TG_CHAT):
        return False
    data = urllib.parse.urlencode({
        "chat_id": TG_CHAT, "text": text, "disable_web_page_preview": "true"
    }).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=data)
        return True
    except Exception as e:
        print(f"  ! Telegram не отправился: {e}")
        return False

# ---- main ----
def main():
    con = db()
    print("Сканирую перелёты...")
    flights = scan_flights(con)
    print(f"  строк: {len(flights)}")
    print("Сканирую туры...")
    tours = scan_tours(con)
    print(f"  стран: {len(tours)}")
    print("Сканирую составные...")
    chains = scan_chains(con)
    print(f"  маршрутов: {len(chains)}")

    price_map = build_map(flights, tours, chains)
    print("\n" + price_map)
    print("\n" + whereto_text(flights, tours))

    hot = find_highlights(flights + tours)
    print(f"\n=== Стоящее ({len(hot)}) ===")
    for x in hot[:10]:
        print("\n" + fmt_highlight(x))

    if TG_TOKEN and TG_CHAT:
        tg_send(price_map)
        for x in hot[:5]:
            tg_send(fmt_highlight(x))
        print("\n📨 Отправлено в Telegram.")

if __name__ == "__main__":
    main()
