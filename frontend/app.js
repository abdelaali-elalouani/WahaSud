/* WahaSud : interface (français / arabe). Toutes les données viennent de l'API (/api/dashboard). */
(function(){
'use strict';
const $=(s,r=document)=>r.querySelector(s);
const clamp=(v,a,b)=>Math.min(b,Math.max(a,v));
const sum=a=>a.reduce((x,y)=>x+y,0);
const I=window.WAHA_I18N,{nb,d1}=I;
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const sgn=(n,f)=>(n>0?'+':n<0?'−':'')+f(Math.abs(n));

/* ---------- langue ---------- */
const LANGS={fr:{dir:'ltr',loc:'fr-FR'},ar:{dir:'rtl',loc:'ar-MA-u-nu-latn'}};
let lang=(()=>{try{const s=localStorage.getItem('wahasud-lang');if(LANGS[s])return s}catch(e){}return /^ar/i.test(navigator.language||'')?'ar':'fr'})();
const t=(k,...a)=>{const v=I[lang][k]!==undefined?I[lang][k]:I.fr[k];return typeof v==='function'?v(...a):v};

const state={sc:'prevu',h:'jour',inj:null,data:null,log:[],busy:false,plan:null};
let last=null,reqId=0;

function applyStatic(){
  const L=LANGS[lang];I.F.lang=lang;
  document.documentElement.lang=lang;document.documentElement.dir=L.dir;
  document.querySelectorAll('[data-i18n]').forEach(el=>{const v=I[lang][el.dataset.i18n];if(typeof v==='string')el.textContent=v});
  document.querySelectorAll('[data-i18n-html]').forEach(el=>{const v=I[lang][el.dataset.i18nHtml];if(typeof v==='string')el.innerHTML=v});
  document.querySelectorAll('[data-i18n-attr]').forEach(el=>el.dataset.i18nAttr.split(';').forEach(p=>{const[a,k]=p.split(':');el.setAttribute(a,t(k))}));
  const b=$('#langBtn');b.textContent=lang==='fr'?'العربية':'Français';b.lang=lang==='fr'?'ar':'fr';
  $('#methodList').innerHTML=t('method').map(x=>`<li>${esc(x)}</li>`).join('');
  document.querySelectorAll('select[data-lang]').forEach(s=>{s.innerHTML='';s.dataset.lang=''});   // reconstruits dans la langue choisie
}

/* ---------- API ---------- */
async function api(path,opts){
  const r=await fetch(path,opts);
  let body=null;try{body=await r.json()}catch(e){}
  if(!r.ok)throw new Error((body&&body.error)||('Erreur '+r.status));
  return body;
}
function banner(msg,isErr){
  const b=$('#banner');
  if(!msg){b.hidden=true;return}
  b.hidden=false;b.textContent=msg;b.className='banner'+(isErr?' err':'');
}
const qs=(extra)=>{const q=new URLSearchParams({scenario:state.sc,lang});if(state.inj)q.set('leak',state.inj);for(const k in extra)q.set(k,extra[k]);return q};
async function load(){
  const my=++reqId;
  try{
    const [data,log]=await Promise.all([api('/api/dashboard?'+qs()),api('/api/decisions/history?limit=30')]);
    if(my!==reqId)return;                       // une réponse plus récente est arrivée
    state.data=adapt(data);state.log=log.history;
    banner(null);render();
  }catch(err){
    if(my!==reqId)return;
    banner(err instanceof TypeError?t('err_server'):err.message,true);
  }
}

/* met la réponse de l'API sous la forme utilisée par l'affichage */
function adapt(a){
  const E=a.energy,W=a.water;
  const cities=W.cities.map(c=>({id:c.id,n:c.name,pop:c.population_k,dem:c.demand_m3_day,lvl:c.level_pct,aut:c.autonomy_days,
    need:c.need_m3_day,alloc:c.allocated_m3_day,score:c.need_score,rank:c.priority_rank,status:c.tank_status,full:c.tank_full,
    q:c.flow_24h_m3h,det:{kind:c.detection.kind,loss:c.detection.loss_m3_day},ratio:c.detection.night_ratio,leak:c.leak_m3_day,
    lat:c.lat,lon:c.lon,src:c.data_source}));
  const by=Object.fromEntries(cities.map(c=>[c.id,c]));
  return{sc:a.scenario,mode:a.data_mode,PLANT:a.constants.plant_capacity_m3_day,NIGHT:a.constants.night_share,
    avail:E.available_mwh,needed:E.needed_mwh,coverage:E.coverage_pct,supplyCap:E.producible_m3_day,
    e:{s:E.solar_mwh,w:E.wind_mwh},sites:E.sites,hourly:E.hourly,days:E.days,fc:a.forecast,impact:a.impact,
    cities,order:W.order.map(id=>by[id]),totalNeed:W.total_need_m3_day,allocated:W.allocated_m3_day,unmet:W.unmet_m3_day,
    sensors:a.sensors,recs:a.decisions};
}

/* ---------- dates ---------- */
const fd=(iso,o)=>new Date(iso+'T12:00:00').toLocaleDateString(LANGS[lang].loc,o);
const dShort=i=>fd(state.data.days[i].date,{day:'numeric',month:'short'}).replace('.','');
const dLong=i=>fd(state.data.days[i].date,{weekday:'long',day:'numeric',month:'long'});
const fmtTs=iso=>new Date(iso).toLocaleString(LANGS[lang].loc,{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}).replace('.','');

function covStatus(c){return c>=130?['info',t('cov_surplus')]:c>=100?['ok',t('cov_ok')]:c>=75?['warn',t('cov_tense')]:['crit',t('cov_deficit')]}
function cityStatus(s){return{crit:['crit',t('ct_crit')],low:['warn',t('ct_low')],ok:['ok',t('ct_ok')],full:['info',t('ct_full')]}[s]}

/* ---------- graphiques ---------- */
function niceMax(v){const p=Math.pow(10,Math.floor(Math.log10(v)));const m=v/p;return[1,2,4,5,8,10].find(x=>m<=x)*p}
function barChart(el,c){
  const W=Math.max(300,el.clientWidth||600),H=250,m={l:46,r:10,t:12,b:34};
  const iw=W-m.l-m.r,ih=H-m.t-m.b,n=c.cats.length,bw=iw/n,bar=Math.max(3,bw*.7);
  const tot=c.cats.map((_,i)=>sum(c.stacks.map(s=>s[i])));
  const top=Math.max(...tot.map((v,i)=>v+(c.whisk?c.whisk[i]:0)),...c.line);
  const maxV=niceMax(top*1.05),y=v=>m.t+ih-v/maxV*ih;
  let s=`<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(c.label)}">`;
  for(let i=0;i<=4;i++){const v=maxV*i/4,yy=y(v);s+=`<line class="grid" x1="${m.l}" x2="${W-m.r}" y1="${yy}" y2="${yy}"/><text class="ax" x="${m.l-6}" y="${yy+4}" text-anchor="end">${v%1?d1(v):v}</text>`}
  for(let i=0;i<n;i++){
    const x=m.l+i*bw+(bw-bar)/2;let acc=0;
    c.stacks.forEach((st,k)=>{s+=`<rect class="${c.cls[k]}" x="${x}" y="${y(acc+st[i])}" width="${bar}" height="${Math.max(0,st[i]/maxV*ih)}"/>`;acc+=st[i]});
    if(c.whisk){const cx=x+bar/2,w=c.whisk[i];s+=`<line class="whisk" x1="${cx}" x2="${cx}" y1="${y(tot[i]-w)}" y2="${y(tot[i]+w)}"/>`}
    if(tot[i]<c.line[i])s+=`<rect class="defmark" x="${x}" y="${H-m.b+4}" width="${bar}" height="3"/>`;
    if(i%c.every===0)s+=`<text class="ax" x="${x+bar/2}" y="${H-12}" text-anchor="middle">${c.cats[i]}</text>`;
  }
  let d='';c.line.forEach((v,i)=>{const x1=m.l+i*bw,x2=x1+bw;d+=(i?'L':'M')+x1+' '+y(v)+'L'+x2+' '+y(v)});
  s+=`<path class="needline" d="${d}"/><rect class="hov" x="0" y="${m.t}" width="${bw}" height="${ih}" hidden/></svg><div class="tip" hidden></div>`;
  el.innerHTML=s;
  const svg=el.firstChild,tip=el.querySelector('.tip'),hov=svg.querySelector('.hov');
  const hide=()=>{tip.hidden=true;hov.setAttribute('hidden','')};
  const show=ev=>{
    const r=svg.getBoundingClientRect(),i=Math.floor((ev.clientX-r.left-m.l)/bw);
    if(i<0||i>=n){hide();return}
    hov.setAttribute('x',m.l+i*bw);hov.removeAttribute('hidden');
    tip.innerHTML=c.tip(i);tip.hidden=false;
    tip.style.left=clamp(m.l+i*bw+bw/2-tip.offsetWidth/2,0,W-tip.offsetWidth)+'px';
  };
  svg.addEventListener('pointermove',show);svg.addEventListener('pointerdown',show);svg.addEventListener('pointerleave',hide);
}
function spark(c){
  const w=120,h=32,x=i=>1+i*(w-2)/23,mx=Math.max(...c.q)*1.1,y=v=>h-2-v/mx*(h-4);
  const pts=c.q.map((v,i)=>`${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const cls=c.det.kind==='leak'?'bad':c.det.kind==='frozen'?'frz':'';
  return`<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(t('spark_aria',c.n))}"><rect class="night" x="${x(1)}" y="0" width="${x(4)-x(1)}" height="${h}"/><polyline class="ln ${cls}" points="${pts}"/></svg>`;
}

/* ---------- carte schématique (pas de fond de carte : fonctionne hors ligne) ---------- */
const COAST=[[-9.8,31.8],[-9.8,31.5],[-9.7,30.7],[-9.65,30.42],[-9.75,29.9],[-9.85,29.55],[-10.17,29.37],[-10.05,28.95],[-10.4,28.7],[-11.1,28.45],
  [-11.6,28.15],[-12.2,28.0],[-12.93,27.94],[-13.2,27.65],[-13.43,27.14],[-13.9,26.7],[-14.45,26.12],[-14.9,25.2],[-15.45,24.4],[-15.93,23.7],
  [-16.2,22.9],[-16.5,22.1],[-17.0,21.35],[-17.05,20.6]];
const MAP={w:420,h:500,lon0:-17.6,lon1:-6.5,lat0:31.8,lat1:20.4};
const mx=lon=>(lon-MAP.lon0)/(MAP.lon1-MAP.lon0)*MAP.w, my=lat=>(MAP.lat0-lat)/(MAP.lat0-MAP.lat1)*MAP.h;
function renderMap(m){
  const land=COAST.map(([lo,la])=>`${mx(lo).toFixed(1)},${my(la).toFixed(1)}`).join(' ')+` ${MAP.w},${MAP.h} ${MAP.w},0`;
  let s=`<svg viewBox="0 0 ${MAP.w} ${MAP.h}" class="map" role="img" aria-label="${esc(t('map_aria'))}"><rect width="${MAP.w}" height="${MAP.h}" class="sea"/><polygon points="${land}" class="land"/>`;
  m.cities.forEach(c=>{
    const x=mx(c.lon),y=my(c.lat);
    s+=`<g><circle class="mk-city ${c.status}" cx="${x}" cy="${y}" r="8"><title>${esc(t('map_city',c.n,Math.round(c.lvl)))}</title></circle><text class="mk-name" x="${x+13}" y="${y+4}">${esc(c.n)}</text></g>`;
  });
  m.sites.forEach(p=>{
    let x=mx(p.lon);const y=my(p.lat),n=esc(p.name)+' · '+p.mw+' MW';
    if(m.cities.some(c=>Math.hypot(mx(c.lon)-x,my(c.lat)-y)<16))x-=16;   // ne pas masquer la ville voisine
    s+=p.type==='solar'?`<rect class="mk-solar" x="${x-5}" y="${y-5}" width="10" height="10"><title>${n}</title></rect>`
      :`<polygon class="mk-wind" points="${x},${y-6} ${x+6},${y+5} ${x-6},${y+5}"><title>${n}</title></polygon>`;
  });
  $('#map').innerHTML=s+'</svg>';
  const rows=m.order.map(c=>{const[sl,sn]=cityStatus(c.status);return`<li><span class="rank">${c.rank}</span><span class="cn">${esc(c.n)}</span><span class="pill ${sl}">${sn}</span><span class="num">${Math.round(c.lvl)} %</span></li>`}).join('');
  $('#mapKey').innerHTML=`<ol>${rows}</ol><p class="hint">${esc(t('map_leg_sites'))}</p>`;
}

/* ---------- page énergie ---------- */
const stat=(lab,val,sub,extra='')=>`<div class="stat"><div class="lab">${lab}</div><div class="val">${val}</div><div class="sub">${sub}</div>${extra}</div>`;
function fillSelect(sel,items,pick){
  if(sel.dataset.lang===lang&&sel.options.length===items.length)return;
  sel.innerHTML=items.map(([v,l])=>`<option value="${esc(v)}"${v===pick?' selected':''}>${esc(l)}</option>`).join('');sel.dataset.lang=lang;
}
function renderEnergy(m){
  const [cl,cn]=covStatus(m.coverage),mwh=t('u_mwh');
  $('#eStrip').innerHTML=
    stat(t('st_e1'),`${nb(m.avail)}<small>${mwh}</small>`,t('st_e1s',m.e.s,m.e.w))+
    stat(t('st_e2'),`${nb(m.needed)}<small>${mwh}</small>`,t('st_e2s',Math.min(m.totalNeed,m.PLANT)))+
    stat(t('st_e3'),`${Math.round(m.coverage)}<small>%</small>`,t('st_e3s'),`<span class="pill ${cl}">${cn}</span>`)+
    stat(t('st_e4'),`${nb(m.supplyCap)}<small>${t('u_m3d')}</small>`,t('st_e4s',m.PLANT));
  const box=$('#eChart');
  if(state.h==='jour'){
    $('#eChartTitle').textContent=t('c_day_title');
    $('#eLegendX').textContent=t('c_day_unit');
    const hh=m.hourly,need=m.needed/24;
    barChart(box,{label:t('c_day_lab'),
      cats:[...Array(24)].map((_,h)=>h+' h'),every:3,stacks:[hh.solar,hh.wind],cls:['c-solar','c-wind'],line:Array(24).fill(need),
      tip:i=>t('c_day_tip',i,hh.solar[i],hh.wind[i],need)});
    const tot=hh.solar.map((v,i)=>v+hh.wind[i]),peak=tot.indexOf(Math.max(...tot)),low=tot.filter(v=>v<need).length;
    $('#eRead').textContent=t('c_day_read',peak,tot[peak],low,need);
  }else{
    const ed=m.days;
    $('#eChartTitle').textContent=t('c_mo_title');
    $('#eLegendX').textContent=t('c_mo_unit');
    const cov=ed.map(x=>(x.solar_mwh+x.wind_mwh)/m.needed*100);
    barChart(box,{label:t('c_mo_lab'),
      cats:ed.map((_,d)=>dShort(d)),every:5,stacks:[ed.map(x=>x.solar_mwh),ed.map(x=>x.wind_mwh)],cls:['c-solar','c-wind'],
      whisk:ed.map(x=>(x.solar_mwh+x.wind_mwh)*x.uncertainty_pct/100),line:Array(ed.length).fill(m.needed),
      tip:i=>t('c_mo_tip',dShort(i),ed[i].solar_mwh+ed[i].wind_mwh,ed[i].uncertainty_pct,cov[i])});
    $('#eRead').textContent=t('c_mo_read',cov.filter(v=>v<100).length,cov.filter(v=>v>=130).length);
  }
  $('#eSource').textContent=m.fc.source==='meteo'?t('fc_meteo',m.fc.real_days,m.fc.updated?fmtTs(m.fc.updated):'–'):t('fc_sim');
  $('#eSites').innerHTML=m.sites.map(s=>{
    const solar=s.type==='solar';
    return`<tr><td>${esc(s.name)}</td><td><span class="dot" style="background:var(--${solar?'solar':'wind'})"></span>${solar?t('solar'):t('wind')}</td><td class="r num">${s.mw} MW</td><td class="r num">${d1(s.mwh)} ${mwh}</td><td class="r num">${Math.round(s.mwh/(s.mw*24)*100)} %</td></tr>`}).join('');
  fillSelect($('#plSite'),m.sites.map(s=>[s.id,`${s.name} (${s.mw} MW)`]),'wind-dakhla');
  renderPlan();
}

/* ---------- planificateur « et si » ---------- */
function renderPlan(){
  const out=$('#plOut'),p=state.plan;
  if(!p){out.innerHTML='';return}
  if(p.error){out.innerHTML=`<p class="hint">${esc(t('pl_err')+p.error)}</p>`;return}
  const b=p.base,v=p.variant,d=p.delta,R=t('pl_rows');
  // [libellé, base, variante, écart, mise en forme, +1 si une hausse est favorable, -1 si c'est une baisse]
  const rows=[
    [R[0],b.coverage_pct,v.coverage_pct,d.coverage_pct,x=>Math.round(x)+' %',1],
    [R[1],b.available_mwh,v.available_mwh,d.available_mwh,x=>nb(x)+' '+t('u_mwh'),1],
    [R[2],b.allocated_m3_day,v.allocated_m3_day,d.allocated_m3_day,x=>nb(x)+' '+t('u_m3d'),1],
    [R[3],b.unmet_m3_day,v.unmet_m3_day,d.unmet_m3_day,x=>nb(x)+' '+t('u_m3d'),-1],
    [R[4],b.cities_served,v.cities_served,d.cities_served,x=>String(x),1],
    [R[5],b.deficit_days,v.deficit_days,d.deficit_days,x=>String(x),-1],
    [R[6],b.value_mad_day,v.value_mad_day,d.value_mad_day,x=>nb(x)+' '+t('u_madd'),1],
    [R[7],b.co2_t_day,v.co2_t_day,d.co2_t_day,x=>d1(x)+' '+t('u_t'),1]];
  out.innerHTML=`<p class="read" style="margin:12px 0 8px"><b>${esc(t('pl_head',p.site.name,p.site.add_mw,d.coverage_pct,d.allocated_m3_day))}</b></p>
  <table><thead><tr><th></th><th class="r">${t('pl_cur')}</th><th class="r">${esc(t('pl_with',p.site.add_mw))}</th><th class="r">${t('pl_chg')}</th></tr></thead><tbody>`+
  rows.map(([l,x,y,dd,f,good])=>{
    const better=Math.abs(dd)<1e-9?'':(dd*good>0?'pos':'neg');
    return`<tr><td>${esc(l)}</td><td class="r num">${f(x)}</td><td class="r num">${f(y)}</td><td class="r num ${better}">${Math.abs(dd)<1e-9?'–':sgn(dd,f)}</td></tr>`}).join('')+'</tbody></table>';
}
async function runPlan(){
  const mw=parseFloat($('#plMw').value),site=$('#plSite').value;
  if(!(mw>0)){state.plan={error:'MW > 0'};renderPlan();return}
  try{state.plan=await api('/api/whatif?'+qs({site,add_mw:String(mw)}))}
  catch(err){state.plan={error:err.message}}
  renderPlan();
}

/* ---------- page eau ---------- */
function renderWater(m){
  const crit=m.cities.filter(c=>c.status==='crit').length,anom=m.cities.filter(c=>c.det.kind!=='ok').length,stopped=m.cities.filter(c=>c.full);
  $('#wStrip').innerHTML=
    stat(t('w_s1'),`${crit}<small>${t('w_s1u',m.cities.length)}</small>`,t('w_s1s'))+
    stat(t('w_s2'),`${anom}`,t('w_s2s'))+
    stat(t('w_s3'),`${nb(m.allocated)}<small>${t('u_m3d')}</small>`,t('w_s3s',m.totalNeed))+
    stat(t('w_s4'),`${stopped.length}`,stopped.length?t('w_s4s',stopped.map(c=>esc(c.n)).join(', ')):t('w_s4n'));
  renderMap(m);
  const pc=v=>clamp(v/m.PLANT*100,0,100);
  $('#wAlloc').innerHTML=`<p class="read" style="margin:0">${t('al_txt',m.allocated,m.PLANT)}${m.unmet>0?t('al_unmet',m.unmet):t('al_all')}</p>
    <div class="alloc-bar" role="img" aria-label="${esc(t('al_aria',m.allocated,m.PLANT))}"><div class="fill" style="width:${pc(m.allocated)}%"></div><div class="mark" style="left:${pc(m.supplyCap)}%"></div></div>
    <div class="alloc-key"><span>${t('al_k1')}</span><span>${t('al_k2',m.supplyCap)}</span></div>`;
  const sel=$('#leakCity');
  fillSelect(sel,m.cities.map(c=>[c.id,c.n]),'tiznit');
  const btn=$('#leakBtn');
  btn.textContent=state.inj?t('lk_rm'):t('lk_sim');sel.disabled=!!state.inj;
  $('#wSensors').innerHTML=m.sensors.map(s=>`<tr><td>${esc(s.name)}</td><td>${s.source==='capteurs'?`<span class="pill ok">${t('sn_cap')}</span>`:`<span class="pill mute">${t('sn_sim')}</span>`}</td><td class="r num">${s.readings_24h} / 24</td><td class="r num">${s.last_reading?fmtTs(s.last_reading):'–'}</td><td class="r num">${s.level_pct==null?'–':Math.round(s.level_pct)+' %'}</td></tr>`).join('');
  $('#wCities').innerHTML=m.order.map(c=>{
    const [sl,sn]=cityStatus(c.status);
    const det=c.det.kind==='leak'?['crit',t('det_leak')]:c.det.kind==='frozen'?['warn',t('det_frozen')]:['ok',t('det_ok')];
    const act=c.need===0?['info',t('act_stop')]:c.alloc>=c.need?['ok',t('act_full')]:c.alloc>0?['warn',t('act_part',Math.round(c.alloc/c.need*100))]:['crit',t('act_def')];
    const live=c.src.flow==='capteurs';
    return`<article class="city">
      <div class="cell"><span class="rank">${t('prio')} ${c.rank}</span><div class="cname">${esc(c.n)}</div><div class="cmeta">${nb(c.pop*1000)} ${t('inhab')} · ${t('need_score')} <span class="num">${c.score}</span></div></div>
      <div class="cell"><div class="k">${t('k_tank')}</div><div class="lvl"><div class="track"><div class="f ${c.status}" style="width:${clamp(c.lvl,0,100)}%"></div><span class="t" style="left:20%"></span><span class="t" style="left:70%"></span></div><span class="pc">${Math.round(c.lvl)} %</span></div><div style="margin-top:6px"><span class="pill ${sl}">${sn}</span></div></div>
      <div class="cell"><div class="k">${t('k_aut')}</div><div class="allocnum">${d1(c.aut)} <small>${t('k_days')}</small></div></div>
      <div class="cell"><div class="k">${t('k_flow')}</div>${spark(c)}<div style="margin-top:4px"><span class="pill ${det[0]}">${det[1]}</span><span class="src${live?' live':''}">${live?t('src_cap'):t('src_sim')}</span></div></div>
      <div class="cell"><div class="k">${t('k_leak')}</div><div class="allocnum">${c.leak?nb(c.leak)+' <small>'+t('u_m3d')+'</small>':t('none')}</div><div class="cmeta">${c.det.kind==='frozen'?t('flat'):t('night',Math.round(c.ratio*100))}</div></div>
      <div class="cell"><div class="k">${t('k_alloc')}</div><div class="allocnum">${nb(c.alloc)} <small>/ ${nb(c.need)} ${t('u_m3d')}</small></div><div class="minibar"><i style="width:${c.need?c.alloc/c.need*100:0}%"></i></div><span class="pill ${act[0]}">${act[1]}</span></div>
    </article>`}).join('');
}

/* ---------- page décisions ---------- */
const UCLS=['crit','warn','info'];
const srcBadge=(label,v)=>`<span class="srcb ${v==='capteurs'||v==='meteo'?'live':''}">${esc(label)} : ${esc(t('src_'+v))}</span>`;
function renderDecisions(m){
  const L=m.recs,U=t('urg');
  const cnt=[0,0,0];L.forEach(r=>{if(!r.status)cnt[r.urgency]++});
  const done=L.filter(r=>r.status==='valide').length;
  $('#dStrip').innerHTML=stat(t('d_s1'),`${cnt[0]}`,t('d_s1s'))+stat(t('d_s2'),`${cnt[1]}`,t('d_s2s'))+stat(t('d_s3'),`${cnt[2]}`,t('d_s3s'))+stat(t('d_s4'),`${done}`,t('d_s4s',L.length));
  $('#dRecs').innerHTML=L.length?L.map(r=>{
    const st=r.status,city=r.city&&m.cities.find(c=>c.id===r.city);
    const badges=(r.sources.flow?srcBadge(t('r_src_flow'),r.sources.flow==='capteurs'?'capteurs':'simulation'):'')+srcBadge(t('r_src_en'),r.sources.energy);
    return`<article class="rec${st?' done':''}" data-id="${esc(r.id)}">
      <div class="rec-head"><span class="pill ${UCLS[r.urgency]}">${U[r.urgency]}</span><h3>${esc(r.title)}</h3></div>
      <div class="rec-cols">
        <div><h4>${t('r_why')}</h4><ul>${r.why.map(w=>`<li>${esc(w)}</li>`).join('')}</ul></div>
        <div><h4>${t('r_act')}</h4><p>${esc(r.action)}</p></div>
        <div><h4>${t('r_eff')}</h4><dl>${r.impact.map(i=>`<dt>${esc(i[0])}</dt><dd>${esc(i[1])}</dd>`).join('')}</dl></div>
      </div>
      <div class="rec-data"><h4>${t('r_data')}</h4>
        <div class="ev">${r.evidence.map(e=>`<div class="chip"><span>${esc(e[0])}</span><b class="num">${esc(e[1])}</b></div>`).join('')}${city?`<div class="chip spk"><span>${t('r_flow')}</span>${spark(city)}</div>`:''}</div>
        <div class="srcs">${badges}</div>
      </div>
      <div class="rec-foot"><button type="button" class="btn${st==='valide'?' primary':''}" data-act="valide">${t('r_valide')}</button><button type="button" class="btn${st==='reporte'?' primary':''}" data-act="reporte">${t('r_reporte')}</button><span class="state">${st==='valide'?t('r_st_v'):st==='reporte'?t('r_st_r'):t('r_st_w')}</span></div>
    </article>`}).join(''):`<div class="empty">${t('r_empty')}</div>`;
  const LS={valide:['ok',t('lg_valide')],reporte:['warn',t('lg_reporte')],annule:['mute',t('lg_annule')]};
  $('#dLog').innerHTML=state.log.length?state.log.map(x=>{
    const [pl,pn]=LS[x.status]||['mute',x.status];
    return`<tr><td class="num">${fmtTs(x.decided_at)}</td><td style="white-space:normal">${esc(x.title)}</td><td><span class="pill ${pl}">${pn}</span></td></tr>`}).join('')
    :`<tr><td colspan="3" style="white-space:normal;color:var(--muted)">${t('lg_empty')}</td></tr>`;
}

/* ---------- page impact ---------- */
function renderImpact(m){
  const I_=m.impact,a=I_.assumptions;
  $('#iStrip').innerHTML=
    stat(t('i_s1'),`${nb(I_.total_value_mad_day)}<small>${t('u_mad')}</small>`,t('i_s1s',I_.total_value_mad_year))+
    stat(t('i_s2'),`${d1(I_.total_co2_t_day)}<small>${t('u_t')}</small>`,t('i_s2s',I_.total_co2_t_year))+
    stat(t('i_s3'),`${nb(I_.total_water_saved_m3_day)}<small>${t('u_m3d')}</small>`,t('i_s3s'))+
    stat(t('i_s4'),`${nb(I_.renewable_used_mwh)}<small>${t('u_mwh')}</small>`,t('i_s4s'));
  const row=(l,w,e,mad,co2)=>`<tr><td>${esc(l)}</td><td class="r num">${w==null?'–':nb(w)+' '+t('u_m3d')}</td><td class="r num">${d1(e)} ${t('u_mwh')}</td><td class="r num">${nb(mad)} ${t('u_mad')}</td><td class="r num">${d1(co2)} ${t('u_t')}</td></tr>`;
  $('#iRows').innerHTML=
    row(t('i_r1'),null,I_.renewable_used_mwh,I_.renewable_value_mad_day,I_.renewable_co2_t_day)+
    row(t('i_r2'),I_.leak_water_m3_day,I_.leak_energy_mwh_day,I_.leak_value_mad_day,I_.leak_co2_t_day)+
    row(t('i_r3'),I_.stop_water_m3_day,I_.stop_energy_mwh_day,I_.stop_value_mad_day,I_.stop_co2_t_day)+
    `<tr><td><b>${t('i_total')}</b></td><td class="r num">${nb(I_.total_water_saved_m3_day)} ${t('u_m3d')}</td><td class="r num">${d1(I_.renewable_used_mwh+I_.leak_energy_mwh_day+I_.stop_energy_mwh_day)} ${t('u_mwh')}</td><td class="r num"><b>${nb(I_.total_value_mad_day)} ${t('u_mad')}</b></td><td class="r num">${d1(I_.total_co2_t_day)} ${t('u_t')}</td></tr>`;
  $('#iAsm').textContent=t('i_asm',a.grid_price_mad_mwh,a.co2_t_per_mwh,I_.backup_gap_mwh,I_.backup_cost_mad_day);
}

/* ---------- affichage ---------- */
const PAGES=['energie','eau','decisions','impact'];
function render(){
  const m=state.data;if(!m)return;
  const h=(location.hash||'#energie').slice(1),p=PAGES.includes(h)?h:'energie';
  PAGES.forEach(x=>{$('#p-'+x).hidden=x!==p;const tb=$('#tab-'+x);if(x===p)tb.setAttribute('aria-current','page');else tb.removeAttribute('aria-current')});
  last=m;
  const urg=m.recs.filter(r=>r.urgency===0&&!r.status).length;
  const b=$('#badge');b.textContent=urg;b.hidden=!urg;
  $('#simTag').textContent=t('mode_'+m.mode)||'';
  $('#today').textContent=t('today_pre')+dLong(0);
  $('#scSeg').querySelectorAll('button').forEach(x=>x.setAttribute('aria-pressed',String(x.dataset.sc===state.sc)));
  $('#hSeg').querySelectorAll('button').forEach(x=>x.setAttribute('aria-pressed',String(x.dataset.h===state.h)));
  if(p==='energie')renderEnergy(m);
  if(p==='eau')renderWater(m);
  if(p==='decisions')renderDecisions(m);
  if(p==='impact')renderImpact(m);
}

/* ---------- événements ---------- */
$('#scSeg').addEventListener('click',e=>{const b=e.target.closest('button');if(b){state.sc=b.dataset.sc;state.plan=null;load()}});
$('#hSeg').addEventListener('click',e=>{const b=e.target.closest('button');if(b){state.h=b.dataset.h;render()}});
$('#leakBtn').addEventListener('click',()=>{state.inj=state.inj?null:$('#leakCity').value;load()});
$('#plBtn').addEventListener('click',runPlan);
$('#langBtn').addEventListener('click',()=>{
  lang=lang==='fr'?'ar':'fr';
  try{localStorage.setItem('wahasud-lang',lang)}catch(e){}
  state.plan=null;applyStatic();load();
});
$('#dRecs').addEventListener('click',async e=>{
  const b=e.target.closest('button[data-act]');if(!b||state.busy||!state.data)return;
  const id=b.closest('.rec').dataset.id,rec=state.data.recs.find(r=>r.id===id);if(!rec)return;
  const status=rec.status===b.dataset.act?'annule':b.dataset.act;   // un second clic annule
  state.busy=true;
  try{
    await api('/api/decisions',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id,status,scenario:state.sc,leak:state.inj||'',lang})});
    await load();
  }catch(err){banner(err.message,true)}
  finally{state.busy=false}
});
$('#copyBtn').addEventListener('click',()=>{
  if(!state.data)return;
  const L=state.data.recs,U=t('urg');
  const txt=t('cp_head')+'\n\n'+L.map((r,i)=>`${i+1}. [${U[r.urgency]}] ${r.title}\n   ${t('cp_why')} : ${r.why.join(' ; ')}\n   ${t('cp_act')} : ${r.action}\n   ${t('cp_eff')} : ${r.impact.map(x=>x[0]+' '+x[1]).join(' ; ')}\n   ${t('cp_ev')} : ${r.evidence.map(x=>x[0]+' '+x[1]).join(' ; ')}`).join('\n\n');
  const out=$('#copyOut');
  const fallback=()=>{out.innerHTML=`<textarea class="copybox" id="copyTxt" readonly aria-label="${esc(t('cp_label'))}"></textarea>`;const ta=$('#copyTxt');ta.value=txt;ta.focus();ta.select()};
  if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(txt).then(()=>{out.innerHTML=`<p class="hint" role="status">${t('cp_done')}</p>`},fallback)}else fallback();
});
addEventListener('hashchange',()=>{render();scrollTo(0,0)});
let rt;addEventListener('resize',()=>{clearTimeout(rt);rt=setTimeout(render,120)});
setInterval(()=>{if(!document.hidden&&!state.busy)load()},60000);   // nouveaux relevés des capteurs
document.querySelectorAll('select').forEach(s=>s.setAttribute('data-lang',''));
applyStatic();
load();
})();
