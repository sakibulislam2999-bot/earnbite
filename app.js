const tg = window.Telegram?.WebApp; tg?.ready(); tg?.expand();
const API = (window.TELEADS_API || "").replace(/\/$/, "");
const $ = s => document.querySelector(s);
let S = null, tab = "tasks", cat = "All";
const esc = t => String(t ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ton = n => (+n).toFixed(3);

async function api(path, body) {
  try {
    const r = await fetch(API + "/api" + path, {
      method: body ? "POST" : "GET",
      headers: { "Content-Type": "application/json", "X-Init-Data": tg?.initData || "", "X-Ref": tg?.initDataUnsafe?.start_param || "" },
      body: body ? JSON.stringify(body) : undefined
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(j.error || "Request failed"), { status: r.status });
    return j;
  } catch (e) { if (e.status) throw e; throw new Error("Cannot reach server (" + (API || "config.js API url is empty") + "). Check the URL in config.js, HTTPS, and ALLOWED_ORIGIN."); }
}
function toast(m) { const t = $("#toast"); t.textContent = m; t.classList.add("show"); clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove("show"), 2400); }
const rw = r => r ? `+${r.stars}⭐${r.ton ? " +" + r.ton + " TON" : ""}` : "";
function modal(html) { $("#sheet").innerHTML = html; $("#modal").classList.add("show"); }
function closeModal() { $("#modal").classList.remove("show"); }
$("#modal").onclick = e => { if (e.target.id === "modal") closeModal(); };

async function load() {
  try {
    S = await api("/bootstrap");
    $("#appname").textContent = S.settings.app_name;
    const a = $("#ann"); a.textContent = S.settings.announcement; a.style.display = S.settings.announcement ? "block" : "none";
    render();
  } catch (e) { $("#view").innerHTML = `<div class="empty">⚠️ ${esc(e.message)}<p><button onclick="load()">Retry</button></p></div>`; }
}
function render() {
  $("#bal").textContent = `⭐ ${S.user.balance_stars} · 💎 ${ton(S.user.balance_ton)}`;
  document.querySelectorAll("nav a").forEach(a => { a.classList.toggle("on", a.dataset.t === tab || (a.dataset.t === "profile" && ["create", "mine"].includes(tab))); a.onclick = () => { tab = a.dataset.t; render(); scrollTo(0, 0); }; });
  tg?.BackButton?.hide();
  ({ tasks: vFeed, games: vGames, wallet: vWallet, profile: vProfile, create: vCreate, mine: vMine })[tab]();
}

/* ---------- Ads feed ---------- */
function vFeed() {
  const list = S.ads;
  $("#view").innerHTML = `
  <div class="card row"><div class="grow"><b>Daily check-in 🔥</b><div class="meta">Streak: ${S.user.streak} days</div></div>
  <button id="ci" ${S.checkin_ready ? "" : "disabled"}>${S.checkin_ready ? "Claim" : "Claimed"}</button></div>
  <div class="hero" id="gocase"><b>Daily Case</b><span id="cd1" class="meta"></span><button class="o">Open!</button></div>
  <div class="tasks"><h3>Available tasks</h3>${list.map(taskRow).join("") || '<div class="empty">No ads available right now.</div>'}
  <button class="o" style="width:100%;border-style:dashed" id="mk">Create your task!</button></div>`;
  $("#ci").onclick = checkin; $("#mk").onclick = () => { tab = "create"; render(); };
  $("#gocase").onclick = () => { tab = "games"; render(); };
  document.querySelectorAll("[data-watch]").forEach(b => b.onclick = () => watch(+b.dataset.watch));
  document.querySelectorAll("[data-open]").forEach(b => b.onclick = () => openAd(+b.dataset.open));
  tickCd("cd1", S.next_case);
  const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { io.unobserve(e.target); browse(+e.target.dataset.id); } }), { threshold: .8 });
  document.querySelectorAll(".trow").forEach(el => io.observe(el));
}
const taskRow = a => `<div class="trow" data-id="${a.id}"><img src="${esc(a.image_url) || "icon.jpg"}" onerror="this.src='icon.jpg'" alt="">
  <div class="grow"><b>${esc(a.title)}</b><div class="meta">${esc(a.sponsor)} · Reward: +${a.reward_stars} ⭐</div></div>
  <button class="o" data-open="${a.id}">${esc(a.cta)}</button><button class="y" data-watch="${a.id}">Go!</button></div>`;
function tickCd(id, until) {
  const el = $("#" + id); if (!el) return;
  const off = S.server_time - Math.floor(Date.now() / 1000);
  const t = () => { const e = $("#" + id); if (!e) return clearInterval(iv); const left = until - (Math.floor(Date.now() / 1000) + off);
    e.textContent = left > 0 ? new Date(left * 1000).toISOString().substr(11, 8) : "Ready!"; };
  const iv = setInterval(t, 1000); t();
}
const browsed = new Set();
async function browse(id) {
  if (browsed.has(id)) return; browsed.add(id);
  try { const r = await api("/ad/browse", { ad_id: id }); if (r.reward) { toast(rw(r.reward)); refreshBal(); } } catch { }
}
async function refreshBal() { try { const j = await api("/bootstrap"); S = j; $("#bal").textContent = `⭐ ${S.user.balance_stars} · 💎 ${ton(S.user.balance_ton)}`; } catch { } }
async function openAd(id) {
  try { const r = await api("/ad/click", { ad_id: id }); if (r.reward) { toast(rw(r.reward)); refreshBal(); } openLink(r.url); } catch (e) { toast(e.message); }
}
function openLink(u) { if (/^https:\/\/t\.me\//.test(u) && tg?.openTelegramLink) tg.openTelegramLink(u); else if (tg?.openLink) tg.openLink(u); else window.open(u, "_blank"); }

async function watch(id) {
  const ad = S.ads.find(a => a.id === id); let s;
  try { s = await api("/ad/start", { ad_id: id }); } catch (e) { return toast(e.message); }
  let left = s.countdown;
  modal(`<h3>${esc(ad.title)}</h3><p class="meta">${esc(ad.description)}</p><div class="ring"><i id="bar" style="width:0"></i></div>
    <button id="claim" disabled style="width:100%">Wait ${left}s…</button><p style="text-align:center"><a onclick="closeModal()" class="meta">Close</a></p>`);
  requestAnimationFrame(() => $("#bar").style.width = "100%"); $("#bar").style.transitionDuration = s.countdown + "s";
  const iv = setInterval(() => { left--; const b = $("#claim"); if (!b) return clearInterval(iv); if (left > 0) b.textContent = `Wait ${left}s…`; else { clearInterval(iv); b.disabled = false; b.textContent = `Claim +${ad.reward_stars}⭐`; } }, 1000);
  $("#claim").onclick = async () => {
    $("#claim").disabled = true;
    try { const r = await api("/ad/claim", { token: s.token }); closeModal(); toast("🎉 " + rw(r.reward)); tg?.HapticFeedback?.notificationOccurred("success"); await load(); }
    catch (e) { toast(e.message); closeModal(); }
  };
}
/* ---------- Games: Daily Case + Wheel ---------- */
function vGames() {
  const box = (k, title, next, btn) => `<div class="card"><h3 style="margin:0 0 6px">${title}</h3>
    <div class="reel" id="${k}_reel">🎁</div><div class="meta" style="text-align:center">Next free: <span id="${k}_cd"></span></div>
    <button class="y" style="width:100%" id="${k}_go">${btn}</button>
    <div class="prizes">${S.prizes[k].map(p => `<span>${p[0] || "💨"}${p[0] ? "⭐" : ""}<i>${p[1]}</i></span>`).join("")}</div></div>`;
  $("#view").innerHTML = box("case", "📦 Daily Case", S.next_case, "Open free") + box("wheel", "🎡 Wheel of Fortune", S.next_wheel, "Spin free");
  tickCd("case_cd", S.next_case); tickCd("wheel_cd", S.next_wheel);
  $("#case_go").onclick = () => play("case", "/case/open"); $("#wheel_go").onclick = () => play("wheel", "/wheel/spin");
}
async function play(k, path) {
  const reel = $("#" + k + "_reel"); $("#" + k + "_go").disabled = true;
  let r; try { r = await api(path, {}); } catch (e) { $("#" + k + "_go").disabled = false; return toast(e.message); }
  const pool = r.prizes; let n = 0;
  const iv = setInterval(() => { reel.textContent = "⭐ " + pool[Math.floor(Math.random() * pool.length)]; if (++n > 18) { clearInterval(iv);
    reel.textContent = r.prize ? "🎉 +" + r.prize + " ⭐" : "💨 Nothing"; toast(r.prize ? "You won " + r.prize + "⭐" : "Better luck next time");
    tg?.HapticFeedback?.notificationOccurred(r.prize ? "success" : "warning"); setTimeout(load, 1200); } }, 90);
}

/* ---------- Profile: stats, leaderboard, promo, invite ---------- */
function vProfile() {
  const u = S.user, ini = (u.name || u.username || "?").trim().slice(0, 2).toUpperCase();
  const link = S.settings.bot_link ? S.settings.bot_link + "?startapp=" + u.id : "";
  $("#view").innerHTML = `<div class="hero big2"><div class="av">${esc(ini)}</div><b>${u.username ? "@" + esc(u.username) : esc(u.name)}</b></div>
  <div class="streak">🔥 Streak <b>${u.streak} Day${u.streak == 1 ? "" : "s"}</b></div>
  <div class="grid3"><div class="st g1"><span>Total Earnings</span><b>${u.earned_stars} ⭐</b></div><div class="st g2"><span>Ads watched</span><b>${u.ads_watched}</b></div><div class="st g3"><span>Friends invited</span><b>${S.friends}</b></div></div>
  <div class="card"><h3 style="margin:0 0 8px;text-align:center">Leaderboard</h3>${S.leaderboard.map((l, i) => `<div class="tx"><span>${i + 1}. ${esc(l.name)}</span><b>${l.stars} ⭐</b></div>`).join("")}</div>
  <div class="card row"><input id="pc" placeholder="Promo code" class="grow"><button id="pcb">Enter</button></div>
  <button class="blue" id="inv">Invite Friend! (+${S.settings.ref_bonus_stars}⭐ each)</button>
  <div class="row" style="margin-top:10px"><button class="o grow" onclick="tab='create';render()">🚀 Advertiser</button><button class="o grow" onclick="tab='mine';render()">📊 My Ads</button></div>`;
  $("#pcb").onclick = async () => { try { const r = await api("/promo", { code: $("#pc").value }); toast("🎁 +" + r.stars + "⭐"); await load(); } catch (e) { toast(e.message); } };
  $("#inv").onclick = () => { if (!link) return toast("Admin: set 'Mini App link' in Settings"); const u2 = "https://t.me/share/url?url=" + encodeURIComponent(link) + "&text=" + encodeURIComponent("Earn ⭐ with me!"); tg?.openTelegramLink ? tg.openTelegramLink(u2) : window.open(u2); };
}
async function checkin() {
  try { const r = await api("/checkin", {}); toast(`🔥 Day ${r.streak}: ${rw(r.reward)}`); tg?.HapticFeedback?.notificationOccurred("success"); await load(); } catch (e) { toast(e.message); }
}

/* ---------- Create campaign ---------- */
function vCreate() {
  if (S.settings.allow_user_campaigns !== "1") { $("#view").innerHTML = '<div class="empty">Campaign creation is currently disabled.</div>'; return; }
  const cats = S.settings.categories.split(",");
  $("#view").innerHTML = `<div class="card"><h3 style="margin-top:0">🚀 Create campaign</h3>
  <label>Title</label><input id="c_title" maxlength="80"><label>Description</label><textarea id="c_desc" maxlength="300"></textarea>
  <label>Your @channel / bot</label><input id="c_sp" value="@"><label>Target link</label><input id="c_url" value="https://t.me/">
  <label>Button text</label><input id="c_cta" value="Open"><label>Category</label><select id="c_cat">${cats.map(c => `<option>${esc(c)}</option>`).join("")}</select>
  <div class="row"><div class="grow"><label>Budget</label><input id="c_bud" type="number" value="150"></div><div class="grow"><label>Currency</label><select id="c_cur"><option>STARS</option><option>TON</option></select></div></div>
  <div class="row"><div class="grow"><label>Cost / click</label><input id="c_cpc" type="number" step="any" value="2"></div><div class="grow"><label>Reward to viewers (⭐)</label><input id="c_rw" type="number" value="10"></div></div>
  <p class="meta">${S.settings.require_ad_approval === "1" ? "Your ad will go live after admin approval." : "Your ad goes live immediately."}</p>
  <button id="c_go" style="width:100%">Launch campaign</button></div>`;
  $("#c_go").onclick = async () => {
    try {
      const r = await api("/campaign", { title: $("#c_title").value, description: $("#c_desc").value, sponsor: $("#c_sp").value, url: $("#c_url").value, cta: $("#c_cta").value, category: $("#c_cat").value, budget: $("#c_bud").value, currency: $("#c_cur").value, cpc: $("#c_cpc").value, reward_stars: $("#c_rw").value });
      toast(r.status === "PENDING" ? "Submitted for approval ✅" : "Campaign live 🚀"); tab = "mine"; await load();
    } catch (e) { toast(e.message); }
  };
}

/* ---------- My campaigns ---------- */
function vMine() {
  $("#view").innerHTML = S.campaigns.map(c => `<div class="card"><div class="row"><b class="grow">${esc(c.title)}</b><span class="tag">${c.status}</span></div>
    <div class="meta">Budget left: ${(+c.remaining_budget).toFixed(2)} / ${c.total_budget} ${c.currency}</div>
    <div class="row"><span>👁 ${c.impressions}</span><span>👆 ${c.clicks}</span><span>CTR ${c.impressions ? (c.clicks / c.impressions * 100).toFixed(1) : 0}%</span>
    ${["ACTIVE", "PAUSED"].includes(c.status) ? `<button class="o" style="margin-left:auto" data-tg="${c.id}">${c.status === "ACTIVE" ? "Pause" : "Resume"}</button>` : ""}</div></div>`).join("")
    || '<div class="empty">No campaigns yet.<p><button onclick="tab=\'create\';render()">Create one</button></p></div>';
  document.querySelectorAll("[data-tg]").forEach(b => b.onclick = async () => { try { await api(`/campaign/${b.dataset.tg}/toggle`, {}); await load(); } catch (e) { toast(e.message); } });
}

/* ---------- Wallet ---------- */
function vWallet() {
  const u = S.user;
  $("#view").innerHTML = `<div class="card"><div class="meta">${esc(u.name)} ${u.username ? "@" + esc(u.username) : ""}</div>
  <div class="big">⭐ ${u.balance_stars}</div><div class="big" style="font-size:22px">💎 ${ton(u.balance_ton)} TON</div>
  <div class="meta">Earned total: ${u.earned_stars}⭐ · ${ton(u.earned_ton)} TON · ${u.ads_watched} ads watched</div>
  <p><button id="wd" style="width:100%">Withdraw</button></p></div>
  ${S.withdrawals.length ? `<div class="card"><b>Withdrawals</b>${S.withdrawals.map(w => `<div class="tx"><span>${w.amount} ${w.currency} → ${esc(w.method)}</span><span class="tag">${w.status}</span></div>${w.admin_note ? `<div class="meta">${esc(w.admin_note)}</div>` : ""}`).join("")}</div>` : ""}
  <div class="card"><b>History</b>${S.transactions.map(t => `<div class="tx"><span>${esc(t.title)}</span><span class="${t.credit ? "pos" : "neg"}">${t.credit ? "+" : "-"}${t.amount} ${t.currency === "STARS" ? "⭐" : "TON"}</span></div>`).join("") || '<div class="empty">No transactions yet</div>'}</div>`;
  $("#wd").onclick = withdrawModal;
}
function withdrawModal() {
  const s = S.settings, methods = s.withdraw_methods.split(",").map(m => m.trim());
  modal(`<h3 style="margin-top:0">Withdraw</h3><label>Currency</label><select id="w_cur"><option>TON</option><option>STARS</option></select>
  <label>Method</label><select id="w_m">${methods.map(m => `<option>${esc(m)}</option>`).join("")}</select>
  <label>Address / @username</label><input id="w_a" value="${S.user.username ? "@" + esc(S.user.username) : ""}">
  <label>Amount <span id="w_min" class="meta"></span></label><input id="w_amt" type="number" step="any">
  <p><button id="w_go" style="width:100%">Request withdrawal</button></p>`);
  const upd = () => { const c = $("#w_cur").value; $("#w_min").textContent = `(min ${c === "TON" ? s.min_withdraw_ton : s.min_withdraw_stars}, balance ${c === "TON" ? ton(S.user.balance_ton) : S.user.balance_stars})`; };
  $("#w_cur").onchange = upd; upd();
  $("#w_go").onclick = async () => {
    try { await api("/withdraw", { currency: $("#w_cur").value, method: $("#w_m").value, address: $("#w_a").value, amount: $("#w_amt").value }); closeModal(); toast("Request sent ✅ Pending review"); await load(); }
    catch (e) { toast(e.message); }
  };
}
load();
