"""A single self-contained HTML page: browse the collection, and build a deck.

Art is referenced from Scryfall's CDN rather than copied into the repo -- 1,775
crops would be roughly 90 MB of duplicated binary in git. `--offline-art` downloads
them into build/art/ for use without a connection.

The workbench cannot write to disk from a browser tab, so finishing a deck hands
back two things: a JSON file to drop into data/decks/, and the exact `mtg deck add`
commands. Nothing is applied behind your back.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Dict, List

from . import db, decks, paths, search, value, vocab

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  --bg:#f6f5f3; --panel:#ffffff; --ink:#1b1a18; --muted:#6d6a64;
  --line:#e2dfd9; --accent:#7c4dff; --ok:#18794e; --warn:#9a6700; --bad:#b42318;
  --chip:#efece7;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg:#16151a; --panel:#1e1d24; --ink:#ece9f0; --muted:#9d99a6;
    --line:#2f2d38; --accent:#a78bfa; --ok:#4ade80; --warn:#fbbf24; --bad:#f87171;
    --chip:#2a2831;
  }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; }
header { position:sticky; top:0; z-index:20; background:var(--panel);
  border-bottom:1px solid var(--line); padding:12px 18px; }
h1 { margin:0 0 2px; font-size:17px; letter-spacing:-.01em; }
.sub { color:var(--muted); font-size:12px; }
.bar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:10px; }
input[type=search], select { background:var(--bg); color:var(--ink);
  border:1px solid var(--line); border-radius:7px; padding:6px 9px; font:inherit; }
input[type=search] { min-width:240px; flex:1; }
button { background:var(--chip); color:var(--ink); border:1px solid var(--line);
  border-radius:7px; padding:6px 11px; font:inherit; cursor:pointer; }
button:hover { border-color:var(--accent); }
button.on { background:var(--accent); color:#fff; border-color:var(--accent); }
.facets { display:none; gap:14px; flex-wrap:wrap; margin-top:10px;
  border-top:1px solid var(--line); padding-top:10px; }
.facets.show { display:flex; }
.facet { min-width:150px; }
.facet h4 { margin:0 0 5px; font-size:11px; text-transform:uppercase;
  letter-spacing:.06em; color:var(--muted); }
.tags { display:flex; flex-wrap:wrap; gap:4px; }
.tag { font-size:11px; padding:2px 7px; border-radius:99px; background:var(--chip);
  border:1px solid transparent; cursor:pointer; }
.tag.on { background:var(--accent); color:#fff; }
.tag .n { opacity:.55; margin-left:3px; }
main { padding:16px 18px 60px; }
.grid { display:grid; gap:12px;
  grid-template-columns:repeat(auto-fill,minmax(210px,1fr)); }
.card { background:var(--panel); border:1px solid var(--line); border-radius:10px;
  overflow:hidden; display:flex; flex-direction:column; }
.card img { width:100%; aspect-ratio:626/457; object-fit:cover; display:block;
  background:var(--chip); }
.card .body { padding:8px 9px 9px; display:flex; flex-direction:column; gap:4px;
  flex:1; }
.nm { font-weight:600; font-size:13px; line-height:1.25; }
.meta { color:var(--muted); font-size:11px; }
.desc { font-size:11px; color:var(--muted); font-style:italic; }
.row { display:flex; gap:5px; align-items:center; flex-wrap:wrap; margin-top:auto;
  padding-top:5px; }
.pill { font-size:10px; padding:1px 6px; border-radius:99px; background:var(--chip); }
.pill.ok { color:var(--ok); } .pill.warn { color:var(--warn); }
.pill.bad { color:var(--bad); } .pill.proxy { color:var(--accent); }
.add { margin-left:auto; font-size:11px; padding:3px 8px; }
.empty { color:var(--muted); padding:40px; text-align:center; }
aside { position:fixed; right:0; top:0; bottom:0; width:340px; background:var(--panel);
  border-left:1px solid var(--line); padding:14px; overflow:auto; z-index:30;
  transform:translateX(100%); transition:transform .18s; }
aside.open { transform:none; }
aside h3 { margin:0 0 8px; font-size:14px; }
.slot { display:flex; gap:6px; align-items:baseline; padding:3px 0;
  border-bottom:1px solid var(--line); font-size:12px; }
.slot .x { margin-left:auto; cursor:pointer; color:var(--muted); }
.slot .x:hover { color:var(--bad); }
pre { background:var(--bg); border:1px solid var(--line); border-radius:7px;
  padding:8px; font-size:11px; overflow:auto; max-height:210px; white-space:pre-wrap; }
.warnbox { border:1px solid var(--warn); border-radius:7px; padding:8px;
  font-size:12px; color:var(--warn); margin:8px 0; }
.curve { display:flex; gap:2px; align-items:flex-end; height:44px; margin:8px 0; }
.curve div { flex:1; background:var(--accent); border-radius:2px 2px 0 0; min-height:2px; }
.curve span { font-size:9px; color:var(--muted); display:block; text-align:center; }
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <div class="sub">__SUBTITLE__</div>
  <div class="bar">
    <input type="search" id="q" placeholder="search name, type, or what the art shows...">
    <select id="colors">
      <option value="">any colours</option>
      __COLOR_OPTIONS__
    </select>
    <button id="availOnly">available only</button>
    <button id="hideProxy">hide proxies</button>
    <button id="facetToggle">art tags</button>
    <button id="deckBtn">deck workbench</button>
    <span class="sub" id="count"></span>
  </div>
  <div class="facets" id="facets"></div>
</header>
<main><div class="grid" id="grid"></div><div class="empty" id="empty" hidden></div></main>
<aside id="panel">
  <button style="float:right" onclick="closePanel()">close</button>
  <h3>Deck workbench</h3>
  <div>
    <select id="deckPick"></select>
    <input type="search" id="deckName" placeholder="or a new deck name" style="margin-top:6px;width:100%">
  </div>
  <div id="deckWarn"></div>
  <div class="curve" id="curve"></div>
  <div id="slots"></div>
  <p class="sub" id="deckCount"></p>
  <button onclick="downloadDeck()">download deck JSON</button>
  <button onclick="showCommands()">show mtg commands</button>
  <pre id="cmds" hidden></pre>
  <p class="sub">Drop the JSON into <code>data/decks/</code>, then run
     <code>./mtg check</code>. Nothing here changes your files on its own.</p>
</aside>
<script>
const CARDS = __CARDS__;
const FACETS = __FACETS__;
const DECKS = __DECKS__;
const CLAIMS = __CLAIMS__;

let filters = { q:"", colors:"", avail:false, noProxy:false, tags:new Set() };
let deck = { id:"", name:"", cards:[] };

const el = id => document.getElementById(id);
const norm = s => (s||"").toLowerCase();

function claimedCount(oracle, excludeDeck) {
  let n = 0;
  for (const [deckName, entries] of Object.entries(CLAIMS)) {
    if (deckName === excludeDeck) continue;
    n += entries[oracle] || 0;
  }
  for (const c of deck.cards) if (c.oracle_id === oracle && c.status !== "proxy") n += c.quantity;
  return n;
}
function availableNow(c) { return Math.max(0, c.owned - claimedCount(c.oracle_id, deck.name)); }

function matches(c) {
  if (filters.noProxy && c.owned === 0 && c.proxies > 0) return false;
  if (filters.colors) {
    const allowed = filters.colors.split("");
    for (const ch of (c.color_identity||"").split(",").filter(Boolean))
      if (!allowed.includes(ch)) return false;
  }
  if (filters.avail && availableNow(c) <= 0) return false;
  for (const t of filters.tags) if (!c.tags.includes(t)) return false;
  if (filters.q) {
    const hay = norm(c.name+" "+c.type_line+" "+c.description+" "+c.tags.join(" ")+" "+c.artist);
    if (!hay.includes(norm(filters.q))) return false;
  }
  return true;
}

function cardNode(c) {
  const avail = availableNow(c);
  const node = document.createElement("div");
  node.className = "card";
  const where = (c.binders||[]).join(", ");
  const pills = [];
  if (avail > 0) pills.push(`<span class="pill ok">${avail} available</span>`);
  else if (c.owned > 0) pills.push(`<span class="pill warn">in use</span>`);
  else pills.push(`<span class="pill bad">not owned</span>`);
  if (c.proxies) pills.push(`<span class="pill proxy">${c.proxies} proxy</span>`);
  if (c.price_eur) pills.push(`<span class="pill">€${c.price_eur.toFixed(2)}</span>`);
  node.innerHTML = `
    <img loading="lazy" src="${c.art}" alt="">
    <div class="body">
      <div class="nm">${c.name}</div>
      <div class="meta">${c.type_line}</div>
      <div class="meta">${(c.set_code||"").toUpperCase()} ${c.collector_number} · ${c.artist||""}</div>
      ${c.description ? `<div class="desc">${c.description}</div>` : ""}
      <div class="meta">${where}</div>
      <div class="row">${pills.join("")}<button class="add">add</button></div>
    </div>`;
  node.querySelector(".add").onclick = () => addToDeck(c);
  return node;
}

function render() {
  const hits = CARDS.filter(matches);
  const grid = el("grid");
  grid.innerHTML = "";
  const frag = document.createDocumentFragment();
  hits.slice(0, 400).forEach(c => frag.appendChild(cardNode(c)));
  grid.appendChild(frag);
  el("count").textContent = hits.length + " cards" + (hits.length > 400 ? " (showing 400)" : "");
  el("empty").hidden = hits.length > 0;
  el("empty").textContent = "Nothing matches those filters.";
}

function buildFacets() {
  const box = el("facets");
  for (const [facet, tags] of Object.entries(FACETS)) {
    const counts = {};
    CARDS.forEach(c => c.tags.forEach(t => { if (tags.includes(t)) counts[t] = (counts[t]||0)+1; }));
    const present = tags.filter(t => counts[t]);
    if (!present.length) continue;
    const div = document.createElement("div");
    div.className = "facet";
    div.innerHTML = `<h4>${facet}</h4><div class="tags">` +
      present.map(t => `<span class="tag" data-t="${t}">${t}<span class="n">${counts[t]}</span></span>`).join("") +
      `</div>`;
    box.appendChild(div);
  }
  box.querySelectorAll(".tag").forEach(node => {
    node.onclick = () => {
      const tag = node.dataset.t;
      if (filters.tags.has(tag)) { filters.tags.delete(tag); node.classList.remove("on"); }
      else { filters.tags.add(tag); node.classList.add("on"); }
      render();
    };
  });
}

// ---- workbench ----
function addToDeck(c) {
  const existing = deck.cards.find(x => x.scryfall_id === c.scryfall_id);
  if (existing) existing.quantity += 1;
  else deck.cards.push({ scryfall_id:c.scryfall_id, oracle_id:c.oracle_id, name:c.name,
                         set:c.set_code, number:c.collector_number, quantity:1,
                         role:"", status: c.owned > 0 ? "assigned" : (c.proxies ? "proxy" : "wanted") });
  openPanel(); renderDeck(); render();
}
function removeSlot(i) { deck.cards.splice(i,1); renderDeck(); render(); }

function renderDeck() {
  deck.name = el("deckName").value || el("deckPick").value || "New Deck";
  const warn = [];
  const counts = {};
  deck.cards.forEach(c => { if (c.status !== "proxy") counts[c.oracle_id] = (counts[c.oracle_id]||0) + c.quantity; });
  for (const [oracle, want] of Object.entries(counts)) {
    const card = CARDS.find(c => c.oracle_id === oracle);
    if (!card) continue;
    let elsewhere = 0;
    for (const [deckName, entries] of Object.entries(CLAIMS))
      if (deckName !== deck.name) elsewhere += entries[oracle] || 0;
    if (want + elsewhere > card.owned) {
      warn.push(`${card.name}: you own ${card.owned}, but ${want + elsewhere} copies are claimed`
        + (elsewhere ? ` (${elsewhere} already in another deck)` : "")
        + (card.proxies ? ` — you have ${card.proxies} proxy` : ""));
    }
  }
  el("deckWarn").innerHTML = warn.length
    ? `<div class="warnbox"><b>Contested</b><br>${warn.join("<br>")}</div>` : "";

  el("slots").innerHTML = deck.cards.map((c,i) =>
    `<div class="slot"><span>${c.quantity}x</span><span>${c.name}</span>
       <span class="meta">(${(c.set||"").toUpperCase()} ${c.number})</span>
       <span class="x" onclick="removeSlot(${i})">remove</span></div>`).join("");
  el("deckCount").textContent = deck.cards.reduce((n,c)=>n+c.quantity,0) + " cards";

  const buckets = new Array(8).fill(0);
  deck.cards.forEach(c => {
    const card = CARDS.find(x => x.scryfall_id === c.scryfall_id);
    if (card && !/Land/.test(card.type_line)) buckets[Math.min(7, Math.round(card.cmc||0))] += c.quantity;
  });
  const peak = Math.max(1, ...buckets);
  el("curve").innerHTML = buckets.map((n,i) =>
    `<div style="height:${n/peak*100}%" title="${n} at ${i}"><span>${i}</span></div>`).join("");
}

function downloadDeck() {
  const id = (deck.name||"deck").toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-|-$/g,"");
  const blob = new Blob([JSON.stringify({ deck_id:id, name:deck.name, format:"commander",
    commander_scryfall_id:null, manabox_binder:deck.name, status:"building",
    notes:"Built in the gallery workbench.", cards:deck.cards }, null, 2)],
    {type:"application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = id + ".json"; a.click();
}
function showCommands() {
  const id = (deck.name||"deck").toLowerCase().replace(/[^a-z0-9]+/g,"-");
  const lines = [`./mtg deck new "${deck.name}"`];
  deck.cards.forEach(c => lines.push(
    `./mtg deck add ${id} "${c.name}" --set ${c.set} --number ${c.number} --qty ${c.quantity}`));
  const box = el("cmds"); box.hidden = false; box.textContent = lines.join("\\n");
}
function openPanel(){ el("panel").classList.add("open"); }
function closePanel(){ el("panel").classList.remove("open"); }

el("q").oninput = e => { filters.q = e.target.value; render(); };
el("colors").onchange = e => { filters.colors = e.target.value; render(); };
el("availOnly").onclick = e => { filters.avail = !filters.avail; e.target.classList.toggle("on"); render(); };
el("hideProxy").onclick = e => { filters.noProxy = !filters.noProxy; e.target.classList.toggle("on"); render(); };
el("facetToggle").onclick = () => el("facets").classList.toggle("show");
el("deckBtn").onclick = () => { openPanel(); renderDeck(); };
el("deckName").oninput = () => renderDeck();
el("deckPick").onchange = e => {
  const found = DECKS.find(d => d.name === e.target.value);
  deck = found ? { id:found.deck_id, name:found.name, cards:JSON.parse(JSON.stringify(found.cards)) }
               : { id:"", name:"", cards:[] };
  el("deckName").value = "";
  renderDeck(); render();
};

el("deckPick").innerHTML = `<option value="">start an empty deck</option>` +
  DECKS.map(d => `<option value="${d.name}">${d.name} (${d.total} cards)</option>`).join("");
buildFacets();
render();
</script>
</body>
</html>
"""

COLORS = [("W", "white"), ("U", "blue"), ("B", "black"), ("R", "red"), ("G", "green"),
          ("WU", "azorius"), ("UB", "dimir"), ("BR", "rakdos"), ("RG", "gruul"),
          ("WG", "selesnya"), ("WB", "orzhov"), ("UR", "izzet"), ("BG", "golgari"),
          ("WR", "boros"), ("UG", "simic"), ("WUBRG", "five colour")]


def download_art(hits: List[search.Hit], log=print) -> Dict[str, str]:
    os.makedirs(paths.ART_THUMBS, exist_ok=True)
    mapping = {}
    for index, hit in enumerate(hits, 1):
        if not hit.art_crop_url or not hit.illustration_id:
            continue
        local = os.path.join(paths.ART_THUMBS, hit.illustration_id + ".jpg")
        if not os.path.exists(local):
            try:
                request = urllib.request.Request(
                    hit.art_crop_url,
                    headers={"User-Agent": "MagicTheBenalliing/0.1"})
                with urllib.request.urlopen(request, timeout=30) as response:
                    with open(local, "wb") as fh:
                        fh.write(response.read())
            except Exception as exc:  # noqa: BLE001
                log("  could not fetch art for {}: {}".format(hit.name, exc))
                continue
        if index % 200 == 0:
            log("  {}/{} images".format(index, len(hits)))
        mapping[hit.scryfall_id] = os.path.join("art", hit.illustration_id + ".jpg")
    return mapping


def build(conn, out_path: str = None, offline_art: bool = False, log=print) -> str:
    paths.ensure_dirs()
    out_path = out_path or os.path.join(paths.BUILD, "gallery.html")

    hits = search.find(conn)
    local = download_art(hits, log=log) if offline_art else {}

    cards = []
    for hit in hits:
        cards.append({
            "scryfall_id": hit.scryfall_id, "oracle_id": hit.oracle_id,
            "name": hit.name, "set_code": hit.set_code,
            "collector_number": hit.collector_number, "type_line": hit.type_line,
            "cmc": hit.cmc, "color_identity": hit.color_identity,
            "artist": hit.artist, "price_eur": hit.price_eur,
            "owned": hit.owned, "proxies": hit.proxies, "binders": hit.binders,
            "tags": hit.tags, "description": hit.description,
            "art": local.get(hit.scryfall_id, hit.art_crop_url),
        })

    deck_list, claims = [], {}
    for deck in decks.load_all():
        deck_list.append({
            "deck_id": deck.deck_id, "name": deck.name, "total": deck.total_cards,
            "cards": [c.to_json() for c in deck.cards],
        })
        entry = {}
        for card in deck.cards:
            if card.status in decks.CONSUMING:
                entry[card.oracle_id] = entry.get(card.oracle_id, 0) + card.quantity
        claims[deck.name] = entry

    valuation = value.value_collection(conn)
    tagged = conn.execute("SELECT COUNT(*) AS n FROM art_notes").fetchone()["n"]
    total_art = conn.execute(
        "SELECT COUNT(DISTINCT illustration_id) AS n FROM card_faces").fetchone()["n"]

    subtitle = ("{:,} real cards worth EUR {:,.2f} &middot; {:,} proxies excluded "
                "&middot; {:,} wishlist cards ignored &middot; {}/{} artworks tagged"
                ).format(valuation.real.cards, valuation.real.value,
                         valuation.proxy.cards, valuation.wishlist.cards,
                         tagged, total_art)

    html = (PAGE
            .replace("__TITLE__", "MagicTheBenalliing")
            .replace("__SUBTITLE__", subtitle)
            .replace("__COLOR_OPTIONS__", "".join(
                '<option value="{}">{}</option>'.format(code, label)
                for code, label in COLORS))
            .replace("__CARDS__", json.dumps(cards, ensure_ascii=False))
            .replace("__FACETS__", json.dumps(vocab.FACETS))
            .replace("__DECKS__", json.dumps(deck_list, ensure_ascii=False))
            .replace("__CLAIMS__", json.dumps(claims, ensure_ascii=False)))

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return out_path
