#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot.py — travel-hunter в Telegram: кнопки, настройки, автоскан.
Запуск:  python3 bot.py  (или двойной клик по start.command)
"""

import os, time, json, uuid, datetime, urllib.request, urllib.parse, traceback
import hunter

LOGO = os.path.join(hunter.BASE, "logo.png")
_logo_file_id = None   # после первой загрузки шлём по file_id (быстро)

TG    = hunter.TG_TOKEN
OWNER = str(hunter.SEC.get("telegram_chat_id", ""))
API   = f"https://api.telegram.org/bot{TG}"
SCAN_EVERY = hunter.CFG.get("check_every_hours", 6) * 3600
DROP = hunter.CFG.get("drop_vs_median_pct", 12) / 100.0

WATCH_DESTS = [("Бангкок","BKK"),("Пхукет","HKT"),("Бали","DPS"),("Дели","DEL"),
               ("Мальдивы","MLE"),("Куала-Лумпур","KUL"),("Шри-Ланка","CMB"),
               ("Нячанг","CXR"),("Ханой","HAN"),("Гоа","GOI")]

MAIN_KB = json.dumps({
    "keyboard": [
        ["🎯 Куда лететь"],
        ["🗺 Карта цен", "🔥 Лучшее"],
        ["🧩 Составной маршрут", "🔎 Обновить"],
        ["👀 Следить за…", "📋 Мои подписки"],
        ["⚙️ Настройки", "❓ Помощь"],
    ],
    "resize_keyboard": True,
}, ensure_ascii=False)

HELP = (
 "🤖 travel-hunter\n\n"
 "Что я показываю:\n"
 "• ✈️ перелёты туда-обратно (дешёвые И удобные)\n"
 "• 🏨 ПАКЕТНЫЕ ТУРЫ с отелем 4★+ (перелёт+отель+трансфер)\n"
 "• 🧩 составные маршруты (Сочи→Абу-Даби→Бангкок→…)\n\n"
 "Кнопки:\n"
 "• 🎯 Куда лететь — куда сезон и выгодно, поездка целиком\n"
 "• 🗺 Карта цен — всё разом (🟢 дёшево/⚪️ норма/🔴 дорого)\n"
 "• 🔥 Лучшее — топ стоящих находок\n"
 "• 🧩 Составной маршрут — несколько стран за раз, по плечам\n"
 "• 🔎 Обновить — пересканировать сейчас\n"
 "• 👀 Следить за… — порог цены, я сам разбужу\n"
 "• ⚙️ Настройки — когда лететь, сколько людей, удобство\n\n"
 "Сам сканирую по расписанию и пишу, когда цена реально падает."
)

# ---------- Telegram API ----------
def api(method, **p):
    try:
        data = urllib.parse.urlencode(p).encode()
        with urllib.request.urlopen(f"{API}/{method}", data=data, timeout=70) as r:
            return json.load(r)
    except Exception as e:
        print("api err", method, e); return {}

def send(chat, text, kb=None):
    for i in range(0, len(text), 3900):
        p = dict(chat_id=chat, text=text[i:i+3900], disable_web_page_preview="true")
        if kb and i == 0:
            p["reply_markup"] = kb
        api("sendMessage", **p)

def answer_cb(cb_id, text=""):
    api("answerCallbackQuery", callback_query_id=cb_id, text=text)

def send_logo(chat, caption=""):
    """шлёт логотип: первый раз загружает файл, дальше — по file_id"""
    global _logo_file_id
    if _logo_file_id:
        api("sendPhoto", chat_id=chat, photo=_logo_file_id, caption=caption)
        return
    if not os.path.exists(LOGO):
        return
    try:
        with open(LOGO, "rb") as f:
            img = f.read()
        b = uuid.uuid4().hex
        body = b""
        for name, value in (("chat_id", str(chat)), ("caption", caption)):
            if value:
                body += (f"--{b}\r\nContent-Disposition: form-data; "
                         f"name=\"{name}\"\r\n\r\n{value}\r\n").encode()
        body += (f"--{b}\r\nContent-Disposition: form-data; name=\"photo\"; "
                 f"filename=\"logo.png\"\r\nContent-Type: image/png\r\n\r\n").encode()
        body += img + f"\r\n--{b}--\r\n".encode()
        req = urllib.request.Request(f"{API}/sendPhoto", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={b}"})
        resp = json.load(urllib.request.urlopen(req, timeout=60))
        photos = (resp.get("result") or {}).get("photo") or []
        if photos:
            _logo_file_id = photos[-1].get("file_id")
    except Exception as e:
        print("logo err", e)

def ikb(rows):
    return json.dumps({"inline_keyboard":
        [[{"text": t, "callback_data": d} for t, d in row] for row in rows]},
        ensure_ascii=False)

def ukb(rows):
    """кнопки-ссылки: rows = [[(label, url), ...], ...]"""
    return json.dumps({"inline_keyboard":
        [[{"text": t, "url": u} for t, u in row] for row in rows]},
        ensure_ascii=False)

# ---------- данные ----------
def ensure_tables():
    con = hunter.db()
    con.execute("""CREATE TABLE IF NOT EXISTS watches(
        id INTEGER PRIMARY KEY AUTOINCREMENT, chat TEXT, key TEXT, max_price INTEGER)""")
    con.execute("CREATE TABLE IF NOT EXISTS alerted(key TEXT PRIMARY KEY, price INTEGER)")
    con.commit(); return con

def latest_rows():
    con = hunter.db()
    q = """SELECT p.kind,p.origin,p.dest,p.label,p.price_two,p.transfers,p.depart,p.ret,p.info,p.url,p.ts
           FROM prices p JOIN (SELECT kind,origin,dest,MAX(ts) mx FROM prices
                               GROUP BY kind,origin,dest) m
           ON p.kind=m.kind AND p.origin=m.origin AND p.dest=m.dest AND p.ts=m.mx"""
    rows = []
    for r in con.execute(q):
        d = dict(kind=r[0], origin=r[1], dest=r[2], label=r[3], price_two=r[4],
                 transfers=r[5], depart=r[6], ret=r[7], info=r[8], url=r[9], ts=r[10])
        if d["kind"] == "tour":
            if "#" not in d["dest"]:
                continue                      # легаси-строки до мульти-источников
            d["src"] = d["dest"].split("#", 1)[1].split("@", 1)[0]
        d["median"] = hunter.median_before(con, d["kind"], d["origin"], d["dest"])
        rows.append(d)
    return rows

def _groups():
    rows = latest_rows()
    return ([r for r in rows if r["kind"].startswith("flight")],
            [r for r in rows if r["kind"] == "tour"],
            [r for r in rows if r["kind"] == "chain"])

def map_text():
    flights, tours, chains = _groups()
    if not flights and not tours:
        return "Данных пока нет — нажми 🔎 Обновить."
    return hunter.build_map(flights, tours, chains)

def send_map(chat):
    """карта + кнопки «смотреть» на всё, что помечено 🟢 дёшево"""
    flights, tours, _ = _groups()
    rows = []
    for x in sorted(flights + tours, key=lambda z: z["price_two"]):
        if x["kind"] == "flightc" or not x.get("url"):
            continue
        if not hunter.arrow(x["price_two"], x.get("median")).startswith("🟢"):
            continue
        if x["kind"] == "tour":
            rows.append([(f"🟢 тур {x['label']} · {hunter.rub(x['price_two'])}", x["url"])])
        else:
            rows.append([(f"🟢 {x['label']} из {hunter.oname(x['origin'])} · "
                          f"{hunter.rub(x['price_two'])}", x["url"])])
        if len(rows) >= 6:
            break
    send(chat, map_text(), kb=(ukb(rows) if rows else MAIN_KB))

def send_whereto(chat):
    flights, tours, _ = _groups()
    if not flights:
        send(chat, "Данных пока нет — нажми 🔎 Обновить.", kb=MAIN_KB); return
    text, buttons = hunter.whereto_core(flights, tours)
    send(chat, text, kb=(ukb(buttons) if buttons else MAIN_KB))

def send_chains(chat):
    _, _, chains = _groups()
    if not chains:
        send(chat, "Составных маршрутов пока нет — нажми 🔎 Обновить.", kb=MAIN_KB); return
    for c in sorted(chains, key=lambda x: x["price_two"]):
        text = (f"🧩 {hunter.oname(c['origin'])} → {c['label']} → домой\n"
                f"итого перелёты: {hunter.rub(c['price_two'])}  "
                f"{hunter.arrow(c['price_two'], c['median'])}\n"
                f"плечи: {c['info']}")
        codes = c["dest"].split(":")
        urls = (c.get("url") or "").split("|")
        rows, row = [], []
        for (a, b), u in zip(zip(codes, codes[1:]), urls):
            if not u: continue
            row.append((f"✈️ {hunter.dname(a)}→{hunter.dname(b)}", u))
            if len(row) == 2:
                rows.append(row); row = []
        if row: rows.append(row)
        send(chat, text, kb=(ukb(rows) if rows else None))
    send(chat, "⚠️ Это оценка по плечам: даты подбери под себя по кнопкам выше. "
               "Отели не включены.", kb=MAIN_KB)

def send_best(chat):
    flights, tours, _ = _groups()
    hot = hunter.find_highlights(flights + tours)
    if not hot:
        send(chat, "Стоящего сейчас нет.", kb=MAIN_KB); return
    for x in hot[:5]:
        btn = [[("👉 Смотреть и купить", x["url"])]] if x.get("url") else None
        send(chat, hunter.fmt_highlight(x, with_url=False), kb=(ukb(btn) if btn else None))

def current_price(code):
    vals = [r["price_two"] for r in latest_rows()
            if r["kind"].startswith("flight") and r["dest"] == code]
    return min(vals) if vals else None

def do_scan():
    con = hunter.db()
    return hunter.scan_flights(con) + hunter.scan_tours(con) + hunter.scan_chains(con)

# ---------- настройки ----------
def season_window(name):
    t = datetime.date.today(); y = t.year
    defs = {"summer": (6, 1, 8, 31), "autumn": (9, 1, 11, 30),
            "winter": (12, 1, 2, 28), "spring": (3, 1, 5, 31)}
    m1, d1, m2, d2 = defs[name]
    if name == "winter":
        if t.month <= 2:
            start, end = datetime.date(y - 1, m1, d1), datetime.date(y, m2, d2)
        else:
            start, end = datetime.date(y, m1, d1), datetime.date(y + 1, m2, d2)
    else:
        start, end = datetime.date(y, m1, d1), datetime.date(y, m2, d2)
        if t > end:
            start, end = datetime.date(y + 1, m1, d1), datetime.date(y + 1, m2, d2)
    if start < t: start = t
    return start.isoformat(), end.isoformat()

def settings_text():
    c = hunter.CFG
    wf, wt = hunter.when_window()
    when = f"{wf} — {wt}" if (wf or wt) else "любые даты (весь год)"
    mode = ("🛋 удобные (≤1 пересадка, без долгих стыковок)"
            if c.get("comfort_mode", "comfort") == "comfort"
            else "💸 любые, лишь бы дёшево")
    home = hunter.oname(c.get("home", "AER"))
    return (f"⚙️ НАСТРОЙКИ\n\n"
            f"🏠 Точка старта: {home} — всё считаю от неё,\n"
            f"   к турам из Москвы добавляю реальные билеты {home}⇄МСК\n"
            f"👤 Людей: {c.get('passengers', 2)}\n"
            f"📅 Когда вылет: {when}\n"
            f"✈️ Перелёты: {mode}\n"
            f"💰 Бюджет: 🔥<{hunter.rub(c['budget']['fire'])} · "
            f"потолок {hunter.rub(c['budget']['ok'])}\n\n"
            f"Что поменять — жми:")

def settings_kb():
    return ikb([
        [("🏠 Старт: Сочи", "set:home:AER"), ("🏠 Старт: Минводы", "set:home:MRV")],
        [("👤 1", "set:pax:1"), ("👤 2", "set:pax:2"),
         ("👤 3", "set:pax:3"), ("👤 4", "set:pax:4")],
        [("📅 Любые даты", "set:when:any")],
        [("☀️ Лето", "set:when:summer"), ("🍂 Осень", "set:when:autumn")],
        [("❄️ Зима", "set:when:winter"), ("🌸 Весна", "set:when:spring")],
        [("🛋 Только удобные перелёты", "set:mode:comfort")],
        [("💸 Любые, лишь бы дёшево", "set:mode:any")],
    ])

def apply_setting(what, val):
    c = hunter.CFG
    if what == "home":
        c["home"] = val
        if val not in c.get("origins", []):
            c.setdefault("origins", []).insert(0, val)
        msg = (f"🏠 Точка старта: {hunter.oname(val)}. Всё считаю от неё — "
               f"к турам добавляю реальные билеты {hunter.oname(val)}⇄Москва под даты тура.")
    elif what == "pax":
        c["passengers"] = int(val)
        msg = f"👤 Считаю на {val} чел."
    elif what == "mode":
        c["comfort_mode"] = val
        msg = ("🛋 Показываю удобные перелёты (≤1 пересадка, без долгих стыковок)."
               if val == "comfort" else "💸 Показываю самые дешёвые, даже жёсткие.")
    elif what == "when":
        if val == "any":
            c["when"] = {"from": "", "to": ""}
            msg = "📅 Окно вылета: любые даты."
        else:
            f, t = season_window(val)
            c["when"] = {"from": f, "to": t}
            msg = f"📅 Окно вылета: {f} — {t}."
    else:
        return "Не понял настройку."
    hunter.save_cfg()
    return msg + "\n\nНажми 🔎 Обновить, чтобы пересканировать под новые настройки."

# ---------- watch-кнопки ----------
def send_watch_menu(chat):
    rows, line = [], []
    for label, code in WATCH_DESTS:
        line.append((label, f"w:{code}"))
        if len(line) == 2:
            rows.append(line); line = []
    if line: rows.append(line)
    send(chat, "За каким направлением следить?", kb=ikb(rows))

def send_price_menu(chat, code):
    cur = current_price(code)
    name = hunter.dname(code)
    if not cur:
        send(chat, f"По «{name}» пока нет данных — нажми 🔎 Обновить.", kb=MAIN_KB); return
    r5 = lambda x: int(round(x / 5000) * 5000)
    opts = [r5(cur * 0.95), r5(cur * 0.90), r5(cur * 0.85)]
    rows = [[(f"≤ {hunter.rub(v)}", f"p:{code}:{v}")] for v in opts]
    rows.append([(f"любое ниже {hunter.rub(cur)}", f"p:{code}:{int(cur)}")])
    send(chat, f"«{name}» сейчас {hunter.rub(cur)}.\nКогда тебя разбудить?", kb=ikb(rows))

def send_watch_list(chat):
    con = ensure_tables()
    rows = list(con.execute("SELECT id,key,max_price FROM watches WHERE chat=?", (chat,)))
    if not rows:
        send(chat, "Подписок нет. Нажми «👀 Следить за…».", kb=MAIN_KB); return
    ik = [[(f"❌ {hunter.dname(k)} ≤ {hunter.rub(p)}", f"del:{i}")] for i, k, p in rows]
    send(chat, "Твои подписки (нажми, чтобы убрать):", kb=ikb(ik))

# ---------- обработка ----------
def handle_text(chat, t):
    low = t.lower().lstrip("/")
    if t == "🎯 Куда лететь" or low == "go":
        send_whereto(chat)
    elif t == "🗺 Карта цен" or low == "map":
        send_map(chat)
    elif t == "🔥 Лучшее" or low == "best":
        send_best(chat)
    elif t == "🧩 Составной маршрут" or low == "chains":
        send_chains(chat)
    elif t == "🔎 Обновить" or low == "scan":
        send(chat, "🔎 Сканирую: перелёты + туры + составные, ~1 мин…")
        do_scan()
        send_map(chat)
    elif t == "👀 Следить за…" or low == "watch":
        send_watch_menu(chat)
    elif t == "📋 Мои подписки" or low == "list":
        send_watch_list(chat)
    elif t == "⚙️ Настройки" or low == "settings":
        send(chat, settings_text(), kb=settings_kb())
    elif t == "❓ Помощь" or low in ("help", "start"):
        if low == "start":
            send_logo(chat, caption="🏝 Travel Hunter — охотник за выгодными поездками")
        send(chat, HELP, kb=MAIN_KB)
    else:
        send(chat, "Жми кнопки внизу 👇", kb=MAIN_KB)

def handle_cb(chat, cb_id, data):
    if data.startswith("w:"):
        send_price_menu(chat, data[2:]); answer_cb(cb_id)
    elif data.startswith("p:"):
        _, code, val = data.split(":"); val = int(val)
        con = ensure_tables()
        con.execute("INSERT INTO watches(chat,key,max_price) VALUES(?,?,?)", (chat, code, val))
        con.commit(); answer_cb(cb_id, "Готово")
        send(chat, f"✅ Слежу: {hunter.dname(code)} ≤ {hunter.rub(val)}. Напишу, когда поймаю.",
             kb=MAIN_KB)
    elif data.startswith("del:"):
        con = ensure_tables()
        con.execute("DELETE FROM watches WHERE id=? AND chat=?", (int(data[4:]), chat))
        con.commit(); answer_cb(cb_id, "Убрал"); send_watch_list(chat)
    elif data.startswith("set:"):
        _, what, val = data.split(":")
        msg = apply_setting(what, val)
        answer_cb(cb_id, "Сохранил")
        send(chat, msg, kb=MAIN_KB)
    else:
        answer_cb(cb_id)

# ---------- авто-алерты ----------
def auto_alerts(items, con):
    mode = hunter.CFG.get("comfort_mode", "comfort")
    cand = []
    for x in items:
        if x["kind"] == "chain":
            continue
        if mode == "comfort" and x["kind"].startswith("flight") and not hunter.row_is_comfort(x):
            continue
        if x.get("median") and x["price_two"] <= x["median"] * (1 - DROP):
            cand.append(x)
        else:
            for (_i, _c, key, maxp) in con.execute("SELECT id,chat,key,max_price FROM watches"):
                k = key.lower()
                if (k == x["dest"].lower() or k in x["label"].lower()) \
                        and x["price_two"] <= maxp:
                    cand.append(x); break
    out, seen = [], set()
    for x in sorted(cand, key=lambda z: z["price_two"]):
        # туры дедупим по стране (не по источнику), перелёты — по маршруту
        key = (f"tour-{x['label']}" if x["kind"] == "tour"
               else f"{x['origin']}-{x['dest']}-flight")
        if key in seen: continue
        seen.add(key)
        prev = con.execute("SELECT price FROM alerted WHERE key=?", (key,)).fetchone()
        if prev and x["price_two"] >= prev[0]: continue
        con.execute("INSERT OR REPLACE INTO alerted(key,price) VALUES(?,?)", (key, x["price_two"]))
        out.append(x)
    con.commit(); return out

# ---------- главный цикл ----------
def main():
    ensure_tables()
    print(f"bot запущен. Автоскан каждые {SCAN_EVERY//3600} ч. Жду нажатия кнопок…")
    if OWNER:
        send(OWNER, "🤖 Я на связи! Жми кнопки внизу 👇\n"
                    "Новое: 🧩 составные маршруты и ⚙️ настройки "
                    "(когда лететь, сколько людей, удобство перелётов).", kb=MAIN_KB)
    offset, last_scan = None, time.time()
    while True:
        if time.time() - last_scan > SCAN_EVERY:
            try:
                con = ensure_tables()
                for x in auto_alerts(do_scan(), con):
                    btn = [[("👉 Смотреть и купить", x["url"])]] if x.get("url") else None
                    send(OWNER, "🔔 Цена упала!\n\n" + hunter.fmt_highlight(x, with_url=False),
                         kb=(ukb(btn) if btn else MAIN_KB))
            except Exception:
                traceback.print_exc()
            last_scan = time.time()
        resp = api("getUpdates", offset=offset, timeout=30)
        for u in resp.get("result", []):
            offset = u["update_id"] + 1
            try:
                if "callback_query" in u:
                    cb = u["callback_query"]
                    chat = str(cb["message"]["chat"]["id"])
                    if OWNER and chat != OWNER: continue
                    handle_cb(chat, cb["id"], cb.get("data", ""))
                elif "message" in u:
                    msg = u["message"]
                    chat = str(msg["chat"]["id"])
                    text = (msg.get("text") or "").strip()
                    if OWNER and chat != OWNER: continue
                    if text: handle_text(chat, text)
            except Exception:
                traceback.print_exc()

if __name__ == "__main__":
    main()
