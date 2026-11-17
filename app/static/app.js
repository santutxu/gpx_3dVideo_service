"use strict";
/* Cumbre — SPA sin recargas: polling parcial, transiciones suaves, sin innerHTML */

const API = "api/v1";
const $ = id => document.getElementById(id);
const state = { user:null, view:"routes", videos:[], jobs:[], timer:null, pollAbort:null, registrationOpen:true, newKey:null, mapAttribution:"", maxMoments:12 };

// ---------- Helpers ----------
function h(tag, props={}, ...children){
  const el = document.createElement(tag);
  for(const [k,v] of Object.entries(props)){
    if(v==null||v===false) continue;
    if(k==="class") el.className=v;
    else if(k==="text") el.textContent=v;
    else if(k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if(k in el && k!=="list") el[k]=v;
    else el.setAttribute(k, v===true?"":v);
  }
  for(const c of children.flat()) if(c!=null&&c!==false) el.append(c);
  return el;
}
class ApiError extends Error{ constructor(s,m){super(m); this.status=s} }
async function api(path, {method="GET", json, body, signal}={}){
  let res;
  try{
    res = await fetch(`${API}${path}`, {method, credentials:"same-origin", headers: json?{"Content-Type":"application/json"}:undefined, body: json?JSON.stringify(json):body, signal});
  }catch(e){
    if(e.name==="AbortError") throw e;
    throw new ApiError(0,"No se pudo conectar con el servidor.");
  }
  if(res.status===204) return null;
  let data=null; try{data=await res.json()}catch{}
  if(res.ok) return data;
  let msg=`Error ${res.status}`;
  if(data){
    if(typeof data.message==="string") msg=data.message;
    else if(typeof data.detail==="string") msg=data.detail;
    else if(Array.isArray(data.detail)&&data.detail[0]) msg=String(data.detail[0].msg||msg).replace(/^Value error, /,"");
  }
  if(res.status===401 && state.user){ state.user=null; showAuth(); }
  throw new ApiError(res.status, msg);
}
function toast(msg, bad=false){
  const el=h("div",{class:"toast"+(bad?" bad":""), text:msg});
  $("toasts").append(el);
  requestAnimationFrame(()=> el.style.opacity="1");
  setTimeout(()=>{ el.style.opacity="0"; el.style.transform="translateY(4px)"; setTimeout(()=>el.remove(),220)}, 4200);
}
function confirmDialog(title, text, ok="Borrar"){
  const d=$("confirm"); $("confirm-title").textContent=title; $("confirm-text").textContent=text; $("confirm-ok").textContent=ok;
  return new Promise(r=>{ d.addEventListener("close",()=>r(d.returnValue==="ok"),{once:true}); d.returnValue="cancel"; d.showModal(); });
}
const num=(v,d=0)=>v.toLocaleString("es-ES",{minimumFractionDigits:d, maximumFractionDigits:d});
const km=m=>num(m/1000,1);
function duration(s){ s=Math.round(s); const h=Math.floor(s/3600), m=Math.floor((s%3600)/60); return h?`${h} h ${String(m).padStart(2,"0")} min`:`${m} min`; }
const dateTime=i=> new Date(i).toLocaleString("es-ES",{dateStyle:"medium", timeStyle:"short"});
const dateOnly=i=> new Date(i).toLocaleDateString("es-ES",{dateStyle:"medium"});

// ---------- Auth ----------
let authMode="login";
function setAuthMode(m){
  authMode=m; const reg=m==="register";
  $("tab-login").setAttribute("aria-selected", String(!reg));
  $("tab-register").setAttribute("aria-selected", String(reg));
  $("auth-pass2-row").hidden=!reg; $("auth-hint").hidden=!reg;
  $("auth-pass").autocomplete=reg?"new-password":"current-password";
  $("auth-submit").textContent=reg?"Crear cuenta":"Entrar"; $("auth-error").textContent="";
}
function showAuth(){ stopPolling(); $("app").hidden=true; $("auth").hidden=false; $("tab-register").hidden=!state.registrationOpen; setAuthMode("login"); $("auth-user").focus(); }
$("tab-login").onclick=()=>setAuthMode("login");
$("tab-register").onclick=()=>setAuthMode("register");
$("auth-form").onsubmit=async e=>{
  e.preventDefault();
  const u=$("auth-user").value.trim(), p=$("auth-pass").value, err=$("auth-error"); err.textContent="";
  if(authMode==="register"){
    if(!/^[A-Za-z0-9_-]{3,32}$/.test(u)) return void(err.textContent="Usuario 3-32 letras/números/_/-.");
    if(p.length<8) return void(err.textContent="Contraseña mínimo 8.");
    if(p!==$("auth-pass2").value) return void(err.textContent="No coinciden.");
  } else if(!u||!p) return void(err.textContent="Usuario y contraseña requeridos.");
  const b=$("auth-submit"); b.disabled=true;
  try{ state.user=await api(authMode==="register"?"/auth/register":"/auth/login",{method:"POST", json:{username:u, password:p}}); $("auth-pass").value=$("auth-pass2").value=""; showApp(); }
  catch(ex){ err.textContent=ex.message } finally{ b.disabled=false }
};
$("logout").onclick=async()=>{ try{await api("/auth/logout",{method:"POST"})}catch{}; state.user=null; state.videos=[]; state.jobs=[]; state.newKey=null; showAuth(); };

// ---------- Shell ----------
function showApp(){
  $("auth").hidden=true; $("app").hidden=false;
  $("who-name").textContent=state.user.name;
  $("who-role").textContent=state.user.is_admin?"Administrador":"Usuario";
  $("avatar").textContent=state.user.name.slice(0,1);
  $("logout").hidden=state.user.name==="anonymous";
  $("nav-users").hidden=!(state.user.is_admin && state.user.name!=="anonymous");
  navigate("routes");
}
const views={home:renderHome, routes:renderRoutes, upload:renderUpload, keys:renderKeys, account:renderAccount, users:renderUsers};
function navigate(v){
  state.view=v;
  for(const b of $("nav").querySelectorAll("button")) b.toggleAttribute("aria-current", b.dataset.view===v);
  stopPolling(); if(activeMap){ activeMap.destroy(); activeMap=null; }
  views[v](); $("main").focus({preventScroll:true}); scrollTo(0,0);
}
$("nav").addEventListener("click", e=>{ const b=e.target.closest("button[data-view]"); if(b) navigate(b.dataset.view) });
function pageHead(title, sub, action){
  return h("div",{class:"page-head"}, h("div",{}, h("h1",{text:title}), sub&&h("p",{text:sub, class:"muted"})), action);
}

// ---------- Rutas — sin recarga completa, solo parches ----------
const STATUS={queued:"En cola", rendering:"Renderizando el 3D", compiling:"Finalizando", failed:"Ha fallado"};
const isActive=j=> j.status==="queued"||j.status==="rendering"||j.status==="compiling";
let routesMounted=false, els={};

async function renderRoutes(){
  const main=$("main");
  if(!routesMounted){
    // primera vez: esqueleto
    const head=pageHead("Rutas", "Tus vídeos y procesos — actualización en vivo sin recargar.",
      h("button",{class:"btn primary", text:"Nuevo vídeo", onclick:()=>navigate("upload")}));
    const statsWrap=h("div",{class:"stats", id:"stats-wrap"});
    for(let i=0;i<4;i++) statsWrap.append(h("div",{class:"card stat"}, h("div",{class:"skeleton", style:"height:18px; width:60%"}), h("div",{class:"skeleton", style:"height:28px; margin-top:8px"})));
    const jobsHead=h("h2",{class:"section-title", text:"En proceso"});
    const jobsBox=h("div",{class:"jobs", id:"jobs-box"}, h("div",{class:"skeleton", style:"height:64px"}));
    const videosHead=h("h2",{class:"section-title", text:"Vídeos"});
    const grid=h("div",{class:"grid", id:"grid-box"});
    const empty=h("div",{class:"card empty", id:"empty-box", hidden:true}, h("div",{class:"art", text:"◐"}), h("h3",{text:"Aún no tienes vídeos"}), h("p",{class:"muted", text:"Sube un GPX y en unos minutos tendrás el recorrido 3D listo para compartir."}), h("button",{class:"btn primary", text:"Subir un GPX", onclick:()=>navigate("upload")}));
    main.replaceChildren(head, statsWrap, jobsHead, jobsBox, videosHead, grid, empty);
    els={statsWrap, jobsHead, jobsBox, videosHead, grid, empty};
    routesMounted=true;
  } else {
    // reutilizar esqueleto, no destruir videos en reproducción
    $("main").querySelector(".page-head")?.replaceWith(pageHead("Rutas", "Tus vídeos y procesos — actualización en vivo sin recargar.", h("button",{class:"btn primary", text:"Nuevo vídeo", onclick:()=>navigate("upload")})));
  }
  try{
    const [videos, jobs]=await Promise.all([api("/videos?limit=200"), api("/jobs")]);
    state.videos=videos.items; state.jobs=jobs;
  }catch(e){ if(e.status!==401) toast(e.message,true); return; }
  if(state.view!=="routes") return;
  patchStats(); patchJobs(); patchVideos();
  if(state.jobs.some(isActive)) startPolling();
}

function patchStats(){
  const v=state.videos; const sum=f=> v.reduce((a,x)=>a+f(x.metrics),0);
  const items=[
    {k:"Rutas", v:num(v.length), s:""},
    {k:"Distancia", v:km(sum(m=>m.total_distance_m)), s:"km"},
    {k:"Desnivel", v:num(sum(m=>m.elevation_gain_m)), s:"m"},
    {k:"Tiempo", v:duration(sum(m=>m.duration_s)), s:""},
  ];
  els.statsWrap.replaceChildren(...items.map(it=> h("div",{class:"card stat fade-in"}, h("small",{text:it.k}), h("strong",{text:it.v}, it.s?h("span",{text:it.s}):null))));
}

function patchJobs(){
  const pending=state.jobs.filter(j=>j.status!=="completed");
  els.jobsHead.hidden = pending.length===0;
  els.jobsBox.hidden = pending.length===0;
  if(!pending.length){ els.jobsBox.replaceChildren(); return; }
  // diff por id: crear mapa existente
  const existing=new Map([...els.jobsBox.children].map(el=>[el.dataset.jobId, el]));
  const nextIds=new Set(pending.map(j=>j.jobId));
  // eliminar los que ya no están
  for(const [id, el] of existing){ if(!nextIds.has(id)) el.remove(); }
  // crear/actualizar
  for(const job of pending){
    let row=existing.get(job.jobId);
    if(!row){
      row=jobRow(job); row.dataset.jobId=job.jobId; els.jobsBox.append(row);
      row.classList.add("fade-in");
    } else {
      // actualizar solo progreso/estado sin recrear video
      const pct=Math.round(job.progress*100);
      const bar=row.querySelector(".bar i");
      const pctEl=row.querySelector(".pct");
      const stateEl=row.querySelector("[data-state]");
      if(bar) bar.style.width=`${job.status==="failed"?0:pct}%`;
      if(pctEl) pctEl.textContent=`${pct} %`;
      if(stateEl) stateEl.textContent= job.status==="failed" ? `Ha fallado: ${job.error||"error desconocido"}` : (STATUS[job.status]||job.status);
      row.classList.toggle("failed", job.status==="failed");
      // si pasó a failed, reemplazar botón descartar
      if(job.status==="failed" && !row.querySelector('[data-action="dismiss"]')){
        const btn=h("button",{class:"btn small", "data-action":"dismiss", text:"Descartar", onclick: async()=>{
          try{ await api(`/jobs/${job.jobId}`,{method:"DELETE"}) }catch(e){ return toast(e.message,true) }
          state.jobs=state.jobs.filter(j=>j.jobId!==job.jobId); patchJobs();
        }});
        row.querySelector(".pct")?.replaceWith(btn);
      }
    }
  }
}

function jobRow(job){
  const failed=job.status==="failed"; const pct=Math.round(job.progress*100);
  const bar=h("i"); bar.style.width=`${failed?0:pct}%`;
  const meta=h("div",{class:"job-meta"},
    h("span",{text:`${job.options.resolution} · ${job.options.orientation==="vertical"?"9:16":"16:9"} · ${job.options.camera_style}`}),
    h("span",{text:`${job.frame_count} fotogramas`})
  );
  return h("div",{class:"card job"+(failed?" failed":"")},
    h("div",{},
      h("h3",{text:job.title}),
      h("div",{"data-state":"", class:"state", text: failed?`Ha fallado: ${job.error||"error desconocido"}`: (STATUS[job.status]||job.status)}),
      meta
    ),
    failed
      ? h("button",{class:"btn small", "data-action":"dismiss", text:"Descartar", onclick: async()=>{
          try{ await api(`/jobs/${job.jobId}`,{method:"DELETE"}) }catch(e){ return toast(e.message,true)}
          state.jobs=state.jobs.filter(j=>j.jobId!==job.jobId); patchJobs();
        }})
      : h("span",{class:"pct", text:`${pct} %`}),
    h("div",{class:"bar", role:"progressbar", "aria-valuenow":pct, "aria-valuemin":0, "aria-valuemax":100}, bar)
  );
}

function patchVideos(){
  const grid=els.grid, empty=els.empty;
  const playingIds=new Set([...document.querySelectorAll(".route video")].filter(v=>!v.paused && !v.ended).map(v=>v.dataset.vid));
  // si hay videos reproduciéndose y no hay nuevos videos, no tocar grid para no cortar reproducción
  const existingIds=new Set(state.videos.map(v=>v.video_id));
  const currentIds=new Set([...grid.children].map(el=>el.dataset.vid));
  const hasNew=[...existingIds].some(id=>!currentIds.has(id));
  const hasRemoved=[...currentIds].some(id=>!existingIds.has(id));
  if(playingIds.size && !hasNew && !hasRemoved){
    // solo actualizar tamaños si no hay cambios estructurales
    return;
  }
  if(!state.videos.length){
    grid.replaceChildren(); empty.hidden=false; els.videosHead.textContent="Vídeos"; return;
  }
  empty.hidden=true;
  els.videosHead.textContent=`Vídeos · ${state.videos.length}`;
  // diff incremental
  const map=new Map([...grid.children].map(el=>[el.dataset.vid, el]));
  // eliminar
  for(const [id, el] of map){ if(!existingIds.has(id)) el.remove(); }
  // añadir/actualizar en orden
  const frag=document.createDocumentFragment();
  for(const v of state.videos){
    let card=map.get(v.video_id);
    if(!card){ card=routeCard(v); card.dataset.vid=v.video_id; frag.append(card); }
    else { frag.append(card); } // reordenar
  }
  grid.append(frag);
}

function routeCard(video){
  const m=video.metrics;
  const metric=(l, val)=> h("div",{}, h("small",{text:l}), h("b",{text:val}));
  const orient=video.options.orientation==="vertical"?"Vertical 9:16":"Horizontal 16:9";
  const resLabel=`${video.width}×${video.height} · ${orient}`;
  const info=`${resLabel} · ${num(video.size_bytes/1e6,1)} MB` + (video.moment_count?` · ${video.moment_count} ${video.moment_count===1?"momento":"momentos"}`:"") + (state.user.is_admin && video.owner && video.owner!==state.user.name?` · ${video.owner}`:"");
  const isV=video.options.orientation==="vertical";
  const el=h("article",{class:"card route"+(isV?" vertical":"")},
    h("div",{class:"route-media", style: isV?"aspect-ratio: 9/16; max-height: 520px":""},
      h("video",{controls:true, preload:"metadata", playsInline:true, "data-vid":video.video_id, src:`${API}/videos/${video.video_id}/stream`, style: isV?"object-fit:contain; background:#000":""}),
      h("span",{class:"badge "+(isV?"":"plain"), text: resLabel, style:"position:absolute; top:10px; left:10px; backdrop-filter:blur(8px)"})
    ),
    h("div",{class:"route-body"},
      h("div",{}, h("h3",{text:video.title}), h("time",{text: m.start_time?`Ruta del ${dateOnly(m.start_time)}`:`Creado el ${dateOnly(video.created_at)}`})),
      h("div",{class:"metrics"}, metric("Distancia", `${km(m.total_distance_m)} km`), metric("Desnivel", `+${num(m.elevation_gain_m)} m`), metric("Tiempo", duration(m.duration_s)), metric("Media", `${num(m.avg_speed_kmh,1)} km/h`)),
      h("div",{class:"route-actions"}, h("a",{class:"btn small", href:`${API}/videos/${video.video_id}/download`, text:"Descargar"}), h("span",{class:"spacer", text:info}), h("button",{class:"btn small danger", text:"Borrar", onclick:()=>deleteVideo(video)}))
    )
  );
  return el;
}
async function deleteVideo(v){
  if(!await confirmDialog("¿Borrar este vídeo?", `"${v.title}" se eliminará definitivamente.`)) return;
  try{ await api(`/videos/${v.video_id}`,{method:"DELETE"}); state.videos=state.videos.filter(x=>x.video_id!==v.video_id); patchVideos(); patchStats(); toast("Vídeo borrado."); }
  catch(e){ toast(e.message,true) }
}

// Polling parcial: solo jobs/videos, sin re-render completo, con backoff y cancelación
function startPolling(){
  stopPolling();
  let interval=2000, visible=true;
  document.addEventListener("visibilitychange", ()=>{ visible=!document.hidden; });
  const tick=async()=>{
    if(state.view!=="routes") return stopPolling();
    if(!visible) return; // no consultar en background
    if(state.pollAbort) state.pollAbort.abort();
    state.pollAbort=new AbortController();
    let jobs;
    try{ jobs=await api("/jobs",{signal: state.pollAbort.signal}) }catch(e){ if(e.name==="AbortError") return; return; }
    const before=new Map(state.jobs.map(j=>[j.jobId,j.status]));
    const finished=state.jobs.filter(isActive).some(o=> jobs.find(j=>j.jobId===o.jobId)?.status==="completed");
    const anyProgress= jobs.some((j,i)=> j.progress!== (state.jobs.find(x=>x.jobId===j.jobId)?.progress));
    state.jobs=jobs;
    if(finished || anyProgress){
      patchJobs();
      if(finished){
        try{
          const v=await api("/videos?limit=200",{signal: state.pollAbort.signal});
          state.videos=v.items; patchVideos(); patchStats(); toast("Tu vídeo está listo.");
        }catch{}
      }
    } else if(jobs.some(isActive)){
      // solo actualizar barras sin rehacer DOM
      patchJobs();
    }
    if(!jobs.some(isActive)) stopPolling();
    else if(document.hidden) interval=Math.min(8000, interval+1000); else interval=2000;
  };
  state.timer=setInterval(tick, interval);
  // ajustar intervalo dinámicamente cuando vuelve a foco
  document.addEventListener("visibilitychange", ()=>{ if(!document.hidden){ clearInterval(state.timer); interval=2000; state.timer=setInterval(tick, interval); tick(); }});
}
function stopPolling(){ clearInterval(state.timer); state.timer=null; if(state.pollAbort){ state.pollAbort.abort(); state.pollAbort=null; } }

// ---------- Mapa ----------
const TILE=256;
const mercX=lon=>(lon+180)/360;
const mercY=lat=>{ const s=Math.sin(lat*Math.PI/180); return 0.5 - Math.log((1+s)/(1-s))/(4*Math.PI); };
function parseGpx(text){
  const doc=new DOMParser().parseFromString(text,"application/xml");
  if(doc.getElementsByTagName("parsererror").length) throw new Error("El archivo no es un GPX válido.");
  let nodes=doc.getElementsByTagName("trkpt"); if(!nodes.length) nodes=doc.getElementsByTagName("rtept");
  const raw=[]; for(const n of nodes){ const lat=parseFloat(n.getAttribute("lat")), lon=parseFloat(n.getAttribute("lon")); if(Number.isFinite(lat)&&Number.isFinite(lon)&&Math.abs(lat)<=85&&Math.abs(lon)<=180) raw.push({lat,lon}); }
  if(raw.length<2) throw new Error("El GPX no contiene una ruta.");
  const step=Math.ceil(raw.length/4000); const points=raw.filter((_,i)=>i%step===0||i===raw.length-1);
  let d=0; points.forEach((p,i)=>{ if(i){ const q=points[i-1], rad=Math.PI/180; const a=Math.sin((p.lat-q.lat)*rad/2)**2 + Math.cos(p.lat*rad)*Math.cos(q.lat*rad)*Math.sin((p.lon-q.lon)*rad/2)**2; d+=2*6371008.8*Math.asin(Math.min(1,Math.sqrt(a))); } p.d=d; p.x=mercX(p.lon); p.y=mercY(p.lat); });
  return {points, total:d};
}
function createRouteMap(container, points, onPick){
  const tiles=h("div",{class:"map-tiles"}); const canvas=h("canvas",{class:"map-canvas"}); const ctx=canvas.getContext("2d");
  const live=new Map(); let zoom=12, cx=0.5, cy=0.5, markers=[], pending=null, drag=null, wheel=0;
  const size=()=>[container.clientWidth, container.clientHeight];
  function fit(){
    const xs=points.map(p=>p.x), ys=points.map(p=>p.y);
    const [minX,maxX,minY,maxY]=[Math.min(...xs),Math.max(...xs),Math.min(...ys),Math.max(...ys)];
    const [w,hgt]=size();
    for(zoom=18; zoom>2; zoom--){ const s=TILE*2**zoom; if((maxX-minX)*s <= w-100 && (maxY-minY)*s <= hgt-100) break; }
    cx=(minX+maxX)/2; cy=(minY+maxY)/2; render();
  }
  function screen(p){ const s=TILE*2**zoom, [w,hgt]=size(); return [(p.x-cx)*s+w/2,(p.y-cy)*s+hgt/2]; }
  function render(){
    const [w,hgt]=size(); if(!w||!hgt) return;
    const s=TILE*2**zoom, n=2**zoom; const left=cx*s-w/2, top=cy*s-hgt/2; const keep=new Set();
    for(let ty=Math.floor(top/TILE); ty<=Math.floor((top+hgt)/TILE); ty++){
      for(let tx=Math.floor(left/TILE); tx<=Math.floor((left+w)/TILE); tx++){
        if(tx<0||ty<0||tx>=n||ty>=n) continue;
        const key=`${zoom}/${tx}/${ty}`; keep.add(key);
        let img=live.get(key); if(!img){ img=h("img",{alt:"",draggable:false, src:`${API}/tiles/${key}`}); live.set(key,img); tiles.append(img); }
        img.style.transform=`translate(${Math.round(tx*TILE-left)}px,${Math.round(ty*TILE-top)}px)`;
      }
    }
    for(const [k,img] of live) if(!keep.has(k)){ img.remove(); live.delete(k); }
    const dpr=window.devicePixelRatio||1;
    if(canvas.width!==Math.round(w*dpr)||canvas.height!==Math.round(hgt*dpr)){ canvas.width=Math.round(w*dpr); canvas.height=Math.round(hgt*dpr); }
    ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,w,hgt); ctx.lineJoin=ctx.lineCap="round";
    ctx.beginPath(); points.forEach((p,i)=>{ const [x,y]=screen(p); if(i) ctx.lineTo(x,y); else ctx.moveTo(x,y); });
    ctx.strokeStyle="rgba(20,10,4,.75)"; ctx.lineWidth=7; ctx.stroke(); ctx.strokeStyle="#ff5c1e"; ctx.lineWidth=3.5; ctx.stroke();
    const dot=(p,r,fill,stroke,label)=>{
      const [x,y]=screen(p); ctx.beginPath(); ctx.arc(x,y,r,0,Math.PI*2); ctx.fillStyle=fill; ctx.fill(); ctx.lineWidth=3; ctx.strokeStyle=stroke; ctx.stroke();
      if(label){ ctx.fillStyle="#1a1d22"; ctx.font="700 12px Inter, system-ui"; ctx.textAlign="center"; ctx.textBaseline="middle"; ctx.fillText(label,x,y+.5); }
    };
    dot(points[0],6,"#3fb97f","#fff"); dot(points[points.length-1],6,"#1a1d22","#fff");
    markers.forEach((idx,i)=> dot(points[idx],11,"#fff","#ff5c1e",String(i+1)));
    if(pending!=null) dot(points[pending],9,"rgba(255,92,30,.35)","#fff");
  }
  function zoomBy(d,px,py){
    const nxt=Math.max(3,Math.min(18,zoom+d)); if(nxt===zoom) return;
    const [w,hgt]=size(); const ax=px??w/2, ay=py??hgt/2, s0=TILE*2**zoom, s1=TILE*2**nxt;
    const wx=cx+(ax-w/2)/s0, wy=cy+(ay-hgt/2)/s0; zoom=nxt; cx=wx-(ax-w/2)/s1; cy=wy-(ay-hgt/2)/s1; render();
  }
  canvas.addEventListener("pointerdown",e=>{ drag={x:e.clientX,y:e.clientY,cx,cy,moved:false}; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener("pointermove",e=>{
    if(!drag) return; const dx=e.clientX-drag.x, dy=e.clientY-drag.y;
    if(Math.hypot(dx,dy)>4) drag.moved=true;
    if(drag.moved){ const s=TILE*2**zoom; cx=drag.cx-dx/s; cy=drag.cy-dy/s; render(); }
  });
  canvas.addEventListener("pointerup",e=>{
    const was=drag&&drag.moved; drag=null; if(was) return;
    const rect=canvas.getBoundingClientRect(), px=e.clientX-rect.left, py=e.clientY-rect.top;
    let best=-1, bd=28; points.forEach((p,i)=>{ const [x,y]=screen(p), d=Math.hypot(x-px,y-py); if(d<bd){bd=d; best=i;}});
    if(best>=0) onPick(best);
  });
  canvas.addEventListener("pointercancel",()=> drag=null);
  canvas.addEventListener("wheel",e=>{
    e.preventDefault(); wheel+=e.deltaY; if(Math.abs(wheel)<60) return;
    const rect=canvas.getBoundingClientRect(); zoomBy(wheel>0?-1:1, e.clientX-rect.left, e.clientY-rect.top); wheel=0;
  },{passive:false});
  const ctrls=h("div",{class:"map-controls"},
    h("button",{type:"button", text:"+", title:"Acercar", "aria-label":"Acercar", onclick:()=>zoomBy(1)}),
    h("button",{type:"button", text:"−", title:"Alejar", "aria-label":"Alejar", onclick:()=>zoomBy(-1)}),
    h("button",{type:"button", text:"⤢", title:"Ver toda la ruta", "aria-label":"Ver toda la ruta", onclick:fit}));
  container.replaceChildren(tiles,canvas,ctrls,h("span",{class:"map-credit", text:state.mapAttribution}));
  const obs=new ResizeObserver(render); obs.observe(container); requestAnimationFrame(fit);
  return { setMarkers(l){markers=l; render()}, setPending(i){pending=i; render()}, destroy(){obs.disconnect()} };
}

// ---------- Home ----------
function renderHome(){
  const main=$("main");
  const head=pageHead("Resumen de actividades","Tus rutas, vídeos y momentos recientes.");
  const error=h("p",{class:"error", role:"alert"}); const stats=h("div",{class:"stats-grid"});
  const list=h("div",{class:"card panel"}, h("h2",{text:"Actividad reciente"}), h("div",{class:"table-wrap"}, h("table",{}, h("thead",{}, h("tr",{}, ["Título","Estado","Duración","Momentos","Acciones"].map(t=>h("th",{text:t})))), h("tbody",{id:"home-jobs"}))));
  async function load(){
    stats.replaceChildren(h("p",{class:"muted", text:"Cargando resumen…"}));
    list.querySelector("tbody").replaceChildren(h("tr",{}, h("td",{colspan:5, text:"Cargando…"})));
    try{
      const jobs=await api("/jobs"); const videos=await api("/videos");
      const completed=videos.filter(v=>v.owner===state.user.name);
      const totalDist=jobs.reduce((a,b)=>a+(b.track?.total_distance_m||0),0)/1000;
      const totalDur=jobs.reduce((a,b)=>a+(b.total_duration_s||0),0);
      const moments=jobs.reduce((a,b)=>a+(b.moment_count||0),0)+completed.reduce((a,b)=>a+(b.moment_count||0),0);
      stats.replaceChildren(
        h("div",{class:"stat-card card stat"}, h("strong",{text:num(totalDist,1)}), h("small",{text:"km recorridos"})),
        h("div",{class:"stat-card card stat"}, h("strong",{text:num(totalDur/60,1)}), h("small",{text:"minutos renderizados"})),
        h("div",{class:"stat-card card stat"}, h("strong",{text:completed.length}), h("small",{text:"vídeos guardados"})),
        h("div",{class:"stat-card card stat"}, h("strong",{text:moments}), h("small",{text:"momentos totales"}))
      );
      const tbody=list.querySelector("tbody");
      tbody.replaceChildren(...jobs.map(j=>{
        const link=h("a",{href:`#/routes/${j.job_id}`, text:j.title||"Ruta sin título", class:"link"});
        return h("tr",{}, h("td",{},link), h("td",{}, h("span",{class:"badge"+(j.status==="completed"?" success":j.status==="failed"?" danger":" plain"), text:j.status.toUpperCase()})), h("td",{}, num(j.total_duration_s||0,1)+" s"), h("td",{}, j.moment_count||0), h("td",{}, h("button",{class:"btn small", text:"Ver", onclick:()=>navigate("routes", j.job_id)})));
      }));
    }catch(e){ error.textContent=e.message; stats.replaceChildren(); list.querySelector("tbody").replaceChildren(h("tr",{}, h("td",{colspan:5, text:"Error al cargar."}))); }
  }
  main.replaceChildren(head, error, h("div",{class:"card panel wide"}, h("h2",{text:"Resumen"}), stats), list); load();
}

// ---------- Nuevo vídeo ----------
const MEDIA_TYPES="image/jpeg,image/png,image/webp,video/mp4,video/quicktime,video/webm";
let activeMap=null;
function renderUpload(){
  let file=null, route=null; const moments=[];
  const error=h("p",{class:"error", role:"alert"});
  const input=h("input",{type:"file", accept:".gpx,application/gpx+xml", hidden:true});
  const dropTitle=h("strong",{text:"Arrastra aquí tu archivo GPX"});
  const dropHint=h("span",{class:"muted", text:"o haz clic para elegirlo · máx. 25 MB"});
  const drop=h("div",{class:"drop", role:"button", tabIndex:0, "aria-label":"Elegir archivo GPX"}, h("span",{class:"ico ico-upload","aria-hidden":"true"}), dropTitle, dropHint);
  const mapBox=h("div",{class:"map"}); const editor=h("div",{class:"moment-editor"}); const list=h("div",{class:"moment-list"});
  const momentsSection=h("fieldset",{hidden:true}, h("legend",{text:"Momentos (opcional)"}), h("p",{class:"hint", text:"Haz clic sobre la ruta y adjunta foto/clip. Aparecerá en el vídeo."}), mapBox, editor, list);
  const kmAt=i=>num(route.points[i].d/1000,1);
  function drawMoments(){
    moments.sort((a,b)=>a.index-b.index); activeMap.setMarkers(moments.map(m=>m.index));
    list.replaceChildren(...moments.map((m,i)=> h("div",{class:"moment"}, h("span",{class:"moment-n", text:String(i+1)}),
      m.file.type.startsWith("image/")?h("img",{src:m.url, alt:""}):h("span",{class:"moment-clip", text:"Clip"}),
      h("div",{}, h("strong",{text:m.caption||m.file.name}), h("small",{text:`km ${kmAt(m.index)} · ${m.file.type.startsWith("image/")?"foto":"vídeo"} · ${num(m.file.size/1e6,1)} MB`})),
      h("button",{class:"btn small danger", type:"button", text:"Quitar", onclick:()=>{ URL.revokeObjectURL(m.url); moments.splice(moments.indexOf(m),1); drawMoments(); }}))));
  }
  function closeEditor(){ editor.replaceChildren(); if(activeMap) activeMap.setPending(null) }
  function openEditor(idx){
    if(moments.length>=state.maxMoments) return toast(`Máximo ${state.maxMoments} momentos por vídeo.`,true);
    activeMap.setPending(idx);
    const media=h("input",{type:"file", accept:MEDIA_TYPES}); const caption=h("input",{maxLength:80, placeholder:"p. ej. Cima del Gorbea"}); const prob=h("p",{class:"error", role:"alert"});
    editor.replaceChildren(h("div",{class:"card moment-form"}, h("strong",{text:`Nuevo momento en km ${kmAt(idx)}`}), h("label",{text:"Foto o clip (JPG, PNG, WebP, MP4, MOV, WebM)"}, media), h("label",{text:"Pie de foto (opcional)"}, caption), prob, h("div",{class:"row"}, h("button",{class:"btn primary small", type:"button", text:"Añadir momento", onclick:()=>{
      const f=media.files[0]; if(!f) return void(prob.textContent="Elige foto o clip."); if(!/^(image\/(jpeg|png|webp)|video\/(mp4|quicktime|webm))$/.test(f.type)) return void(prob.textContent="Formato no admitido."); if(f.size>100*1024*1024) return void(prob.textContent="Supera 100 MB."); moments.push({index:idx, file:f, caption:caption.value.trim(), url:URL.createObjectURL(f)}); closeEditor(); drawMoments();
    }}), h("button",{class:"btn small", type:"button", text:"Cancelar", onclick:closeEditor}))));
    media.focus();
  }
  const setFile=async f=>{
    if(!f) return; if(!/\.gpx$/i.test(f.name)) return void(error.textContent="El archivo debe ser .gpx");
    file=f; error.textContent=""; drop.classList.add("has-file"); dropTitle.textContent=f.name; dropHint.textContent=`${num(f.size/1024)} kB · cambiar`;
    if(!titleInput.value.trim()) titleInput.value=f.name.replace(/\.gpx$/i,"").replace(/[_-]+/g," ").trim().slice(0,120);
    moments.splice(0).forEach(m=>URL.revokeObjectURL(m.url)); list.replaceChildren(); closeEditor(); if(activeMap){activeMap.destroy(); activeMap=null}
    try{ route=parseGpx(await f.text()); momentsSection.hidden=false; activeMap=createRouteMap(mapBox, route.points, openEditor); }catch{ route=null; momentsSection.hidden=true; }
  };
  drop.onclick=()=>input.click(); drop.onkeydown=e=>{ if(e.key==="Enter"||e.key===" ") {e.preventDefault(); input.click()}};
  input.onchange=()=>setFile(input.files[0]);
  drop.ondragover=e=>{ e.preventDefault(); drop.classList.add("over") }; drop.ondragleave=()=>drop.classList.remove("over");
  drop.ondrop=e=>{ e.preventDefault(); drop.classList.remove("over"); setFile(e.dataTransfer.files[0]) };
  const option=(n,v,t,tx,ch)=> h("label",{class:"option"}, h("input",{type:"radio", name:n, value:v, checked:ch}), h("span",{}, h("b",{text:t}), h("small",{text:tx})));
  const speedOut=h("output",{text:"1×"}); const speed=h("input",{type:"range", min:0.5, max:4, step:0.25, value:1, "aria-label":"Velocidad", oninput:()=>{ speedOut.textContent=`${num(Number(speed.value),2).replace(/,?0+$/, "")}×` }});
  const titleInput=h("input",{type:"text", name:"title", maxLength:120, placeholder:"Ej: Gorbea invernal · 12 km", autocomplete:"off"});
  const orientationHelp=h("p",{class:"hint", text:"Vertical 9:16 ocupa toda la pantalla en móviles (Reels, TikTok). El relieve y la ruta se adaptan al formato."});
  const submit=h("button",{class:"btn primary", type:"submit", text:"Crear vídeo"});
  const form=h("form",{class:"form", novalidate:true, onsubmit: async e=>{
    e.preventDefault(); if(!file) return void(error.textContent="Elige primero un GPX");
    const data=new FormData(); data.set("file", file);
    const t=titleInput.value.trim(); if(t) data.set("title", t);
    data.set("camera_style", form.elements.camera_style.value);
    data.set("resolution", form.elements.resolution.value);
    data.set("orientation", form.elements.orientation.value);
    data.set("animation_speed", speed.value);
    if(moments.length){
      data.set("moments", JSON.stringify(moments.map((m,i)=>{ const p=route.points[m.index]; return {lat:p.lat, lon:p.lon, position:route.total? p.d/route.total:0, caption:m.caption, file_index:i}})));
      moments.forEach(m=> data.append("moment_files", m.file, m.file.name));
    }
    submit.disabled=true; submit.textContent=moments.length?"Subiendo ruta y momentos…":"Subiendo…"; error.textContent="";
    try{ await api("/render-gpx",{method:"POST", body:data}); moments.forEach(m=>URL.revokeObjectURL(m.url)); toast("Ruta recibida. Creando vídeo."); navigate("routes"); }
    catch(err){ error.textContent=err.message; submit.disabled=false; submit.textContent="Crear vídeo" }
  }},
    h("fieldset",{}, h("legend",{text:"Nombre del vídeo"}), h("label",{text:"Título — se usa como nombre en tu biblioteca"}, titleInput), h("p",{class:"hint", text:"Vacíousa el nombre del GPX. Máx. 120."})),
    drop, input, momentsSection,
    h("fieldset",{}, h("legend",{text:"Cámara"}), h("div",{class:"options"},
      option("camera_style","follow","Seguimiento","Vuela detrás",true),
      option("camera_style","orbit","Órbita","Gira alrededor",false),
      option("camera_style","overhead","Cenital","Norte al frente",false))),
    h("fieldset",{}, h("legend",{text:"Formato"}), h("div",{class:"options"},
      option("orientation","horizontal","Horizontal 16:9","YouTube, TV",true),
      option("orientation","vertical","Vertical 9:16","Reels / TikTok",false))),
    h("fieldset",{}, h("legend",{text:"Resolución"}), h("div",{class:"options"},
      option("resolution","1080p","1080p","Máxima calidad",true),
      option("resolution","720p","720p","Más rápido",false))),
    orientationHelp,
    h("fieldset",{}, h("legend",{text:"Velocidad"}), h("div",{class:"range-row"}, speed, speedOut), h("p",{class:"hint", text:"A 1× dura 20-90s según distancia; a 2× dura la mitad + momentos."})),
    error, h("div",{}, submit));
  $("main").replaceChildren(pageHead("Nuevo vídeo","Sube un GPX, marca tus momentos y elige cómo quieres verlo."), form);
}

// ---------- Users / Keys / Account (igual, sin recarga de página al navegar) ----------
function promptDialog(title, text, {type="password", okLabel="Guardar", minLength=0}={}){
  const d=$("prompt"), f=$("prompt-input"); $("prompt-title").textContent=title; $("prompt-text").textContent=text; $("prompt-ok").textContent=okLabel; f.type=type; f.value=""; f.minLength=minLength; f.required=minLength>0;
  return new Promise(r=>{ d.addEventListener("close",()=>r(d.returnValue==="ok"?f.value:null),{once:true}); d.returnValue="cancel"; d.showModal(); f.focus(); });
}
async function renderUsers(){
  const main=$("main"); const head=pageHead("Usuarios","Cuentas con acceso al servicio."); const error=h("p",{class:"error", role:"alert"}); let users=[];
  try{ users=await api("/admin/users") }catch(e){ if(e.status===401) return; error.textContent=e.message }
  if(state.view!=="users") return;
  const act=async(fn,ok)=>{ try{ await fn(); if(ok) toast(ok); renderUsers(); }catch(e){ toast(e.message,true)} };
  const rows=users.map(u=>{
    const self=u.username.toLowerCase()===state.user.name.toLowerCase();
    return h("tr",{}, h("td",{}, h("strong",{text:u.username}), self&&h("span",{class:"muted", text:"  (tú)"})),
      h("td",{}, h("span",{class:"badge"+(u.is_admin?"":" plain"), text:u.is_admin?"Administrador":"Usuario"})),
      h("td",{text:dateOnly(u.created_at)}), h("td",{text:`${num(u.video_count)} · ${num(u.storage_bytes/1e6,1)} MB`}), h("td",{text:num(u.api_key_count)}),
      h("td",{}, h("div",{class:"row-actions"},
        !self&&h("button",{class:"btn small", text:u.is_admin?"Quitar admin":"Hacer admin", onclick:()=>act(()=>api(`/admin/users/${u.id}`,{method:"PATCH", json:{is_admin:!u.is_admin}}),"Rol actualizado.")}),
        h("button",{class:"btn small", text:"Contraseña", onclick: async()=>{ const p=await promptDialog("Restablecer contraseña",`Nueva contraseña para ${u.username} (mínimo 8). Se cerrarán sus sesiones.`,{minLength:8}); if(p) act(()=>api(`/admin/users/${u.id}`,{method:"PATCH", json:{new_password:p}}),"Contraseña restablecida.") }}),
        !self&&h("button",{class:"btn small danger", text:"Eliminar", onclick: async()=>{
          if(await confirmDialog("¿Eliminar este usuario?", `Se eliminará ${u.username} con ${u.video_count} vídeos.`,"Eliminar")) act(()=>api(`/admin/users/${u.id}`,{method:"DELETE"}),"Usuario eliminado.");
        }}))));
  });
  const name=h("input",{maxLength:32, autocomplete:"off", required:true}); const pass=h("input",{type:"password", autocomplete:"new-password", required:true, minLength:8}); const admin=h("input",{type:"checkbox"}); const ce=h("p",{class:"error", role:"alert"});
  const create=h("form",{class:"inline-form", novalidate:true, onsubmit: async e=>{
    e.preventDefault(); ce.textContent=""; if(!/^[A-Za-z0-9_-]{3,32}$/.test(name.value.trim())) return void(ce.textContent="Usuario 3-32 letras/números/_/-."); if(pass.value.length<8) return void(ce.textContent="Mínimo 8.");
    try{ await api("/admin/users",{method:"POST", json:{username:name.value.trim(), password:pass.value, is_admin:admin.checked}}); toast("Usuario creado."); renderUsers(); }catch(err){ ce.textContent=err.message }
  }}, h("label",{text:"Usuario"},name), h("label",{text:"Contraseña inicial"},pass), h("label",{class:"check"}, admin,"Administrador"), h("button",{class:"btn primary", type:"submit", text:"Crear usuario"}));
  main.replaceChildren(head, h("div",{class:"card panel wide"}, h("h2",{text:`Cuentas (${users.length})`}), error, h("div",{class:"table-wrap"}, h("table",{}, h("thead",{}, h("tr",{}, ["Usuario","Rol","Alta","Vídeos","Claves",""].map(t=>h("th",{text:t})))), h("tbody",{}, rows)))), h("div",{class:"card panel wide"}, h("h2",{text:"Crear un usuario"}), create, ce));
}
async function renderKeys(){
  const main=$("main"); const head=pageHead("Claves de API","Para scripts u otras apps."); if(!state.user.has_account){ main.replaceChildren(head, h("div",{class:"card panel"}, h("p",{class:"muted", text:"Requiere cuenta registrada."}))); return; }
  let keys=[]; const err=h("p",{class:"error", role:"alert"}); try{ keys=await api("/api-keys")}catch(e){ if(e.status===401) return; err.textContent=e.message } if(state.view!=="keys") return;
  const name=h("input",{maxLength:60, placeholder:"p. ej. Portátil de casa", required:true});
  const create=h("form",{class:"inline-form", onsubmit: async e=>{
    e.preventDefault(); if(!name.value.trim()) return void(err.textContent="Ponle un nombre."); try{ state.newKey=await api("/api-keys",{method:"POST", json:{name:name.value.trim()}}); renderKeys(); }catch(er){ err.textContent=er.message }
  }}, h("label",{text:"Nombre de la clave"}, name), h("button",{class:"btn primary", type:"submit", text:"Crear clave"}));
  let reveal=null;
  if(state.newKey){
    const k=state.newKey.key; const cp=h("button",{class:"btn small", type:"button", text:"Copiar", onclick: async()=>{ try{await navigator.clipboard.writeText(k); cp.textContent="Copiada"}catch{ toast("Copia manualmente",true)}}});
    reveal=h("div",{class:"reveal"}, h("strong",{text:`Clave «${state.newKey.name}» creada. Cópiala ahora.`}), h("div",{class:"row"}, h("code",{text:k}), cp));
  }
  const rows=keys.map(k=> h("tr",{}, h("td",{text:k.name}), h("td",{}, h("code",{text:`${k.prefix}…`})), h("td",{text:dateOnly(k.created_at)}), h("td",{text: k.last_used_at?dateTime(k.last_used_at):"Nunca"}), h("td",{}, h("button",{class:"btn small danger", text:"Revocar", onclick: async()=>{
    if(!await confirmDialog("¿Revocar esta clave?", `«${k.name}» dejará de funcionar.`,"Revocar")) return;
    try{ await api(`/api-keys/${k.id}`,{method:"DELETE"}); if(state.newKey&&state.newKey.id===k.id) state.newKey=null; renderKeys(); }catch(e){ toast(e.message,true)}
  }}))));
  const list=keys.length? h("table",{}, h("thead",{}, h("tr",{}, ["Nombre","Clave","Creada","Último uso",""].map(t=>h("th",{text:t})))), h("tbody",{}, rows)) : h("p",{class:"muted", text:"Todavía no has creado ninguna clave."});
  const origin=location.origin; const sample=`curl -H "X-API-Key: TU_CLAVE" \\\n   -F "file=@ruta.gpx" -F resolution=1080p -F camera_style=follow -F orientation=vertical \\\n   ${origin}/api/v1/render-gpx\n\ncurl -H "X-API-Key: TU_CLAVE" ${origin}/api/v1/videos`;
  main.replaceChildren(head, h("div",{class:"card panel"}, h("h2",{text:"Crear una clave"}), create, err, reveal), h("div",{class:"card panel"}, h("h2",{text:"Tus claves"}), list), h("div",{class:"card panel"}, h("h2",{text:"Cómo usarla"}), h("p",{class:"muted", text:"Envía la clave en X-API-Key o Authorization: Bearer."}), h("pre",{}, h("code",{text:sample})), h("p",{class:"muted"}, "Docs en ", h("a",{href:"docs", target:"_blank", rel:"noopener", text:"/docs", class:"badge"}), ".")));
}
function renderAccount(){
  const head=pageHead("Cuenta","Tus datos de acceso.");
  const profile=h("div",{class:"card panel"}, h("h2",{text:"Perfil"}), h("p",{}, h("strong",{text:state.user.name}), " ", h("span",{class:"badge", text:state.user.is_admin?"Administrador":"Usuario"})), state.user.is_admin && h("p",{class:"muted", text:"Como administrador ves y puedes borrar los vídeos de todos."}));
  if(!state.user.has_account) return void $("main").replaceChildren(head, profile);
  const err=h("p",{class:"error", role:"alert"}); const cur=h("input",{type:"password", autocomplete:"current-password", required:true}); const nxt=h("input",{type:"password", autocomplete:"new-password", required:true, minLength:8}); const rep=h("input",{type:"password", autocomplete:"new-password", required:true, minLength:8});
  const form=h("form",{class:"stack", novalidate:true, onsubmit: async e=>{
    e.preventDefault(); err.textContent=""; if(nxt.value.length<8) return void(err.textContent="Mínimo 8."); if(nxt.value!==rep.value) return void(err.textContent="No coinciden.");
    try{ await api("/auth/password",{method:"PUT", json:{current_password:cur.value, new_password:nxt.value}}); form.reset(); toast("Contraseña cambiada.") }catch(er){ err.textContent=er.message }
  }}, h("label",{text:"Contraseña actual"},cur), h("label",{text:"Nueva contraseña"},nxt), h("label",{text:"Repite nueva contraseña"},rep), err, h("div",{}, h("button",{class:"btn primary", type:"submit", text:"Cambiar contraseña"})));
  $("main").replaceChildren(head, profile, h("div",{class:"card panel"}, h("h2",{text:"Contraseña"}), form));
}
(async function init(){
  try{ const c=await api("/auth/config"); state.registrationOpen=c.registration_open; state.mapAttribution=c.map_attribution||""; state.maxMoments=c.max_moments||12; }catch{}
  try{ state.user=await api("/auth/me"); showApp(); }catch{ showAuth(); }
})();
