/* ECU-Can-DAQ Dashboard - Plotly Dark CANape-style */
const state = {
  signals: [],
  rasters: [],
  selected: new Set(),
  latest: {},
  history: {}, // signal -> {x:[], y:[]}
  ws: null,
  recording: false,
  decimation: 30,
};

const MAX_POINTS = 2000;

// DOM helpers
const $ = (s) => document.querySelector(s);
function logCfg(msg) {
  const el = $("#cfg-log");
  const ts = new Date().toISOString().slice(11,19);
  el.textContent = `[${ts}] ${msg}\n` + el.textContent;
}

// Fetch API
async function fetchJSON(url, opts={}) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`${url} ${r.status} ${await r.text()}`);
  return r.json();
}

async function loadSignals() {
  try {
    const data = await fetchJSON("/api/signals");
    state.signals = data.signals || [];
    const rasterData = await fetchJSON("/api/rasters");
    state.rasters = rasterData.rasters || [];
    if (state.rasters.length===0) state.rasters = [{name:"default", rate_hz:100, signals:[]}]
    renderSignals();
    renderRasters();
    populateRasterSelect();
  } catch(e) {
    logCfg("Failed load signals: "+e);
  }
}

async function loadDeviceConfig() {
  try {
    const cfg = await fetchJSON("/api/device/config");
    // populate UI
    $("#cfg-transport").value = cfg.transport || "simulated";
    $("#cfg-channel").value = cfg.channel || "CAN1";
    const bitrateSel = $("#cfg-bitrate");
    const known = ["125000","250000","500000","1000000"];
    const brStr = String(cfg.bitrate);
    if (known.includes(brStr)) {
      bitrateSel.value = brStr;
      $("#cfg-bitrate-custom").style.display="none";
    } else {
      bitrateSel.value = "custom";
      $("#cfg-bitrate-custom").style.display="block";
      $("#cfg-bitrate-custom").value = brStr;
    }
    $("#cfg-req-id").value = cfg.request_id || "0x7E0";
    $("#cfg-resp-id").value = cfg.response_id || "0x7E8";
    $("#cfg-timeout").value = cfg.response_timeout_ms || 250;
    $("#cfg-term").checked = !!cfg.can_termination;
    $("#cfg-fd").checked = !!cfg.can_fd;
    $("#cfg-host").value = cfg.host || "127.0.0.1";
    $("#cfg-port").value = cfg.port || 5555;

    // NI interfaces
    try {
      const ifaces = await fetchJSON("/api/devices/ni/interfaces");
      $("#ni-ifaces").textContent = "Available: " + (ifaces.interfaces||[]).join(", ");
    } catch {}
  } catch(e){ logCfg("device cfg load err "+e); }
}

async function loadStatus() {
  try {
    const s = await fetchJSON("/api/status");
    $("#conn-badge").textContent = s.device_connected ? "CONNECTED" : "DISCONNECTED";
    $("#conn-badge").className = "badge " + (s.device_connected ? "ok" : "dim");
    $("#daq-badge").textContent = s.running ? (s.recording ? "RECORDING" : "ACQUIRING") : "IDLE";
    $("#daq-badge").className = "badge " + (s.running ? (s.recording?"ok":"warn") : "dim");
    $("#overrun-badge").textContent = `Overruns: ${s.overruns||0} | Batches: ${s.batches_sent||0}`;
    if (s.latest_values) {
      state.latest = {...state.latest, ...s.latest_values};
      renderNumerics();
    }
    if (s.a2l_path) $("#cfg-a2l").value = s.a2l_path;
  } catch {}
}

function renderSignals() {
  const filter = $("#signal-search").value.toLowerCase();
  const ul = $("#signal-list");
  ul.innerHTML = "";
  for (const sig of state.signals) {
    if (filter && !sig.name.toLowerCase().includes(filter) && !(sig.description||"").toLowerCase().includes(filter)) continue;
    const li = document.createElement("li");
    li.className = "signal-item";
    li.innerHTML = `<input type="checkbox" data-name="${sig.name}" ${state.selected.has(sig.name)?"checked":""}> <div style="flex:1"><div>${sig.name}</div><div class="signal-meta">${sig.data_type} @ ${sig.ecu_address} ${sig.unit||""} ${sig.description||""}</div></div>`;
    ul.appendChild(li);
  }
  ul.querySelectorAll("input[type=checkbox]").forEach(chk=>{
    chk.addEventListener("change", (e)=>{
      const name = e.target.getAttribute("data-name");
      if (e.target.checked) state.selected.add(name); else state.selected.delete(name);
      // Persist
      localStorage.setItem("ecu_selected", JSON.stringify([...state.selected]));
    });
  });
}

function renderRasters() {
  const div = $("#raster-list");
  div.innerHTML = "";
  for (const r of state.rasters) {
    const count = (r.signals||[]).length;
    const el = document.createElement("div");
    el.style.cssText="padding:4px 6px;border:1px solid var(--border);border-radius:4px;margin-bottom:4px;background:var(--panel-2);";
    el.innerHTML = `<div style="display:flex;justify-content:space-between;"><b>${r.name}</b><span style="color:var(--text-dim)">${r.rate_hz} Hz</span></div><div style="font-size:10px;color:var(--text-dim)">${count} signals: ${(r.signals||[]).slice(0,5).join(", ")}${count>5?"...":""}</div>`;
    div.appendChild(el);
  }
}
function populateRasterSelect() {
  const sel = $("#raster-assign");
  sel.innerHTML="";
  for (const r of state.rasters) {
    const opt = document.createElement("option");
    opt.value = r.name; opt.textContent = `${r.name} ${r.rate_hz}Hz`;
    sel.appendChild(opt);
  }
  if (state.rasters.length===0) {
    const opt=document.createElement("option"); opt.value="default"; opt.textContent="default 100Hz"; sel.appendChild(opt);
  }
}

function renderNumerics() {
  const cont = $("#numerics");
  cont.innerHTML="";
  // Show selected or latest 12
  let names = [...state.selected];
  if (names.length===0) names = Object.keys(state.latest).slice(0,12);
  else names = names.slice(0,12);
  for (const n of names) {
    const v = state.latest[n];
    const card = document.createElement("div");
    card.className="numeric-card";
    const display = (v!==undefined)? (typeof v==="number"? v.toFixed(3): String(v)) : "--";
    card.innerHTML=`<div class="label">${n}</div><div class="value">${display}</div>`;
    cont.appendChild(card);
  }
  $("#latest-ts").textContent = new Date().toISOString().slice(11,19);
}

// Plotly
function initPlot() {
  const layout = {
    paper_bgcolor: "#0e1116",
    plot_bgcolor: "#0e1116",
    font: {color: "#c9d1d9", size: 11},
    margin: {l:50,r:20,t:20,b:40},
    xaxis: {gridcolor:"#21262d", title:"Time", showspikes:true, spikemode:"across"},
    yaxis: {gridcolor:"#21262d"},
    legend: {orientation:"h", y:-0.2, font:{size:10}},
    hovermode:"x unified",
  };
  Plotly.newPlot("plot", [], layout, {responsive:true, displayModeBar:true, modeBarButtonsToRemove:["select2d","lasso2d"]});
}

function updatePlot(batch) {
  const now = Date.now();
  // batch contains values dict, timestamp_ns
  const ts = batch.timestamp_ns ? batch.timestamp_ns/1e6 : now; // ms
  const x = new Date(ts);
  for (const [name,val] of Object.entries(batch.values)) {
    if (!state.selected.has(name) && state.selected.size>0) continue;
    if (!state.history[name]) state.history[name]={x:[], y:[]};
    const h = state.history[name];
    h.x.push(x);
    h.y.push(val);
    if (h.x.length>MAX_POINTS) { h.x.shift(); h.y.shift(); }
  }

  const traces = [];
  // Use Plotly colors CANape-like palette
  const palette = ["#1f77b4","#ff7f0e","#2ca02c","#d62728","#9467bd","#8c564b","#e377c2","#7f7f7f","#bcbd22","#17becf","#00d4ff","#ff4d4d","#4dff4d","#ffff4d"];
  let i=0;
  for (const name of Object.keys(state.history)) {
    if (state.selected.size>0 && !state.selected.has(name)) continue;
    const h = state.history[name];
    traces.push({
      x: h.x,
      y: h.y,
      mode: "lines",
      name: name,
      line: {width:1.5, color:palette[i%palette.length]},
      hovertemplate: `${name}: %{y}<extra></extra>`,
    });
    i++;
  }
  const layout = {datarevision: now};
  Plotly.react("plot", traces, layout);
}

// WS
function connectWS() {
  const proto = location.protocol==="https:"?"wss":"ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/live`);
  state.ws = ws;
  ws.onopen = ()=>{ $("#ws-badge").textContent="WS: CONNECTED"; $("#ws-badge").className="badge ok"; logCfg("WS connected"); };
  ws.onclose = ()=>{ $("#ws-badge").textContent="WS: DISCONNECTED"; $("#ws-badge").className="badge err"; logCfg("WS disconnected, retry in 2s"); setTimeout(connectWS,2000); };
  ws.onerror = (e)=>{ logCfg("WS error"); };
  ws.onmessage = (ev)=>{
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type==="metadata") {
        if (msg.measurements) { state.signals = msg.measurements; renderSignals(); }
        if (msg.rasters) { state.rasters = msg.rasters; renderRasters(); populateRasterSelect(); }
        return;
      }
      if (msg.type==="status") {
        // update badges
        return;
      }
      if (msg.type==="sample_batch") {
        // batch
        state.latest = {...state.latest, ...msg.values};
        renderNumerics();
        updatePlot(msg);
      }
    } catch(e){ console.log("ws parse err",e); }
  };
}

// Device config apply
async function applyDeviceConfig() {
  const brSel = $("#cfg-bitrate").value;
  const br = brSel==="custom" ? parseInt($("#cfg-bitrate-custom").value) : parseInt(brSel);
  const cfg = {
    transport: $("#cfg-transport").value,
    channel: $("#cfg-channel").value,
    bitrate: br,
    request_id: $("#cfg-req-id").value,
    response_id: $("#cfg-resp-id").value,
    response_timeout_ms: parseInt($("#cfg-timeout").value),
    can_termination: $("#cfg-term").checked,
    can_fd: $("#cfg-fd").checked,
    host: $("#cfg-host").value,
    port: parseInt($("#cfg-port").value)||0,
  };
  try {
    const res = await fetchJSON("/api/device/config", {method:"PUT", headers:{"Content-Type":"application/json"}, body: JSON.stringify(cfg)});
    logCfg("Config applied: "+JSON.stringify(res));
    $("#status-text").textContent = "Config applied";
  } catch(e){ logCfg("Apply failed: "+e); $("#status-text").textContent = "Config failed"; }
}

async function testConnection() {
  logCfg("Testing connection...");
  $("#status-text").textContent="Testing...";
  try {
    const res = await fetchJSON("/api/device/test", {method:"POST"});
    logCfg(`Test ${res.success?"OK":"FAIL"}: ${res.message} ${JSON.stringify(res.details)}`);
    $("#status-text").textContent = res.success ? "Test OK" : "Test FAIL: "+res.message;
    $("#conn-badge").textContent = res.success ? "CONNECTED" : "FAIL";
    $("#conn-badge").className = "badge "+(res.success?"ok":"err");
  } catch(e){ logCfg("Test error "+e); }
}

// Events
document.addEventListener("DOMContentLoaded", ()=>{
  // restore selected
  try { const sel = JSON.parse(localStorage.getItem("ecu_selected")||"[]"); state.selected = new Set(sel); } catch {}
  initPlot();
  loadSignals();
  loadDeviceConfig();
  loadStatus();
  setInterval(loadStatus, 3000);
  connectWS();

  $("#signal-search").addEventListener("input", renderSignals);
  $("#btn-select-all").addEventListener("click", ()=>{ for(const s of state.signals) state.selected.add(s.name); localStorage.setItem("ecu_selected", JSON.stringify([...state.selected])); renderSignals(); renderNumerics(); });
  $("#btn-select-none").addEventListener("click", ()=>{ state.selected.clear(); localStorage.setItem("ecu_selected", JSON.stringify([])); renderSignals(); renderNumerics(); });
  $("#btn-assign").addEventListener("click", ()=>{
    const rasterName = $("#raster-assign").value;
    const raster = state.rasters.find(r=>r.name===rasterName);
    if (!raster) return;
    // add selected to raster
    for (const name of state.selected) if (!raster.signals.includes(name)) raster.signals.push(name);
    // send to API
    fetchJSON("/api/rasters", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(state.rasters)}).then(()=>{ logCfg(`Assigned ${state.selected.size} signals to ${rasterName}`); renderRasters(); }).catch(e=>logCfg("Assign failed "+e));
  });

  $("#cfg-bitrate").addEventListener("change", (e)=>{
    $("#cfg-bitrate-custom").style.display = e.target.value==="custom" ? "block" : "none";
  });
  $("#btn-apply-cfg").addEventListener("click", applyDeviceConfig);
  $("#btn-test-conn").addEventListener("click", testConnection);
  $("#btn-connect").addEventListener("click", ()=>{
    fetchJSON("/api/daq/start", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({file_path:$("#record-file").value, format: "both", enable_csv_preview: $("#record-csv").checked})}).then(r=>{ logCfg("Start requested"); $("#status-text").textContent="Start requested (CLI managed)"; }).catch(e=>logCfg(e));
  });
  $("#btn-stop").addEventListener("click", ()=>{
    fetchJSON("/api/daq/stop", {method:"POST"}).then(r=>logCfg("Stop requested")).catch(e=>logCfg(e));
  });
  $("#btn-record").addEventListener("click", (e)=>{
    const btn = e.target;
    if (btn.textContent.includes("Record")) { btn.textContent="■ Stop Record"; btn.classList.remove("success"); } else { btn.textContent="● Record"; btn.classList.add("success"); }
  });
  $("#btn-save-proj").addEventListener("click", ()=>{ logCfg("Save project not yet implemented – use CLI: ecu-can-daq save-project"); });
});
