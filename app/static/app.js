// ChurchFocus v2 chat client (REDESIGN §9). Talks only to /api/chat, /api/state, /api/churches, /api/know_more, /api/memory, /api/questions, /api/deep.
(() => {
  const $ = (s) => document.querySelector(s);
  let sid = null; try { sid = localStorage.getItem("cs_sid"); } catch (e) {}
  let polling = false, maxPages = 1, locationText = null;
  let lastN = 0, page = 1, tableVersion = -1, memoryVersion = -1, busy = false;
  const picked = new Set();
  let research = {}, jobs = [], churchRequest = 0, locationRadius = null, hasOrigin = false; 
  const running = j => ["running", "pending", "queued"].includes(j.status);
  const safeURL = u => /^(https?:\/\/|\/reports\/[a-zA-Z0-9_-]+$)/.test(u || "");
  const sourceLink = (u, label = "source") => safeURL(u) ? `<a href="${esc(u)}" target="_blank" rel="noopener nofollow">${esc(label)}</a>` : "";

  const thread = $("#thread"), input = $("#input"), sendBtn = $("#send");
  const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const linkify = (t) => String(t ?? "").split(/(https?:\/\/[^\s<>"']+)/g).map(part => /^https?:\/\//.test(part) ? `<a href="${esc(part)}" target="_blank" rel="noopener nofollow">source</a>` : esc(part)).join("");
  const nearBottom = () => thread.scrollHeight - thread.scrollTop - thread.clientHeight < 100;
  const scroll = () => { thread.scrollTop = thread.scrollHeight; };

  function clearOptions() { thread.querySelectorAll(".options").forEach((o) => o.remove()); }
  function addMessage(m) {
    const follow = nearBottom();
    if (m.role === "user" && thread.querySelector(`[data-local="${CSS.escape(m.text)}"]`)) { thread.querySelector(`[data-local="${CSS.escape(m.text)}"]`).removeAttribute("data-local"); return; }
    const d = document.createElement("div");
    d.className = "msg " + (m.role || "bot");
    d.innerHTML = linkify(m.text);
    thread.appendChild(d);
    if (m.meta && m.meta.report_url && /^\/reports\/[a-zA-Z0-9_-]+$/.test(m.meta.report_url)) {
      const a = document.createElement("a"); a.href = m.meta.report_url; a.textContent = "Open full report"; a.target = "_blank"; a.rel = "noopener"; d.appendChild(document.createElement("br")); d.appendChild(a);
    }
    if (m.meta && m.meta.options && m.meta.options.length) {
      const o = document.createElement("div"); o.className = "options";
      m.meta.options.forEach((label) => { const b = document.createElement("button"); b.textContent = label; b.onclick = () => label === "Start a new chat" ? restart("new") : label === "Change this search here" ? restart("continue") : send(label); o.appendChild(b); });
      thread.appendChild(o);
    }
    if (follow) scroll();
  }
  function typing(on) {
    let t = thread.querySelector(".typing");
    const follow = nearBottom();
    if (on && !t) { t = document.createElement("div"); t.className = "typing"; t.innerHTML = "<span>●</span><span>●</span><span>●</span>"; thread.appendChild(t); if (follow) scroll(); }
    if (!on && t) t.remove();
  }

  async function send(text) {
    text = (text ?? input.value).trim();
    if (!text || busy) return;
    busy = true; sendBtn.disabled = true; clearOptions();
    const mine = document.createElement("div"); mine.className = "msg user"; mine.textContent = text; mine.dataset.local = text; thread.appendChild(mine);
    input.value = ""; autosize(); scroll(); typing(true);
    try {
      const r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: sid, text }) });
      if (!r.ok) throw new Error("Chat request failed");
      const d = await r.json();
      if (d.session_id && d.session_id !== sid) { if (sid) { thread.innerHTML = ""; lastN = 0; churchRequest++; tableVersion = -1; memoryVersion = -1; locationText = null; locationRadius = null; picked.clear(); hasOrigin = false; jobs = []; renderJobs([]); setResearch({}); } sid = d.session_id; try { localStorage.setItem("cs_sid", sid); } catch (e) {} }
      await poll();
    } catch (e) { addMessage({ role: "system", text: "Something went wrong sending that. Please try again." }); }
    finally { typing(false); busy = false; sendBtn.disabled = false; input.focus(); }
  }

  async function poll() {
    if (!sid || polling) return;
    polling = true;
    try {
      const requestSid = sid;
      const r = await fetch(`/api/state/${sid}?since=${lastN}`); if (!r.ok || requestSid !== sid) return;
      const d = await r.json(); if (requestSid !== sid) return;
      (d.messages || []).forEach((m) => { addMessage(m); lastN = Math.max(lastN, m.n); });
      if (d.location && (d.location.text !== locationText || d.location.limit_miles !== locationRadius)) {
        const originChanged = d.location.text !== locationText;
        locationText = d.location.text; locationRadius = d.location.limit_miles;
        const radius = String(d.location.limit_miles || 15);
        if (![...$("#radius").options].some(o => o.value === radius)) $("#radius").add(new Option(radius, radius));
        $("#radius").value = radius; page = 1; tableVersion = -1; if (originChanged) picked.clear();
      }
      hasOrigin = !!d.location;
      if (!hasOrigin) { locationText = null; locationRadius = null; }
      setResearch(d.research || {});
      jobs = d.jobs || []; renderJobs(jobs);
      if (document.activeElement.tagName !== "BUTTON") await loadQuestions();
      if (d.table_version !== tableVersion) { tableVersion = d.table_version; loadChurches(); }
      if (d.memory_version !== memoryVersion) { memoryVersion = d.memory_version; loadAbout(); loadQuestions(); }
    } catch (e) {} finally { polling = false; }
  }

  function updateFocus() {
    let stage = hasOrigin ? 1 : 0;
    for (const job of jobs) {
      if (job.kind === "medium" && running(job)) stage = Math.max(stage,2);
      if (job.kind === "medium" && job.status === "complete") stage = Math.max(stage,3);
      if (job.kind === "deep" && (running(job) || job.report_url)) stage = Math.max(stage,4);
      if (job.kind === "deep" && job.status === "complete" && !/partial|stopped early|budget/i.test(job.label || "")) stage = 5;
    }
    const chat = $(".chat"); chat.dataset.focusStage = String(stage);
    chat.style.setProperty("--scene-blur", `${[18,13,9,6,3,0][stage]}px`);
  }

  function draftKey() { return `cs_selection:${sid}:${research.generation || 0}`; }
  function saveDraft() { try { sessionStorage.setItem(draftKey(), JSON.stringify([...picked])); } catch(e) {} }
  function setResearch(state) {
    const entering = state.selection_mode && (!research.selection_mode || state.generation !== research.generation);
    research = state;
    if (entering) {
      picked.clear(); let draft = state.selected || [];
      try { const saved = JSON.parse(sessionStorage.getItem(draftKey()) || "null"); if (Array.isArray(saved)) draft = saved; } catch(e) {}
      draft.filter(id => typeof id === "string").slice(0,5).forEach(id => picked.add(id));
    }
    $("#selection").classList.toggle("hidden", !state.selection_mode);
    $("#knowmore").classList.toggle("hidden", !!state.selection_mode);
    $("#submitselection").disabled = picked.size < 1 || picked.size > 5;
    $("#selectioncount").textContent = `${picked.size} selected (choose 1–5)`;
    $("#knowmore").disabled = !sid;
  }

  async function postAction(url, body) {
    const r = await fetch(url, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    if (!r.ok) { let message = "That action could not be completed. Please try again."; try { const d = await r.json(); if (typeof d.detail === "string") message = d.detail; else if (typeof d.error === "string") message = d.error; } catch(e) {} throw new Error(message); }
    return r.json();
  }
  function actionError(e) { addMessage({role:"system",text:e.message || "Please try again."}); }

  function renderJobs(items) {
    jobs = items; updateFocus();
    const latest = new Map(); items.forEach(j => latest.set(`${j.church_id}:${j.kind}`, j));
    $("#jobs").innerHTML = [...latest.values()].filter(j => j.kind === "deep" || running(j)).map(j => {
      const active = running(j), count = j.total ? ` · ${j.done || 0} of ${j.total}` : "";
      const elapsed = active && j.elapsed_seconds ? ` · ${Math.floor(j.elapsed_seconds / 60)}m ${Math.floor(j.elapsed_seconds % 60)}s` : "";
      const partial = /partial|stopped early|budget/i.test(j.label || "");
      const state = active ? (j.kind === "deep" ? "Deep dive running" : "Reading website") : j.status === "complete" ? (partial ? "Partial report ready" : "Report ready") : j.status === "cancelled" ? "Stopped" : "Stopped early";
      return `<div class="job ${active ? "active" : "terminal"}" role="status"><strong>${esc(j.name)} · ${state}</strong><div>${esc(j.label)}${count}${elapsed}</div>${j.report_url ? sourceLink(j.report_url, j.status === "complete" && !partial ? "Open report" : "Open partial report") : ""}${active ? `<div class="bar ${j.total ? "" : "indeterminate"}"><i${j.total ? ` style="width:${Math.min(100, Math.max(0, 100 * (j.done || 0) / j.total))}%"` : ""}></i></div>` : ""}</div>`;
    }).join("");
    document.querySelectorAll(".research-status[data-church]").forEach(el => {
      const churchJobs = [...latest.values()].filter(j => j.church_id === el.dataset.church);
      const j = churchJobs.find(running) || churchJobs.find(j => j.kind === "medium" && j.status === "error");
      el.innerHTML = j && !running(j) ? `<span>Website research stopped: ${esc(j.label)}</span>` : j ? `<span class="activity-circle ${j.total ? "measured" : ""}" ${j.total ? `style="--progress:${Math.min(100, 100 * (j.done || 0) / j.total)}%"` : ""} aria-hidden="true"></span><span>${j.kind === "deep" ? "Deep dive running" : "Reading website"}${j.total ? ` · ${j.done || 0}/${j.total}` : ""}</span>` : "";
    });
  }

  function mediumSummary(summary) {
    if (!summary) return "";
    const factHTML = f => `<li><strong>${esc(f.label)}:</strong> ${esc(f.value)} ${sourceLink(f.url)}</li>`;
    const seen = new Set();
    const allFacts = (summary.facts || []).filter(f => { const key = `${f.label}:${f.value}`.toLowerCase().replace(/[^a-z0-9]/g, ""); if (seen.has(key)) return false; seen.add(key); return true; });
    const priority = f => {
      const label = String(f.label || "").toLowerCase(), value = String(f.value || "").toLowerCase();
      if (/service|worship|sunday/.test(label) && /\d[:.]\d|\d\s*(am|pm)|\d:\d|morning|evening/.test(value)) return 0;
      if (/lead|senior|head/.test(label) && /pastor|minister/.test(label)) return 1;
      if (/small.?group|ministr|youth|children/.test(label)) return 2;
      if (/contact|address|location|phone|visit/.test(label)) return 3;
      return 4;
    };
    allFacts.sort((a,b) => priority(a)-priority(b));
    const preview = allFacts.filter(f => priority(f) < 4).slice(0,5);
    const remaining = allFacts.filter(f => !preview.includes(f));
    const facts = preview.map(factHTML).join("");
    const lead = (summary.staff || []).find(f => /lead|senior|head|primary/.test(String(f.position || "").toLowerCase()) && /pastor|minister/.test(String(f.position || "").toLowerCase())) || (summary.staff || []).find(f => /pastor|minister/.test(String(f.position || "").toLowerCase()) && !/assistant|associate|youth|children|worship/.test(String(f.position || "").toLowerCase()));
    const leadPreview = lead && !preview.some(f => String(f.value).includes(lead.name)) ? `<p><strong>${esc(lead.position)}:</strong> ${esc(lead.name)} ${sourceLink(lead.url)}</p>` : "";
    const staff = (summary.staff || []).map(f => `<li>${esc(f.name)} — ${esc(f.position)} ${sourceLink(f.url)}</li>`).join("");
    const resources = (summary.resources || []).map(f => sourceLink(f.url, f.label || f.kind)).filter(Boolean).join(" · ");
    const gaps = summary.coverage || {};
    return `<div class="medium-summary">${facts ? `<ul>${facts}</ul>` : ""}${leadPreview}${!facts && !leadPreview ? (gaps.failures?.length && !gaps.pages_scanned ? "<p>Could not read the website; essential facts have not been assessed.</p>" : "<p>Website reviewed; essential facts were not found.</p>") : ""}${remaining.length ? `<details><summary>More website facts (${remaining.length})</summary><ul>${remaining.map(factHTML).join("")}</ul></details>` : ""}${staff ? `<details><summary>Public staff (${summary.staff.length})</summary><ul>${staff}</ul></details>` : ""}${resources ? `<details><summary>Saved resources</summary>${resources}</details>` : ""}${summary.reason ? `<p class="muted">Deep-dive potential: ${esc(summary.deep_dive_candidate)} · ${esc(summary.reason)}</p>` : ""}${gaps.limited ? `<p class="muted">${esc(gaps.limit_reason || 'Website scan reached its limit; some pages remain unread.')}</p>` : ""}${(gaps.missing_basics || []).length ? `<p class="muted">Not found: ${gaps.missing_basics.map(esc).join(", ")}</p>` : ""}</div>`;
  }

  async function loadChurches() {
    if (!sid) return;
    const request = ++churchRequest, requestSid = sid, el = $("#churches");
    const q = new URLSearchParams({ radius: $("#radius").value, sort: $("#sort").value, page, size: $("#size").value });
    const r = await fetch(`/api/churches/${sid}?${q}`); if (!r.ok) return;
    const d = await r.json(); if (request !== churchRequest || requestSid !== sid) return;
    if (d.research) setResearch(d.research);
    $("#discovery").textContent = d.discovery && ["running","pending","queued","error"].includes(d.discovery.status) ? d.discovery.label || "Finding nearby churches…" : "";
    page = d.page || 1; maxPages = d.pages || 1; $("#prev").disabled = page <= 1; $("#next").disabled = page >= maxPages;
    if (!d.rows || !d.rows.length) { el.innerHTML = `<p class="muted">${d.total === 0 && d.covered_mi ? "No churches found in this radius yet." : "Tell me where you're starting from and I'll start looking."}</p>`; $("#pageinfo").textContent = ""; return; }
    const fitText = { strong: "Strong fit", possible: "Possible fit", unknown: "Not enough info yet", unlikely: "Unlikely fit", poor: "Poor fit" };
    const openDetails = new Set([...el.querySelectorAll("details[open]")].map(x => `${x.closest(".row").dataset.id}:${x.querySelector("summary").textContent}`));
    el.innerHTML = d.rows.map(c => `<div class="row" data-id="${esc(c.church_id)}">
      ${research.selection_mode ? `<input type="checkbox" data-id="${esc(c.church_id)}" ${picked.has(c.church_id) ? "checked" : ""} aria-label="Select ${esc(c.name)}">` : ""}
      <div class="church-body"><div class="name">${esc(c.name)}</div><div class="meta">${esc(c.denomination || "Denomination unknown")}${c.affiliation_verified === false ? " · Affiliation not verified" : ""} · ${Number(c.distance_miles).toFixed(1)} mi${c.mismatch ? " · Outside current preferences" : ""}</div>
      ${(c.why || []).length ? `<ul class="why">${c.why.map(w => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
      <div class="research-status" data-church="${esc(c.church_id)}"></div>${mediumSummary(c.summary)}
      <div class="church-actions"><button data-ask="${esc(c.church_id)}">Ask a question</button><button data-deep="${esc(c.church_id)}">Deep dive</button></div></div>
      <div class="row-tools"><button class="pin ${c.pinned ? "pinned" : ""}" data-pin="${esc(c.church_id)}" data-pinned="${!!c.pinned}" aria-pressed="${!!c.pinned}" aria-label="${c.pinned ? "Unpin" : "Pin"} ${esc(c.name)}" title="${c.pinned ? "Unpin" : "Pin and research"}"><svg width="13" height="13" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3h8l-1 7 4 4v2H5v-2l4-4zM12 16v6" fill="none" stroke="currentColor" stroke-width="2"/></svg>${c.pinned ? " Pinned" : " Pin"}</button><span class="fit ${esc(c.fit)}">${fitText[c.fit] || ""}</span></div></div>`).join("");
    el.querySelectorAll("details").forEach(x => { x.open = openDetails.has(`${x.closest(".row").dataset.id}:${x.querySelector("summary").textContent}`); });
    el.querySelectorAll("input[type=checkbox]").forEach(cb => cb.onchange = () => {
      cb.checked ? picked.add(cb.dataset.id) : picked.delete(cb.dataset.id);
      if (picked.size > 5) { cb.checked = false; picked.delete(cb.dataset.id); $("#selectioncount").textContent = "Choose up to 5 churches."; }
      else setResearch(research);
      saveDraft();
    });
    el.querySelectorAll("button[data-pin]").forEach(b => b.onclick = async () => { b.disabled = true; try { await postAction("/api/pin", {session_id:sid, church_id:b.dataset.pin, pinned:b.dataset.pinned !== "true"}); await poll(); await loadChurches(); } catch(e) { actionError(e); b.disabled = false; } });
    el.querySelectorAll("button[data-ask]").forEach(b => b.onclick = () => { const c = d.rows.find(c => c.church_id === b.dataset.ask); input.value = `About ${c.name}: `; autosize(); input.focus(); });
    el.querySelectorAll("button[data-deep]").forEach(b => b.onclick = () => launchDeep(b));
    $("#pageinfo").textContent = `${d.page} / ${d.pages} · ${d.total} churches`;
    renderJobs(jobs);
  }
  async function launchDeep(b) {
    b.disabled = true;
    try { await postAction("/api/deep", {session_id:sid,church_id:b.dataset.deep}); await poll(); }
    catch(e) { actionError(e); } finally { b.disabled = false; }
  }

  async function loadAbout() {
    if (!sid) return;
    const r = await fetch(`/api/memory/${sid}`); if (!r.ok) return;
    const items = await r.json(); const el = $("#about");
    if (!Array.isArray(items) || !items.length) { el.innerHTML = "<p class=muted>No preferences remembered yet.</p>"; return; }
    el.innerHTML = items.map((i) => `<div class="about-item"><div>${esc(i.text)}</div><div class="src">${esc(i.src_text || i.src || "")} <button data-key="${esc(i.key)}">edit</button></div></div>`).join("");
    el.querySelectorAll("button[data-key]").forEach((b) => b.onclick = async () => {
      const t = prompt("What should I know instead? Leave blank to remove this item."); if (t === null) return;
      const response = await fetch(`/api/memory/${sid}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key: b.dataset.key, text: t }) });
      if (!response.ok) addMessage({role:"system", text:"I couldn’t apply that edit. Please describe the correction in the chat."});
      memoryVersion = -1; poll();
    });
  }

  async function loadQuestions() {
    if (!sid) return;
    const r = await fetch(`/api/questions/${sid}`); if (!r.ok) return;
    const qs = await r.json(); const el = $("#questions");
    if (!qs.length) { el.innerHTML = "<p class=muted>No open questions yet. Add one below.</p>"; return; }
    const by = {}; qs.forEach((q) => (by[q.church_name || q.church_id] ||= []).push(q));
    const rendered = JSON.stringify(qs); if (el.dataset.rendered === rendered) return; el.dataset.rendered = rendered;
    el.innerHTML = Object.entries(by).map(([name, list]) => `<h4>${esc(name)}</h4>` + list.map((q) => `<div class="q-item">${esc(q.text)} <em class="muted">${esc(q.status)}</em>${q.answer ? `<p>${esc(q.answer)}</p>` : ""} <button data-edit="${q.id}">edit</button> ${q.status === "open" ? `<button data-id="${q.id}">drop</button>` : ""}</div>`).join("") +
      `<button class="primary" data-deep="${esc(list[0].church_id)}">Find these out</button>`).join("");
    el.querySelectorAll("button[data-edit]").forEach((b) => b.onclick = async () => {
      const q = qs.find((x) => String(x.id) === b.dataset.edit);
      const text = prompt("Edit this question", q.text); if (!text || !text.trim()) return;
      await fetch(`/api/questions/${sid}`, {method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({id:q.id,text:text.trim(),status:"open"})}); await loadQuestions();
    });
    el.querySelectorAll("button[data-id]").forEach((b) => b.onclick = async () => { await fetch(`/api/questions/${sid}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: b.dataset.id, status: "dropped" }) }); loadQuestions(); });
    el.querySelectorAll("button[data-deep]").forEach((b) => b.onclick = () => launchDeep(b));
  }

  // composer: Enter sends, Shift+Enter newline
  function autosize() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 160) + "px"; }
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
  $("#composer").addEventListener("submit", (e) => { e.preventDefault(); send(); });

  document.querySelectorAll(".tabs button").forEach((b) => b.onclick = () => {
    document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
    document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("hidden", p.id !== "panel-" + b.dataset.tab));
  });
  ["#sort", "#size"].forEach((s) => $(s).onchange = () => { page = 1; loadChurches(); });
  $("#radius").onchange = async () => { if (!sid) return; const radius = Number($("#radius").value); try { await postAction("/api/radius", {session_id:sid,radius}); page = 1; await poll(); await loadChurches(); } catch(e) { actionError(e); } };
  $("#prev").onclick = () => { if (page > 1) { page--; loadChurches(); } };
  $("#next").onclick = () => { if (page < maxPages) { page++; loadChurches(); } };
  $("#knowmore").onclick = async () => {
    if (!sid) return; $("#knowmore").disabled = true;
    try { const d = await postAction("/api/know_more", {session_id:sid,action:"prepare"}); setResearch(d.research); await loadChurches(); await poll(); }
    catch(e) { actionError(e); $("#knowmore").disabled = false; }
  };
  $("#submitselection").onclick = async () => {
    if (picked.size < 1 || picked.size > 5) return; $("#submitselection").disabled = true;
    try { const d = await postAction("/api/know_more", {session_id:sid,action:"submit",church_ids:[...picked]}); try { sessionStorage.removeItem(draftKey()); } catch(e) {} picked.clear(); setResearch(d.research); await poll(); await loadChurches(); }
    catch(e) { actionError(e); setResearch(research); }
  };

  $("#addquestion").onclick = async () => {
    if (!sid) return;
    const r = await fetch(`/api/churches/${sid}?radius=${$("#radius").value}&size=50`); if (!r.ok) return;
    const rows = (await r.json()).rows || [];
    if (!rows.length) { addMessage({role:"system",text:"Choose a starting place first so I can find churches."}); return; }
    const choice = prompt("Which church? Enter its number:\n" + rows.map((c,i)=>`${i+1}. ${c.name}`).join("\n"));
    const c = rows[Number(choice)-1]; if (!c) return;
    const text = prompt("What would you like to find out?"); if (!text || !text.trim()) return;
    await fetch(`/api/questions/${sid}`, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({church_id:c.church_id,text:text.trim()})}); await loadQuestions();
  };
  async function start() {
    if (sid) { await poll(); if (lastN > 0) return; }
    await send.call(null, "") ; // no-op
    if (!sid || lastN === 0) {
      const r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: sid, text: null }) });
      const d = await r.json(); sid = d.session_id; try { localStorage.setItem("cs_sid", sid); } catch (e) {}
      await poll();
    }
  }
  start();
  setInterval(poll, 2000);
  async function restart(mode) {
    if (busy) return; busy = true;
    try {
      const d = await postAction("/api/restart", {session_id:sid,mode});
      sid = d.session_id; try { localStorage.setItem("cs_sid",sid); } catch(e) {}
      if (mode === "new") { thread.innerHTML = ""; lastN = 0; }
      churchRequest++; tableVersion = -1; memoryVersion = -1; locationText = null; locationRadius = null; picked.clear(); hasOrigin = false; jobs = []; setResearch(d.research || {}); renderJobs([]); await poll();
    } catch(e) { actionError(e); } finally { busy = false; }
  }
  window.csNewChat = () => restart("new");
})();
