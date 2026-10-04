"""TeleAds backend: Telegram Mini App API + full admin panel (Flask + SQLite)."""
import os, hmac, hashlib, json, time, sqlite3, secrets, urllib.parse, urllib.request
from functools import wraps
from flask import (Flask, g, request, jsonify, session, redirect, url_for,
                   render_template, flash, abort)

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE, "teleads.db"))
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-me-now")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")  # e.g. https://you.github.io
DEV_MODE = os.environ.get("DEV_MODE", "0") == "1"       # allow testing in a normal browser

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", hashlib.sha256((ADMIN_PASSWORD + "teleads").encode()).hexdigest())
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

DEFAULT_SETTINGS = {
    "app_name": "TeleAds",
    "announcement": "",
    "maintenance": "0",
    "browse_stars": "2", "browse_ton": "0.005",
    "click_stars": "5", "click_ton": "0.01",
    "watch_ton": "0.05", "watch_countdown": "5",
    "ad_cooldown_hours": "24",
    "checkin_rewards": "10,20,30,50,75,100,150",
    "checkin_ton_bonus": "0.1",
    "min_withdraw_ton": "0.5", "min_withdraw_stars": "50",
    "withdraw_methods": "Telegram @wallet,TON Space (Self-Custody)",
    "require_ad_approval": "1",
    "allow_user_campaigns": "1",
    "new_user_bonus_stars": "0",
    "categories": "Web3 & Crypto,Gaming,Bots & Utilities,Finance,Education,Lifestyle",
    "bot_link": "",  # e.g. https://t.me/YourBot/app  (invite link = bot_link?startapp=USERID)
    "ref_bonus_stars": "20", "ref_bonus_new_stars": "5",
    "case_prizes": "1:68,2:15,5:10,10:3,25:1,100:0.6", "case_cooldown_hours": "24",
    "wheel_prizes": "0:30,1:30,2:20,5:12,10:6,50:2", "wheel_cooldown_hours": "6",
    "promo_codes": "WELCOME:20:100",  # CODE:stars:max_uses, comma separated
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT, name TEXT, is_premium INTEGER DEFAULT 0,
  balance_ton REAL DEFAULT 0, balance_stars INTEGER DEFAULT 0,
  earned_ton REAL DEFAULT 0, earned_stars INTEGER DEFAULT 0,
  ads_watched INTEGER DEFAULT 0, streak INTEGER DEFAULT 0, last_checkin INTEGER DEFAULT 0,
  banned INTEGER DEFAULT 0, note TEXT DEFAULT '', created_at INTEGER, last_seen INTEGER);
CREATE TABLE IF NOT EXISTS ads(
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, description TEXT, sponsor TEXT,
  url TEXT, cta TEXT DEFAULT 'Open', category TEXT DEFAULT 'Web3 & Crypto',
  ad_type TEXT DEFAULT 'CHANNEL_POST', image_url TEXT DEFAULT '',
  currency TEXT DEFAULT 'STARS', total_budget REAL DEFAULT 0, remaining_budget REAL DEFAULT 0,
  cpc REAL DEFAULT 0, cpm REAL DEFAULT 0, reward_stars INTEGER DEFAULT 15,
  impressions INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0,
  status TEXT DEFAULT 'ACTIVE', owner_id INTEGER DEFAULT 0, featured INTEGER DEFAULT 0,
  created_at INTEGER);
CREATE TABLE IF NOT EXISTS tx(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT, amount REAL,
  currency TEXT, credit INTEGER, type TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS withdrawals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, currency TEXT, amount REAL,
  method TEXT, address TEXT, status TEXT DEFAULT 'PENDING', admin_note TEXT DEFAULT '',
  created_at INTEGER, processed_at INTEGER);
CREATE TABLE IF NOT EXISTS events(
  user_id INTEGER, ad_id INTEGER, kind TEXT, day INTEGER, ts INTEGER);
CREATE INDEX IF NOT EXISTS ev_idx ON events(user_id, ad_id, kind, ts);
CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY, user_id INTEGER, ad_id INTEGER, ts INTEGER, used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS promo_uses(user_id INTEGER, code TEXT, PRIMARY KEY(user_id, code));
"""

SEED_ADS = [
    ("TON Community Official", "Fastest blockchain natively integrated into Telegram.", "@toncoin",
     "https://t.me/toncoin", "Explore TON", "Web3 & Crypto", "REWARDED_VIDEO", 35),
    ("Telegram Mini Apps Developers Hub", "Official SDK and tooling for building Mini Apps.", "@telegram_dev",
     "https://t.me/telegram_dev", "Open Docs", "Bots & Utilities", "CHANNEL_POST", 20),
]


# ---------------------------------------------------------------- db helpers
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=15)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()


def init_db():
    c = sqlite3.connect(DB_PATH)
    c.executescript(SCHEMA)
    for col in ("referrer INTEGER DEFAULT 0", "last_case INTEGER DEFAULT 0", "last_wheel INTEGER DEFAULT 0"):
        try:
            c.execute("ALTER TABLE users ADD COLUMN " + col)
        except sqlite3.OperationalError:
            pass
    for k, v in DEFAULT_SETTINGS.items():
        c.execute("INSERT OR IGNORE INTO settings VALUES(?,?)", (k, v))
    if c.execute("SELECT COUNT(*) FROM ads").fetchone()[0] == 0:
        now = int(time.time())
        for t, d, s, u, cta, cat, typ, r in SEED_ADS:
            c.execute("INSERT INTO ads(title,description,sponsor,url,cta,category,ad_type,reward_stars,created_at)"
                      " VALUES(?,?,?,?,?,?,?,?,?)", (t, d, s, u, cta, cat, typ, r, now))
    c.commit()
    c.close()


def settings():
    return {r["key"]: r["value"] for r in db().execute("SELECT key,value FROM settings")}


def fnum(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def add_tx(uid, title, amount, cur, credit, typ):
    db().execute("INSERT INTO tx(user_id,title,amount,currency,credit,type,ts) VALUES(?,?,?,?,?,?,?)",
                 (uid, title, amount, cur, 1 if credit else 0, typ, int(time.time())))


def credit(uid, stars=0, ton=0.0, title="", typ="REWARD"):
    db().execute("UPDATE users SET balance_stars=balance_stars+?, balance_ton=balance_ton+?,"
                 " earned_stars=earned_stars+?, earned_ton=earned_ton+? WHERE id=?",
                 (stars, ton, max(stars, 0), max(ton, 0), uid))
    if stars:
        add_tx(uid, title, stars, "STARS", stars > 0, typ)
    if ton:
        add_tx(uid, title, ton, "TON", ton > 0, typ)


def notify(uid, text):
    """Send a Telegram message to a user via the bot (silently ignored on failure)."""
    if not BOT_TOKEN:
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": uid, "text": text}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data, timeout=8)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- CORS
@app.after_request
def cors(resp):
    if request.path.startswith("/api/"):
        resp.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Init-Data"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@app.route("/api/<path:_>", methods=["OPTIONS"])
def preflight(_):
    return ("", 204)


# ---------------------------------------------------------------- Mini App auth
def verify_init_data(raw):
    """Validate Telegram WebApp initData (HMAC). Returns user dict or None."""
    if not raw:
        return None
    try:
        pairs = dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))
        their = pairs.pop("hash", "")
        check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        mine = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(mine, their):
            return None
        if time.time() - int(pairs.get("auth_date", 0)) > 86400 * 2:
            return None
        return json.loads(pairs["user"])
    except Exception:
        return None


def api(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        raw = request.headers.get("X-Init-Data", "")
        tg = verify_init_data(raw) if BOT_TOKEN else None
        if not tg and DEV_MODE:
            tg = {"id": 1, "first_name": "Dev Tester", "username": "dev_tester", "is_premium": True}
        if not tg:
            return jsonify(error="Open this app from Telegram."), 401
        now = int(time.time())
        d = db()
        u = d.execute("SELECT * FROM users WHERE id=?", (tg["id"],)).fetchone()
        name = (tg.get("first_name", "") + " " + tg.get("last_name", "")).strip()
        if not u:
            s = settings()
            ref = request.headers.get("X-Ref", "")
            ref = int(ref) if ref.isdigit() and int(ref) != tg["id"] and d.execute(
                "SELECT 1 FROM users WHERE id=?", (int(ref),)).fetchone() else 0
            d.execute("INSERT INTO users(id,username,name,is_premium,created_at,last_seen,referrer) VALUES(?,?,?,?,?,?,?)",
                      (tg["id"], tg.get("username", ""), name, int(bool(tg.get("is_premium"))), now, now, ref))
            if ref:
                credit(ref, stars=int(fnum(s["ref_bonus_stars"])), title=f"Friend joined: {name or tg['id']}", typ="REFERRAL")
                credit(tg["id"], stars=int(fnum(s["ref_bonus_new_stars"])), title="Invite bonus", typ="REFERRAL")
                notify(ref, f"🎉 {name or 'A friend'} joined with your link! +{s['ref_bonus_stars']}⭐")
            bonus = int(fnum(s["new_user_bonus_stars"]))
            if bonus:
                credit(tg["id"], stars=bonus, title="Welcome bonus", typ="BONUS")
            d.commit()
            u = d.execute("SELECT * FROM users WHERE id=?", (tg["id"],)).fetchone()
        else:
            d.execute("UPDATE users SET last_seen=?, username=?, name=?, is_premium=? WHERE id=?",
                      (now, tg.get("username", ""), name, int(bool(tg.get("is_premium"))), tg["id"]))
            d.commit()
        if u["banned"]:
            return jsonify(error="Your account is suspended."), 403
        if settings()["maintenance"] == "1":
            return jsonify(error="App is under maintenance. Please come back soon.", maintenance=True), 503
        g.uid = tg["id"]
        return fn(*a, **kw)
    return wrapper


def public_user(u):
    return {k: u[k] for k in ("id", "username", "name", "is_premium", "balance_ton", "balance_stars",
                              "earned_ton", "earned_stars", "ads_watched", "streak", "last_checkin")}


def public_ad(a):
    return {k: a[k] for k in ("id", "title", "description", "sponsor", "url", "cta", "category",
                              "ad_type", "image_url", "reward_stars", "featured", "impressions", "clicks")}


def day_start(ts=None):
    return int((ts or time.time()) // 86400)


def once_per_cooldown(uid, ad_id, kind, hours):
    since = int(time.time() - hours * 3600)
    r = db().execute("SELECT 1 FROM events WHERE user_id=? AND ad_id=? AND kind=? AND ts>?",
                     (uid, ad_id, kind, since)).fetchone()
    return r is None


def log_event(uid, ad_id, kind):
    db().execute("INSERT INTO events VALUES(?,?,?,?,?)", (uid, ad_id, kind, day_start(), int(time.time())))


def charge_campaign(ad, amount):
    """Deduct from a user-created campaign's budget; auto-complete when empty."""
    if not ad["owner_id"] or amount <= 0:
        return
    db().execute("UPDATE ads SET remaining_budget=MAX(remaining_budget-?,0) WHERE id=?", (amount, ad["id"]))
    left = db().execute("SELECT remaining_budget FROM ads WHERE id=?", (ad["id"],)).fetchone()[0]
    if left <= 0:
        db().execute("UPDATE ads SET status='COMPLETED' WHERE id=?", (ad["id"],))


def get_ad(ad_id):
    return db().execute("SELECT * FROM ads WHERE id=? AND status='ACTIVE'", (ad_id,)).fetchone()


# ---------------------------------------------------------------- Mini App API
@app.route("/api/bootstrap")
@api
def bootstrap():
    d, s = db(), settings()
    u = d.execute("SELECT * FROM users WHERE id=?", (g.uid,)).fetchone()
    ads = d.execute("SELECT * FROM ads WHERE status='ACTIVE' ORDER BY featured DESC, id DESC").fetchall()
    mine = d.execute("SELECT * FROM ads WHERE owner_id=? ORDER BY id DESC", (g.uid,)).fetchall()
    txs = d.execute("SELECT * FROM tx WHERE user_id=? ORDER BY id DESC LIMIT 50", (g.uid,)).fetchall()
    wds = d.execute("SELECT * FROM withdrawals WHERE user_id=? ORDER BY id DESC LIMIT 20", (g.uid,)).fetchall()
    checkin_ready = day_start() > day_start(u["last_checkin"]) if u["last_checkin"] else True
    return jsonify(
        user=public_user(u), checkin_ready=checkin_ready,
        ads=[public_ad(a) for a in ads],
        campaigns=[dict(a) for a in mine],
        transactions=[dict(t) for t in txs],
        withdrawals=[dict(w) for w in wds],
        settings={k: s[k] for k in s if k != "promo_codes"},
        friends=d.execute("SELECT COUNT(*) FROM users WHERE referrer=?", (g.uid,)).fetchone()[0],
        next_case=u["last_case"] + int(fnum(s["case_cooldown_hours"], 24) * 3600),
        next_wheel=u["last_wheel"] + int(fnum(s["wheel_cooldown_hours"], 6) * 3600),
        prizes={"case": prize_list(s["case_prizes"]), "wheel": prize_list(s["wheel_prizes"])},
        leaderboard=[{"name": r["username"] and "@" + r["username"] or r["name"] or "User", "stars": r["earned_stars"]}
                     for r in d.execute("SELECT username,name,earned_stars FROM users WHERE banned=0"
                                        " ORDER BY earned_stars DESC LIMIT 10")],
        server_time=int(time.time()),
    )


@app.route("/api/ad/browse", methods=["POST"])
@api
def ad_browse():
    ad = get_ad((request.json or {}).get("ad_id"))
    s = settings()
    if not ad or not once_per_cooldown(g.uid, ad["id"], "browse", fnum(s["ad_cooldown_hours"], 24)):
        return jsonify(ok=False, reward=None)
    st, tn = int(fnum(s["browse_stars"])), fnum(s["browse_ton"])
    log_event(g.uid, ad["id"], "browse")
    db().execute("UPDATE ads SET impressions=impressions+1 WHERE id=?", (ad["id"],))
    charge_campaign(ad, ad["cpm"] / 1000.0)
    credit(g.uid, st, tn, "Browsing reward", "BROWSE")
    db().commit()
    return jsonify(ok=True, reward={"stars": st, "ton": tn})


@app.route("/api/ad/click", methods=["POST"])
@api
def ad_click():
    ad = get_ad((request.json or {}).get("ad_id"))
    s = settings()
    if not ad:
        return jsonify(ok=False)
    reward = None
    if once_per_cooldown(g.uid, ad["id"], "click", fnum(s["ad_cooldown_hours"], 24)):
        st, tn = int(fnum(s["click_stars"])), fnum(s["click_ton"])
        log_event(g.uid, ad["id"], "click")
        db().execute("UPDATE ads SET clicks=clicks+1 WHERE id=?", (ad["id"],))
        charge_campaign(ad, ad["cpc"])
        credit(g.uid, st, tn, f"Click: {ad['title']}", "CLICK")
        reward = {"stars": st, "ton": tn}
    db().commit()
    return jsonify(ok=True, reward=reward, url=ad["url"])


@app.route("/api/ad/start", methods=["POST"])
@api
def ad_start():
    ad = get_ad((request.json or {}).get("ad_id"))
    s = settings()
    if not ad:
        return jsonify(error="Ad not available"), 404
    if not once_per_cooldown(g.uid, ad["id"], "watch", fnum(s["ad_cooldown_hours"], 24)):
        return jsonify(error="You already watched this ad. Come back later."), 429
    token = secrets.token_urlsafe(16)
    db().execute("INSERT INTO sessions(token,user_id,ad_id,ts) VALUES(?,?,?,?)",
                 (token, g.uid, ad["id"], int(time.time())))
    db().execute("UPDATE ads SET impressions=impressions+1 WHERE id=?", (ad["id"],))
    charge_campaign(ad, ad["cpm"] / 1000.0)
    db().commit()
    return jsonify(token=token, countdown=int(fnum(s["watch_countdown"], 5)))


@app.route("/api/ad/claim", methods=["POST"])
@api
def ad_claim():
    j = request.json or {}
    s = settings()
    row = db().execute("SELECT * FROM sessions WHERE token=? AND user_id=? AND used=0",
                       (j.get("token", ""), g.uid)).fetchone()
    if not row:
        return jsonify(error="Invalid session"), 400
    if time.time() - row["ts"] < fnum(s["watch_countdown"], 5) - 0.5:
        return jsonify(error="Too early"), 400
    ad = db().execute("SELECT * FROM ads WHERE id=?", (row["ad_id"],)).fetchone()
    db().execute("UPDATE sessions SET used=1 WHERE token=?", (row["token"],))
    log_event(g.uid, row["ad_id"], "watch")
    st, tn = ad["reward_stars"], fnum(s["watch_ton"])
    credit(g.uid, st, tn, f"Reward for ad: {ad['title']}", "AD_REWARD")
    db().execute("UPDATE users SET ads_watched=ads_watched+1 WHERE id=?", (g.uid,))
    db().commit()
    return jsonify(ok=True, reward={"stars": st, "ton": tn})


@app.route("/api/checkin", methods=["POST"])
@api
def checkin():
    s, d = settings(), db()
    u = d.execute("SELECT * FROM users WHERE id=?", (g.uid,)).fetchone()
    today = day_start()
    last = day_start(u["last_checkin"]) if u["last_checkin"] else None
    if last == today:
        return jsonify(error="Already checked in today"), 400
    rewards = [int(fnum(x)) for x in s["checkin_rewards"].split(",") if x.strip()] or [10]
    streak = u["streak"] + 1 if last == today - 1 else 1
    if streak > len(rewards):
        streak = 1
    stars = rewards[streak - 1]
    ton = fnum(s["checkin_ton_bonus"]) if streak == len(rewards) else 0
    credit(g.uid, stars, ton, f"Daily check-in (Day {streak})", "DAILY_CHECKIN")
    d.execute("UPDATE users SET streak=?, last_checkin=? WHERE id=?", (streak, int(time.time()), g.uid))
    d.commit()
    return jsonify(ok=True, streak=streak, reward={"stars": stars, "ton": ton})


@app.route("/api/campaign", methods=["POST"])
@api
def campaign_create():
    s, d, j = settings(), db(), request.json or {}
    if s["allow_user_campaigns"] != "1":
        return jsonify(error="Campaign creation is disabled."), 403
    title, desc, url = (j.get("title") or "").strip(), (j.get("description") or "").strip(), (j.get("url") or "").strip()
    if not title or not desc or not url:
        return jsonify(error="Title, description and link are required."), 400
    cur = "TON" if j.get("currency") == "TON" else "STARS"
    budget = fnum(j.get("budget"))
    if budget <= 0:
        return jsonify(error="Enter a valid budget."), 400
    u = d.execute("SELECT * FROM users WHERE id=?", (g.uid,)).fetchone()
    col = "balance_ton" if cur == "TON" else "balance_stars"
    if u[col] < budget:
        return jsonify(error=f"Insufficient {cur} balance."), 400
    d.execute(f"UPDATE users SET {col}={col}-? WHERE id=?", (budget, g.uid))
    add_tx(g.uid, f"Campaign launch: {title}", budget, cur, False, "CAMPAIGN_CREATION")
    status = "PENDING" if s["require_ad_approval"] == "1" else "ACTIVE"
    d.execute("INSERT INTO ads(title,description,sponsor,url,cta,category,ad_type,currency,total_budget,"
              "remaining_budget,cpc,cpm,reward_stars,status,owner_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (title[:80], desc[:300], (j.get("sponsor") or "")[:40], url[:300], (j.get("cta") or "Open")[:20],
               j.get("category") or "Web3 & Crypto", j.get("ad_type") or "CHANNEL_POST", cur, budget, budget,
               fnum(j.get("cpc"), 1), fnum(j.get("cpm"), 10), int(fnum(j.get("reward_stars"), 10)),
               status, g.uid, int(time.time())))
    d.commit()
    return jsonify(ok=True, status=status)


@app.route("/api/campaign/<int:aid>/toggle", methods=["POST"])
@api
def campaign_toggle(aid):
    a = db().execute("SELECT * FROM ads WHERE id=? AND owner_id=?", (aid, g.uid)).fetchone()
    if not a or a["status"] not in ("ACTIVE", "PAUSED"):
        return jsonify(error="Not allowed"), 400
    db().execute("UPDATE ads SET status=? WHERE id=?", ("PAUSED" if a["status"] == "ACTIVE" else "ACTIVE", aid))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/withdraw", methods=["POST"])
@api
def withdraw():
    s, d, j = settings(), db(), request.json or {}
    cur = "TON" if j.get("currency") == "TON" else "STARS"
    amount = fnum(j.get("amount"))
    method, address = (j.get("method") or "").strip(), (j.get("address") or "").strip()
    if method not in [m.strip() for m in s["withdraw_methods"].split(",")]:
        return jsonify(error="Invalid payout method."), 400
    if not address:
        return jsonify(error="Enter your payout address / username."), 400
    minimum = fnum(s["min_withdraw_ton"]) if cur == "TON" else fnum(s["min_withdraw_stars"])
    if amount < minimum:
        return jsonify(error=f"Minimum withdrawal is {minimum:g} {cur}."), 400
    col = "balance_ton" if cur == "TON" else "balance_stars"
    amount = amount if cur == "TON" else int(amount)
    cur_row = d.execute(f"UPDATE users SET {col}={col}-? WHERE id=? AND {col}>=?", (amount, g.uid, amount))
    if cur_row.rowcount == 0:
        return jsonify(error="Insufficient balance."), 400
    d.execute("INSERT INTO withdrawals(user_id,currency,amount,method,address,created_at) VALUES(?,?,?,?,?,?)",
              (g.uid, cur, amount, method, address[:200], int(time.time())))
    add_tx(g.uid, f"Withdrawal request ({method})", amount, cur, False, "WITHDRAWAL")
    d.commit()
    return jsonify(ok=True)


def prize_list(raw):
    out = []
    for part in raw.split(","):
        try:
            a, w = part.split(":")
            out.append((int(float(a)), float(w)))
        except ValueError:
            pass
    return out or [(1, 1.0)]


def timed_spin(kind):
    """Daily Case / Wheel: weighted prize from admin settings, server-side cooldown."""
    import random
    s, d = settings(), db()
    col = "last_" + kind
    hours = fnum(s[kind + "_cooldown_hours"], 24)
    u = d.execute("SELECT * FROM users WHERE id=?", (g.uid,)).fetchone()
    if time.time() < u[col] + hours * 3600:
        return jsonify(error="Not ready yet. Come back later."), 429
    prizes = prize_list(s[kind + "_prizes"])
    win = random.choices([p[0] for p in prizes], weights=[p[1] for p in prizes])[0]
    d.execute(f"UPDATE users SET {col}=? WHERE id=? AND {col}=?", (int(time.time()), g.uid, u[col]))
    if win:
        credit(g.uid, stars=win, title="Daily Case reward" if kind == "case" else "Wheel reward", typ=kind.upper())
    d.commit()
    return jsonify(ok=True, prize=win, prizes=[p[0] for p in prizes])


@app.route("/api/case/open", methods=["POST"])
@api
def case_open():
    return timed_spin("case")


@app.route("/api/wheel/spin", methods=["POST"])
@api
def wheel_spin():
    return timed_spin("wheel")


@app.route("/api/promo", methods=["POST"])
@api
def promo():
    code = ((request.json or {}).get("code") or "").strip().upper()
    for part in settings()["promo_codes"].split(","):
        bits = part.strip().split(":")
        if len(bits) == 3 and bits[0].upper() == code:
            stars, mx = int(fnum(bits[1])), int(fnum(bits[2]))
            d = db()
            used = d.execute("SELECT COUNT(*) FROM promo_uses WHERE code=?", (code,)).fetchone()[0]
            if mx and used >= mx:
                return jsonify(error="This code has been fully used."), 400
            try:
                d.execute("INSERT INTO promo_uses VALUES(?,?)", (g.uid, code))
            except sqlite3.IntegrityError:
                return jsonify(error="You already used this code."), 400
            credit(g.uid, stars=stars, title=f"Promo code {code}", typ="PROMO")
            d.commit()
            return jsonify(ok=True, stars=stars)
    return jsonify(error="Invalid code."), 404


@app.route("/api/health")
def health():
    return jsonify(ok=True, bot_token_set=bool(BOT_TOKEN), dev_mode=DEV_MODE, origin=ALLOWED_ORIGIN)


# ---------------------------------------------------------------- Admin panel
def admin_required(fn):
    @wraps(fn)
    def w(*a, **kw):
        if not session.get("admin"):
            return redirect(url_for("login", next=request.path))
        return fn(*a, **kw)
    return w


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token
app.jinja_env.filters["dt"] = lambda ts: time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts)) if ts else "—"


@app.before_request
def csrf_protect():
    if request.method == "POST" and request.path.startswith("/admin"):
        if request.path != "/admin/login" and request.form.get("csrf") != session.get("csrf"):
            abort(400, "Bad CSRF token")


@app.route("/")
def home():
    return redirect("/admin")


@app.route("/admin/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ok_u = hmac.compare_digest(request.form.get("username", ""), ADMIN_USER)
        ok_p = hmac.compare_digest(request.form.get("password", ""), ADMIN_PASSWORD)
        if ok_u and ok_p:
            session.clear()
            session["admin"] = True
            return redirect(request.args.get("next") or "/admin")
        time.sleep(1)
        flash("Wrong username or password", "err")
    return render_template("login.html")


@app.route("/admin/logout")
def logout():
    session.clear()
    return redirect("/admin/login")


@app.route("/admin")
@admin_required
def dashboard():
    d = db()
    q = lambda sql, *a: d.execute(sql, a).fetchone()[0] or 0
    day = int(time.time()) - 86400
    stats = {
        "users": q("SELECT COUNT(*) FROM users"),
        "new_today": q("SELECT COUNT(*) FROM users WHERE created_at>?", day),
        "active_today": q("SELECT COUNT(*) FROM users WHERE last_seen>?", day),
        "stars": q("SELECT SUM(balance_stars) FROM users"),
        "ton": round(q("SELECT SUM(balance_ton) FROM users"), 3),
        "pending_wd": q("SELECT COUNT(*) FROM withdrawals WHERE status='PENDING'"),
        "paid_ton": round(q("SELECT SUM(amount) FROM withdrawals WHERE status='PAID' AND currency='TON'"), 3),
        "paid_stars": int(q("SELECT SUM(amount) FROM withdrawals WHERE status='PAID' AND currency='STARS'")),
        "ads_active": q("SELECT COUNT(*) FROM ads WHERE status='ACTIVE'"),
        "ads_pending": q("SELECT COUNT(*) FROM ads WHERE status='PENDING'"),
        "impr": q("SELECT SUM(impressions) FROM ads"), "clicks": q("SELECT SUM(clicks) FROM ads"),
    }
    recent = d.execute("SELECT tx.*, users.username FROM tx LEFT JOIN users ON users.id=tx.user_id "
                       "ORDER BY tx.id DESC LIMIT 12").fetchall()
    return render_template("dashboard.html", s=stats, recent=recent, page="dashboard")


@app.route("/admin/users")
@admin_required
def users():
    q = request.args.get("q", "").strip()
    sql, args = "SELECT * FROM users", []
    if q:
        sql += " WHERE CAST(id AS TEXT) LIKE ? OR username LIKE ? OR name LIKE ?"
        args = [f"%{q}%"] * 3
    rows = db().execute(sql + " ORDER BY last_seen DESC LIMIT 200", args).fetchall()
    return render_template("users.html", rows=rows, q=q, page="users")


@app.route("/admin/users/<int:uid>", methods=["GET", "POST"])
@admin_required
def user_detail(uid):
    d = db()
    u = d.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        abort(404)
    if request.method == "POST":
        f = request.form
        act = f.get("action")
        if act == "adjust":
            st, tn = int(fnum(f.get("stars"))), fnum(f.get("ton"))
            reason = f.get("reason") or "Admin adjustment"
            d.execute("UPDATE users SET balance_stars=MAX(balance_stars+?,0), balance_ton=MAX(balance_ton+?,0) WHERE id=?",
                      (st, tn, uid))
            if st:
                add_tx(uid, reason, abs(st), "STARS", st > 0, "ADMIN")
            if tn:
                add_tx(uid, reason, abs(tn), "TON", tn > 0, "ADMIN")
            flash("Balance updated", "ok")
        elif act == "set":
            d.execute("UPDATE users SET balance_stars=?, balance_ton=?, streak=? WHERE id=?",
                      (int(fnum(f.get("stars"))), fnum(f.get("ton")), int(fnum(f.get("streak"))), uid))
            flash("Values set", "ok")
        elif act == "ban":
            d.execute("UPDATE users SET banned=1-banned WHERE id=?", (uid,))
            flash("Ban status toggled", "ok")
        elif act == "note":
            d.execute("UPDATE users SET note=? WHERE id=?", (f.get("note", ""), uid))
            flash("Note saved", "ok")
        elif act == "reset_checkin":
            d.execute("UPDATE users SET last_checkin=0 WHERE id=?", (uid,))
            flash("Check-in reset: user can claim again today", "ok")
        elif act == "message":
            flash("Message sent" if notify(uid, f.get("text", "")) else "Could not send (BOT_TOKEN missing or user blocked bot)",
                  "ok" if BOT_TOKEN else "err")
        elif act == "delete":
            for t in ("users", "tx", "withdrawals", "events", "sessions"):
                d.execute(f"DELETE FROM {t} WHERE {'id' if t == 'users' else 'user_id'}=?", (uid,))
            d.commit()
            flash("User deleted", "ok")
            return redirect("/admin/users")
        d.commit()
        return redirect(f"/admin/users/{uid}")
    txs = d.execute("SELECT * FROM tx WHERE user_id=? ORDER BY id DESC LIMIT 50", (uid,)).fetchall()
    wds = d.execute("SELECT * FROM withdrawals WHERE user_id=? ORDER BY id DESC", (uid,)).fetchall()
    return render_template("user.html", u=u, txs=txs, wds=wds, page="users")


AD_FIELDS = ("title", "description", "sponsor", "url", "cta", "category", "ad_type", "image_url", "currency", "status")


@app.route("/admin/ads")
@admin_required
def ads():
    st = request.args.get("status", "")
    sql = "SELECT * FROM ads" + (" WHERE status=?" if st else "") + " ORDER BY (status='PENDING') DESC, featured DESC, id DESC"
    rows = db().execute(sql, [st] if st else []).fetchall()
    return render_template("ads.html", rows=rows, st=st, page="ads")


@app.route("/admin/ads/new", methods=["GET", "POST"])
@app.route("/admin/ads/<int:aid>", methods=["GET", "POST"])
@admin_required
def ad_edit(aid=None):
    d = db()
    a = d.execute("SELECT * FROM ads WHERE id=?", (aid,)).fetchone() if aid else None
    if aid and not a:
        abort(404)
    if request.method == "POST":
        f = request.form
        vals = [f.get(k, "").strip() for k in AD_FIELDS]
        nums = [fnum(f.get("total_budget")), fnum(f.get("remaining_budget")), fnum(f.get("cpc")),
                fnum(f.get("cpm")), int(fnum(f.get("reward_stars"), 15)), 1 if f.get("featured") else 0]
        if aid:
            d.execute("UPDATE ads SET " + ",".join(f"{k}=?" for k in AD_FIELDS) +
                      ",total_budget=?,remaining_budget=?,cpc=?,cpm=?,reward_stars=?,featured=? WHERE id=?",
                      vals + nums + [aid])
            if f.get("reset_stats"):
                d.execute("UPDATE ads SET impressions=0, clicks=0 WHERE id=?", (aid,))
        else:
            d.execute("INSERT INTO ads(" + ",".join(AD_FIELDS) +
                      ",total_budget,remaining_budget,cpc,cpm,reward_stars,featured,created_at) VALUES(" +
                      ",".join("?" * (len(AD_FIELDS) + 7)) + ")", vals + nums + [int(time.time())])
        d.commit()
        flash("Ad saved", "ok")
        return redirect("/admin/ads")
    return render_template("ad_form.html", a=a, cats=settings()["categories"].split(","), page="ads")


@app.route("/admin/ads/<int:aid>/<action>", methods=["POST"])
@admin_required
def ad_action(aid, action):
    d = db()
    a = d.execute("SELECT * FROM ads WHERE id=?", (aid,)).fetchone()
    if not a:
        abort(404)
    if action in ("approve", "resume"):
        d.execute("UPDATE ads SET status='ACTIVE' WHERE id=?", (aid,))
        if a["owner_id"] and action == "approve":
            notify(a["owner_id"], f"✅ Your campaign \"{a['title']}\" is approved and live!")
    elif action == "pause":
        d.execute("UPDATE ads SET status='PAUSED' WHERE id=?", (aid,))
    elif action == "feature":
        d.execute("UPDATE ads SET featured=1-featured WHERE id=?", (aid,))
    elif action == "reject":  # refund remaining budget to owner
        if a["owner_id"] and a["remaining_budget"] > 0:
            col = "balance_ton" if a["currency"] == "TON" else "balance_stars"
            d.execute(f"UPDATE users SET {col}={col}+? WHERE id=?", (a["remaining_budget"], a["owner_id"]))
            add_tx(a["owner_id"], f"Refund: {a['title']}", a["remaining_budget"], a["currency"], True, "REFUND")
            notify(a["owner_id"], f"❌ Your campaign \"{a['title']}\" was rejected. Budget refunded.")
        d.execute("UPDATE ads SET status='REJECTED', remaining_budget=0 WHERE id=?", (aid,))
    elif action == "delete":
        d.execute("DELETE FROM ads WHERE id=?", (aid,))
    d.commit()
    return redirect(request.referrer or "/admin/ads")


@app.route("/admin/withdrawals")
@admin_required
def withdrawals():
    st = request.args.get("status", "PENDING")
    rows = db().execute("SELECT w.*, u.username, u.name FROM withdrawals w LEFT JOIN users u ON u.id=w.user_id "
                        + ("WHERE w.status=? " if st != "ALL" else "") + "ORDER BY w.id DESC LIMIT 300",
                        [st] if st != "ALL" else []).fetchall()
    return render_template("withdrawals.html", rows=rows, st=st, page="withdrawals")


@app.route("/admin/withdrawals/<int:wid>/<action>", methods=["POST"])
@admin_required
def withdrawal_action(wid, action):
    d = db()
    w = d.execute("SELECT * FROM withdrawals WHERE id=? AND status='PENDING'", (wid,)).fetchone()
    if not w:
        flash("Already processed", "err")
        return redirect("/admin/withdrawals")
    note = request.form.get("note", "")
    now = int(time.time())
    if action == "pay":
        d.execute("UPDATE withdrawals SET status='PAID', admin_note=?, processed_at=? WHERE id=?", (note, now, wid))
        notify(w["user_id"], f"✅ Withdrawal of {w['amount']:g} {w['currency']} has been paid." + (f"\n{note}" if note else ""))
    else:  # reject -> refund
        col = "balance_ton" if w["currency"] == "TON" else "balance_stars"
        d.execute(f"UPDATE users SET {col}={col}+? WHERE id=?", (w["amount"], w["user_id"]))
        add_tx(w["user_id"], "Withdrawal rejected: refund", w["amount"], w["currency"], True, "REFUND")
        d.execute("UPDATE withdrawals SET status='REJECTED', admin_note=?, processed_at=? WHERE id=?", (note, now, wid))
        notify(w["user_id"], f"❌ Withdrawal of {w['amount']:g} {w['currency']} was rejected and refunded." + (f"\nReason: {note}" if note else ""))
    d.commit()
    flash("Withdrawal updated", "ok")
    return redirect("/admin/withdrawals")


@app.route("/admin/transactions")
@admin_required
def transactions():
    t = request.args.get("type", "")
    rows = db().execute("SELECT tx.*, u.username FROM tx LEFT JOIN users u ON u.id=tx.user_id "
                        + ("WHERE tx.type=? " if t else "") + "ORDER BY tx.id DESC LIMIT 300", [t] if t else []).fetchall()
    return render_template("transactions.html", rows=rows, t=t, page="transactions")


SETTING_GROUPS = [
    ("Games, Friends & Promo", [("bot_link", "Mini App link (https://t.me/YourBot/app)"),
                 ("ref_bonus_stars", "Referral bonus to inviter (Stars)"), ("ref_bonus_new_stars", "Bonus to new invited user (Stars)"),
                 ("case_prizes", "Daily Case prizes  stars:weight,..."), ("case_cooldown_hours", "Daily Case cooldown (hours)"),
                 ("wheel_prizes", "Wheel prizes  stars:weight,..."), ("wheel_cooldown_hours", "Wheel cooldown (hours)"),
                 ("promo_codes", "Promo codes  CODE:stars:max_uses,...")]),
    ("General", [("app_name", "App name"), ("announcement", "Announcement banner (empty = hidden)"),
                 ("maintenance", "Maintenance mode (1 = on)"), ("new_user_bonus_stars", "New-user bonus (Stars)"),
                 ("allow_user_campaigns", "Users can create campaigns (1/0)"),
                 ("require_ad_approval", "Require admin approval for user ads (1/0)"),
                 ("categories", "Ad categories (comma separated)")]),
    ("Rewards", [("browse_stars", "Browse reward: Stars"), ("browse_ton", "Browse reward: TON"),
                 ("click_stars", "Click reward: Stars"), ("click_ton", "Click reward: TON"),
                 ("watch_ton", "Watch-ad bonus: TON (Stars set per ad)"), ("watch_countdown", "Watch countdown (seconds)"),
                 ("ad_cooldown_hours", "Hours before same ad can reward again")]),
    ("Daily check-in", [("checkin_rewards", "Stars per day (comma separated, any length)"),
                        ("checkin_ton_bonus", "TON bonus on the last day")]),
    ("Withdrawals", [("min_withdraw_ton", "Minimum TON"), ("min_withdraw_stars", "Minimum Stars"),
                     ("withdraw_methods", "Payout methods (comma separated)")]),
]


@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def settings_page():
    if request.method == "POST":
        for _, items in SETTING_GROUPS:
            for k, _l in items:
                if k in request.form:
                    db().execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, request.form[k].strip()))
        db().commit()
        flash("Settings saved. The app uses them instantly.", "ok")
        return redirect("/admin/settings")
    return render_template("settings.html", groups=SETTING_GROUPS, v=settings(), page="settings")


@app.route("/admin/broadcast", methods=["GET", "POST"])
@admin_required
def broadcast():
    result = None
    if request.method == "POST":
        text = request.form.get("text", "").strip()
        if text and BOT_TOKEN:
            ids = [r[0] for r in db().execute("SELECT id FROM users WHERE banned=0")]
            ok = sum(1 for i in ids if notify(i, text))
            result = f"Sent to {ok} of {len(ids)} users."
        else:
            result = "Set BOT_TOKEN and write a message first."
    n = db().execute("SELECT COUNT(*) FROM users WHERE banned=0").fetchone()[0]
    return render_template("broadcast.html", result=result, n=n, page="broadcast")


@app.route("/admin/backup")
@admin_required
def backup():
    from flask import send_file
    return send_file(DB_PATH, as_attachment=True, download_name=f"teleads-{time.strftime('%Y%m%d-%H%M')}.db")


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
