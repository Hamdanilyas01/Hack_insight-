'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const NS = 'http://www.w3.org/2000/svg';
const state = {data:null, guidance:null, api:false, view:'network', filter:null, selected:'forecast', zoom:1, pan:{x:0,y:0}, unit:'C', orbit:false, paused:matchMedia('(prefers-reduced-motion: reduce)').matches};
const groups = {
  weather:{color:'#a6dcc5',fill:'mint',label:'Weather signals'},
  forecast:{color:'#d9c28d',fill:'amber',label:'Heat forecast'},
  safety:{color:'#d8a0a5',fill:'rose',label:'Worker safety'},
  evidence:{color:'#ae98d6',fill:'violet',label:'Verified evidence'}
};
const nodes = [
  {id:'weather',group:'weather',x:472,y:264,r:16,label:'Weather signals',meta:'OPEN-METEO · KARACHI'},
  {id:'temperature',group:'weather',x:362,y:186,r:9,label:'Air temperature',parent:'weather'},
  {id:'humidity',group:'weather',x:519,y:139,r:10,label:'Relative humidity',parent:'weather'},
  {id:'wind',group:'weather',x:305,y:343,r:8,label:'Wind speed',parent:'weather'},
  {id:'dew',group:'weather',x:372,y:419,r:7,label:'Dew point',parent:'weather'},
  {id:'forecast',group:'forecast',x:865,y:272,r:19,label:'24-hour forecast',meta:'MACHINE LEARNING'},
  {id:'heatindex',group:'forecast',x:1037,y:176,r:10,label:'Heat index',parent:'forecast'},
  {id:'uncertainty',group:'forecast',x:1062,y:370,r:11,label:'Prediction interval',parent:'forecast'},
  {id:'risk',group:'forecast',x:859,y:125,r:9,label:'Risk category',parent:'forecast'},
  {id:'safety',group:'safety',x:834,y:574,r:17,label:'Worker safety',meta:'GUIDANCE → ACTION'},
  {id:'hydration',group:'safety',x:1005,y:524,r:11,label:'Hydration',parent:'safety'},
  {id:'rest',group:'safety',x:1000,y:667,r:9,label:'Rest & shade',parent:'safety'},
  {id:'acclimatization',group:'safety',x:767,y:714,r:10,label:'Acclimatization',parent:'safety'},
  {id:'evidence',group:'evidence',x:471,y:556,r:17,label:'Verified evidence',meta:'TRACEABLE BY DESIGN'},
  {id:'model',group:'evidence',x:336,y:538,r:10,label:'ML performance',parent:'evidence'},
  {id:'warehouse',group:'evidence',x:530,y:705,r:11,label:'Data warehouse',parent:'evidence'},
  {id:'quality',group:'evidence',x:321,y:663,r:8,label:'Quality checks',parent:'evidence'},
  {id:'sources',group:'evidence',x:644,y:615,r:9,label:'Official sources',parent:'evidence'}
];
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const fmt = (n, digits=1) => Number.isFinite(Number(n)) ? Number(n).toFixed(digits) : '—';
const temp = n => state.unit === 'F' ? n*1.8+32 : n;
const date = (value, options={}) => new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Karachi',day:'numeric',month:'short',hour:'numeric',minute:'2-digit',hour12:true,...options}).format(new Date(value));
const hour = value => date(value,{day:undefined,month:undefined,minute:undefined});
const svgEl = (name, attrs, parent) => {const el=document.createElementNS(NS,name); for(const [k,v] of Object.entries(attrs))el.setAttribute(k,v); if(parent)parent.appendChild(el); return el;};
function toast(message){$('#toast').textContent=message;$('#toast').classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('#toast').classList.remove('show'),3000);}

async function loadData(initial=false){
  $('#refresh-button').disabled=true;
  let data;
  try {
    const response=await fetch('/api/dashboard',{signal:AbortSignal.timeout(5000)});
    if(!response.ok || !response.headers.get('content-type')?.includes('application/json'))throw Error('Python API unavailable');
    data=await response.json();state.api=true;
  } catch {
    state.api=false;
    try {const response=await fetch('snapshot.json',{cache:'no-store'});if(!response.ok)throw Error('Missing snapshot');data=await response.json();}
    catch {$('#data-mode').textContent='Evidence unavailable';$('#updated-label').textContent='Try refreshing';$('#refresh-button').disabled=false;$('#detail-content').innerHTML='<h2 class="detail-title">Evidence unavailable</h2><p class="detail-subtitle">The saved evidence could not be loaded. Refresh to try again.</p>';toast('Unable to load project evidence.');return;}
  }
  state.data=data;
  const age=Date.now()-new Date(data.freshness.collected_at_utc).getTime();
  data.fresh=data.verified && age>=0 && age<=3*60*60*1000;
  const bundled=state.api && ['vercel','deployment'].includes(data.storage?.location);
  const apiName=data.storage?.location==='vercel'?'Vercel API':'Packaged API';
  $('#data-mode').textContent=state.api?(bundled?(data.fresh?apiName+' connected':apiName+' · stale data'):(data.fresh?'Warehouse connected':'Stored data · stale')):'Project snapshot';
  $('.data-status .status-dot').classList.toggle('stale',!data.fresh || !state.api);
  $('#updated-label').textContent=date(data.freshness.collected_at_utc)+' PKT';
  $('.data-status').title=`Collected ${date(data.freshness.collected_at_utc)} PKT. ${state.api?(bundled?'Read from the read-only DuckDB file bundled with this deployment. Data updates require redeployment.':'Read from local DuckDB.'):'Saved project evidence; this is not a live weather feed.'} ${data.fresh?'':'Older than the three-hour freshness limit.'}`;
  $('#dock-heat').innerHTML=fmt(data.forecast.model_prediction_c)+'<small>°C</small>';
  $('#dock-risk').textContent=data.forecast.model_risk_category;
  $('#dock-mae').innerHTML=fmt(data.metrics.test_model_mae_c,2)+'<small>°C</small>';
  if(initial){buildNetwork();if(innerWidth<=720)$('#detail-panel').classList.add('closed');}
  showDetail(state.selected,false);
  if(state.view!=='network')showView(state.view);
  $('#refresh-button').disabled=false;
  if(!initial)toast(state.api?'Warehouse evidence refreshed.':'Saved project snapshot reloaded.');
}

function positions(){
  if(!state.orbit)return nodes;
  return nodes.map((n,i)=>{const groupIndex=Object.keys(groups).indexOf(n.group);const groupNodes=nodes.filter(m=>m.group===n.group);const j=groupNodes.findIndex(m=>m.id===n.id);const a=-Math.PI+groupIndex*Math.PI/2+j*.19;const r=n.parent?290:160;return {...n,x:705+Math.cos(a)*r,y:398+Math.sin(a)*r};});
}
function buildNetwork(){
  const points=positions();const edgeGroup=$('#edges'),nodeGroup=$('#nodes'),particleGroup=$('#particles');
  edgeGroup.replaceChildren();nodeGroup.replaceChildren();particleGroup.replaceChildren();
  [126,243,352].forEach(r=>svgEl('circle',{cx:705,cy:398,r,class:'orbit-ring'},edgeGroup));
  const pathFor=(a,b)=>{const bend=b.parent?20:0;return `M ${a.x} ${a.y} Q ${(a.x+b.x)/2+bend} ${(a.y+b.y)/2-bend} ${b.x} ${b.y}`;};
  points.forEach((node,i)=>{
    const parent=points.find(p=>p.id===node.parent)||{x:705,y:398};
    const path=svgEl('path',{d:pathFor(parent,node),class:`graph-edge ${node.parent?'':'main'}`,id:`edge-${node.id}`,'data-group':node.group},edgeGroup);
    const pulse=svgEl('circle',{r:node.parent?1.5:2,fill:groups[node.group].color,opacity:'.85','data-group':node.group},particleGroup);
    const motion=svgEl('animateMotion',{dur:`${6+i%5}s`,repeatCount:'indefinite',begin:`-${i*.73}s`,path:path.getAttribute('d')},pulse);
    for(let j=0;j<(node.parent?2:4);j++){
      const seed=(i*7+j*13+3);const a=seed*2.39996;const distance=26+(seed%38);const x=node.x+Math.cos(a)*distance,y=node.y+Math.sin(a)*distance;
      svgEl('path',{d:`M${node.x},${node.y} L${x},${y}`,class:'graph-edge','data-group':node.group,opacity:'.35'},edgeGroup);
      svgEl('circle',{cx:x,cy:y,r:1.7+(j%2),fill:groups[node.group].color,opacity:'.6','data-group':node.group},particleGroup);
      svgEl('circle',{cx:x,cy:y,r:4,fill:groups[node.group].color,filter:'url(#glow)',opacity:'.4','data-group':node.group},particleGroup);
    }
    const g=svgEl('g',{class:'graph-node',transform:`translate(${node.x} ${node.y})`,tabindex:'0',role:'button','aria-label':`Explore ${node.label}`,'data-id':node.id,'data-group':node.group},nodeGroup);
    svgEl('title',{},g).textContent=`${node.label} — select to explore evidence`;
    svgEl('circle',{r:Math.max(22,node.r+5),fill:'transparent'},g);
    svgEl('circle',{r:node.r+9,fill:groups[node.group].color,filter:'url(#glow)',class:'node-halo ambient',style:`animation-delay:-${i*.41}s`},g);
    svgEl('circle',{r:node.r+3,fill:'none',stroke:groups[node.group].color,'stroke-opacity':'.25','stroke-width':'.7'},g);
    svgEl('circle',{r:node.r,fill:`url(#${groups[node.group].fill}-fill)`,stroke:groups[node.group].color,'stroke-opacity':'.7','stroke-width':'.8',class:'node-body'},g);
    svgEl('circle',{r:node.r+9,fill:'none',stroke:groups[node.group].color,'stroke-width':'1',class:'selection-ring'},g);
    svgEl('circle',{cx:-node.r*.3,cy:-node.r*.35,r:node.r*.23,fill:'#efffe9',opacity:'.35',filter:'url(#glow)'},g);
    const leftLabel=['temperature','wind','dew','model','quality'].includes(node.id);
    const label=svgEl('text',{x:leftLabel?-node.r-11:node.r+12,y:node.meta?-2:3,'text-anchor':leftLabel?'end':'start',class:'node-label'},g);label.textContent=node.label;
    const meta=svgEl('text',{x:leftLabel?-node.r-11:node.r+12,y:node.meta?12:16,'text-anchor':leftLabel?'end':'start',class:'node-meta'},g);meta.textContent=node.meta||nodeMeta(node.id);
    const select=()=>{showView('network');showDetail(node.id);};g.addEventListener('click',select);g.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();select();}});
  });
  [['weather','evidence'],['forecast','safety'],['humidity','heatindex'],['quality','forecast'],['sources','safety']].forEach(([a,b])=>{const p=points.find(n=>n.id===a),q=points.find(n=>n.id===b);svgEl('path',{d:`M${p.x},${p.y} L${q.x},${q.y}`,class:'graph-edge cross'},edgeGroup);});
  const core=svgEl('g',{class:'graph-node core-node',tabindex:'0',role:'button','aria-label':'Karachi heat intelligence overview','data-id':'core'},nodeGroup);
  svgEl('circle',{cx:705,cy:398,r:60,fill:'#e8d29b',opacity:'.15',filter:'url(#core-glow)'},core);
  [39,45,51].forEach((r,i)=>svgEl('circle',{cx:705,cy:398,r,fill:'none',stroke:'#c8bd87','stroke-width':i===1?'.8':'.5','stroke-opacity':'.35','stroke-dasharray':i===1?'1 4':'none',class:i===1?'core-orbit':''},core));
  svgEl('circle',{cx:705,cy:398,r:32,fill:'url(#core-fill)',stroke:'#e5d09a','stroke-width':'1'},core);
  svgEl('circle',{cx:705,cy:398,r:25,fill:'none',stroke:'#fff2c3','stroke-width':'.5','stroke-opacity':'.6'},core);
  svgEl('circle',{cx:705,cy:398,r:19,fill:'none',stroke:'#fff2c3','stroke-width':'.5','stroke-opacity':'.4'},core);
  svgEl('text',{x:705,y:402,'text-anchor':'middle',class:'core-value'},core).textContent='✳';
  svgEl('text',{x:705,y:464,'text-anchor':'middle',class:'core-label'},core).textContent='Karachi';
  svgEl('text',{x:705,y:480,'text-anchor':'middle',class:'core-meta'},core).textContent='HEAT INTELLIGENCE';
  core.addEventListener('click',()=>showDetail('core'));core.addEventListener('keydown',e=>{if(e.key==='Enter'){showDetail('core');}});
  updateFilter();updateSelection();
}
function nodeMeta(id){const d=state.data;if(!d)return '';const w=d.current,f=d.forecast;return ({temperature:fmt(w.temperature_c)+' °C',humidity:fmt(w.humidity_pct,0)+' %',wind:fmt(w.wind_speed_ms)+' m/s',dew:fmt(w.dew_point_c)+' °C',heatindex:fmt(f.model_prediction_c)+' °C · ML',uncertainty:'HISTORICALLY CALIBRATED',risk:f.model_risk_category.toUpperCase(),hydration:'CDC NIOSH',rest:'NIOSH · HSE',acclimatization:'GRADUAL EXPOSURE',model:fmt(d.metrics.test_model_mae_c,2)+' °C TEST MAE',warehouse:'DUCKDB · GOLD LAYER',quality:d.verified?'EVIDENCE CHECKS PASSED':'CHECKS UNAVAILABLE',sources:'NIOSH · HSE · NOAA'})[id]||'';}
function updateSelection(){
  $$('.graph-node').forEach(n=>n.classList.toggle('selected',n.dataset.id===state.selected));
  $$('.graph-edge').forEach(e=>e.classList.toggle('selected-edge',e.id===`edge-${state.selected}`));
}
function updateFilter(){
  $$('[data-group]',$('#network')).forEach(el=>{el.style.opacity=!state.filter||el.dataset.group===state.filter?'':'0.08';});
  $$('.legend-item').forEach(b=>b.setAttribute('aria-pressed',b.dataset.filter===state.filter));
}
function showDetail(id,open=true){
  state.selected=id;updateSelection();const d=state.data;if(!d)return;const f=d.forecast,w=d.current;
  const source=(label)=>`<div class="source-line">EVIDENCE<br><code>${escapeHTML(label)}</code><br>Collected ${escapeHTML(date(d.freshness.collected_at_utc))} PKT${!d.fresh?' · STALE':''}</div>`;
  const grid=(items)=>`<div class="detail-grid">${items.map(([v,k])=>`<div><strong>${v}</strong><span>${escapeHTML(k)}</span></div>`).join('')}</div>`;
  const section=(title,text)=>`<div class="detail-section"><h3>${escapeHTML(title)}</h3><p>${escapeHTML(text)}</p></div>`;
  const button=(label,action)=>`<button class="detail-action" data-action="${action}">${escapeHTML(label)} <span>↗</span></button>`;
  const headings={core:['CONNECTED INTELLIGENCE','Karachi, Pakistan','A connected picture of heat, from weather signals to workplace action.'],forecast:['24-HOUR ML FORECAST','The heat outlook.','Karachi · '+date(f.target_time_utc)+' PKT'],weather:['WEATHER EVIDENCE','The conditions.','Weather inputs from the latest stored Open-Meteo snapshot.'],temperature:['WEATHER SIGNAL','Air temperature','Stored weather input · not a personal exposure measurement.'],humidity:['WEATHER SIGNAL','Relative humidity','One of the key inputs behind the heat index.'],wind:['WEATHER SIGNAL','Wind speed','Wind measured at 10 metres, provided in metres per second.'],dew:['WEATHER SIGNAL','Dew point','The temperature at which air becomes saturated.'],heatindex:['HEAT FORECAST','More than temperature.','The modeled heat index combines weather signals.'],uncertainty:['MODEL UNCERTAINTY','A range, not a promise.','A historically calibrated interval around the 24-hour prediction.'],risk:['DERIVED RISK','Know the category.','Risk categories are derived from heat index, not worker illness outcomes.'],safety:['WORKER SAFETY','Turn insight into action.','Cited general guidance for people working in hot conditions.'],hydration:['WORKER SAFETY','Stay hydrated.','Official workplace heat guidance from CDC NIOSH.'],rest:['WORKER SAFETY','Rest. Recover.','Workplace guidance from CDC NIOSH and UK HSE.'],acclimatization:['WORKER SAFETY','Build up gradually.','Evidence-based guidance on getting used to hot work.'],evidence:['VERIFIED EVIDENCE','Every insight has a source.','Trace weather inputs, model evidence and official guidance.'],model:['MODEL EVALUATION','Measured performance.','HistGradientBoosting · held-out 2025 test set.'],warehouse:['DATA WAREHOUSE','A traceable foundation.','Read-only evidence from the project’s DuckDB gold views.'],quality:['QUALITY ASSURANCE','Trust, then verify.','The latest prediction must match a validated weather ingestion.'],sources:['OFFICIAL GUIDANCE','Grounded in evidence.','Three official sources in the project’s retrieval knowledge base.']};
  const [tag,title,subtitle]=headings[id]||headings.forecast;
  let html=`<span class="detail-tag">${escapeHTML(tag)}</span><h2 class="detail-title">${escapeHTML(title)}</h2><p class="detail-subtitle">${escapeHTML(subtitle)}</p>`;
  if(['forecast','heatindex','risk','core'].includes(id)){
    html+=`<div class="metric-hero"><div class="metric-label">PREDICTED HEAT INDEX</div><div class="metric-number">${fmt(f.model_prediction_c)}<small>°C</small></div><span class="risk-badge">◈ ${escapeHTML(f.model_risk_category)}</span><div class="risk-scale"><i></i><i></i><i></i><i></i><i></i></div><div class="scale-labels"><span>BELOW CAUTION</span><span>EXTREME DANGER</span></div></div>`;
    html+=grid([[`${fmt(f.interval_lower_c)}–${fmt(f.interval_upper_c)}<small>°C</small>`,'Historical interval'],['24<small>hours</small>','Prediction horizon'],[fmt(d.metrics.test_model_mae_c,2)+'<small>°C</small>','Held-out test MAE'],['Karachi','Initial focus city']]);
    html+=section('WHAT THIS MEANS','Heat-index categories help contextualize the forecast. Sunlight, exertion, protective equipment and individual conditions also affect workplace heat stress.');
    html+=button('Explore the forecast','forecast')+source('gold.latest_live_ml');
  }else if(['weather','temperature','humidity','wind','dew'].includes(id)){
    const values={weather:[w.temperature_c,'°C'],temperature:[w.temperature_c,'°C'],humidity:[w.humidity_pct,'%'],wind:[w.wind_speed_ms,'m/s'],dew:[w.dew_point_c,'°C']};
    const [value,unit]=values[id];html+=`<div class="metric-hero"><div class="metric-label">${id==='weather'?'AIR TEMPERATURE':'STORED INPUT'}</div><div class="metric-number">${fmt(value)}<small>${unit}</small></div><div class="metric-label">${date(w.time_utc)} PKT</div></div>`;
    html+=grid([[fmt(w.temperature_c)+'<small>°C</small>','Air temperature'],[fmt(w.humidity_pct,0)+'<small>%</small>','Relative humidity'],[fmt(w.wind_speed_ms)+'<small>m/s</small>','Wind speed'],[fmt(w.dew_point_c)+'<small>°C</small>','Dew point']]);
    html+=section('FROM WEATHER TO HEAT RISK','The model uses weather inputs and time-based features to predict the heat index 24 hours ahead. These values belong to the stored snapshot, not a live sensor.');html+=button('See hourly conditions','forecast')+source('gold.latest_live_weather');
  }else if(id==='uncertainty'){
    html+=`<div class="metric-hero"><div class="metric-label">HISTORICAL INTERVAL · °C</div><div class="metric-number" style="font-size:31px">${fmt(f.interval_lower_c)}–${fmt(f.interval_upper_c)}</div></div>`+section('HOW TO READ IT','The interval was calibrated on historical ERA5 data. Coverage with live forecast inputs has not yet been verified. It is not a guarantee of workplace safety.');
    html+=grid([[fmt(d.metrics.interval_nominal_coverage*100,0)+'<small>%</small>','Nominal coverage'],[fmt(d.metrics.interval_empirical_coverage*100,1)+'<small>%</small>','Historical test coverage']])+button('Inspect model evidence','evidence')+source('gold.latest_live_ml');
  }else if(['safety','hydration','rest','acclimatization'].includes(id)){
    html+=section('CITED GUIDANCE','Explore the project’s existing retrieval assistant for source-backed workplace precautions. It retrieves official guidance and does not generate medical advice.');
    [['Water & hydration','hydration'],['Rest breaks & shade','rest'],['Gradual acclimatization','acclimatization']].forEach(([label,key])=>html+=button(label,'ask-'+key));
    html+=section('RESPONSIBLE USE','General precautions do not replace an on-site heat-risk assessment. Weather-derived heat index is not occupational WBGT.');
    html+=`<div class="source-line">GUIDANCE SOURCES<br>CDC NIOSH · UK HSE · NOAA NWS</div>`;
  }else{
    html+=grid([[fmt(d.metrics.test_model_mae_c,2)+'<small>°C</small>','Model test MAE'],[fmt(d.metrics.test_model_r2,3),'Model test R²'],[Number(d.metrics.test_rows).toLocaleString(),'Held-out test rows'],['03','Official guidance sources']]);
    html+=section('EVIDENCE STATUS',`${d.verified?'The stored forecast passed data quality checks and matches the latest ingestion.':'Forecast verification could not be confirmed.'} ${d.fresh?'The snapshot is within the three-hour freshness limit.':'The snapshot is older than the three-hour freshness limit.'}`);
    if(id==='quality'||id==='warehouse')html+=section('SNAPSHOT ID',f.snapshot_id);
    if(id==='model')html+=section('BASELINE COMPARISON',`Test MAE is ${fmt(d.metrics.test_model_mae_c,3)}°C, compared with ${fmt(d.metrics.test_baseline_mae_c,3)}°C for the same-hour previous-day baseline.`);
    html+=button('Open the evidence library','evidence')+source(id==='model'?'modeling/evaluations/model_metrics':id==='sources'?'rag/source_registry.json':'gold.latest_live_ml');
  }
  $('#detail-content').innerHTML=html;
  $$('[data-action]',$('#detail-content')).forEach(b=>b.addEventListener('click',()=>{const action=b.dataset.action;if(action.startsWith('ask-'))askTopic(action.slice(4));else showView(action);}));
  if(open)$('#detail-panel').classList.remove('closed');
}

function showView(view){
  state.view=view;$('#workspace').classList.toggle('focused-view',view!=='network');$$('.view-tab').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
  $('#content-view').hidden=view==='network';
  if(view==='network')return;
  $('#answer-panel').hidden=true;
  if(!state.data)return;
  const d=state.data,f=d.forecast;
  const header=(label,title,subtitle)=>`<div class="view-heading"><div><span class="view-label">${label}</span><h2>${title}</h2><p>${subtitle}</p></div><button class="icon-button" id="close-view" aria-label="Back to network">×</button></div>`;
  if(view==='forecast'){
    $('#content-view').innerHTML=header('KARACHI · FORECAST EXPLORER','The next 24 hours.',`${date(f.issue_time_utc)} → ${date(f.target_time_utc)} PKT`) + `<div class="view-heading"><div class="forecast-summary"><strong>${fmt(temp(f.model_prediction_c))}<small>°${state.unit}</small></strong><p>24-hour ML prediction<br>${escapeHTML(f.model_risk_category)}</p></div><div class="unit-toggle" aria-label="Temperature unit"><button data-unit="C" class="${state.unit==='C'?'active':''}">°C</button><button data-unit="F" class="${state.unit==='F'?'active':''}">°F</button></div></div><div class="chart-container" id="forecast-chart"></div><div class="chart-tooltip" id="chart-tooltip">Hover over the chart to explore an hour.</div><div class="chart-legend"><span><i class="chart-key"></i>Formula heat index · weather inputs</span><span><i class="chart-key ml"></i>ML prediction · +24h</span></div><div class="view-footnote">The curve is the NWS heat-index formula applied to stored Open-Meteo forecast inputs. The gold point is the separate 24-hour ML prediction. These are different methods, not a continuous ML forecast.<br><br>Historical interval: ${fmt(temp(f.interval_lower_c))}–${fmt(temp(f.interval_upper_c))}°${state.unit}. ${d.mode==='snapshot'?'Saved project snapshot. ':''}${d.fresh?'':'Stored evidence is stale. '}Collected ${date(d.freshness.collected_at_utc)} PKT. Source: gold.latest_live_weather and gold.latest_live_ml.</div>`;
    drawChart();$$('[data-unit]').forEach(b=>b.addEventListener('click',()=>{state.unit=b.dataset.unit;showView('forecast');}));
  }else{
    $('#content-view').innerHTML=header('TRACEABLE BY DESIGN','Behind the intelligence.','The datasets, evaluation and official guidance behind HeatSafe.')+`<div class="evidence-stats"><div><strong>${fmt(d.metrics.test_model_mae_c,2)}°C</strong><span>HEAT INDEX TEST MAE</span></div><div><strong>${fmt(d.metrics.test_model_r2,3)}</strong><span>HELD-OUT TEST R²</span></div><div><strong>${Number(d.metrics.test_rows).toLocaleString()}</strong><span>HELD-OUT TEST HOURS</span></div></div><div class="evidence-card"><h3>01 / Weather → warehouse</h3><p>ERA5 historical weather and Open-Meteo forecast inputs, validated and stored in DuckDB. The latest stored snapshot has ${d.freshness.total_hours} weather hours. Data attribution: Open-Meteo / ECMWF, CC BY 4.0.</p><p style="margin-top:9px">Snapshot: ${escapeHTML(f.snapshot_id)}<br>Quality: ${escapeHTML(f.dq_status)} · Matches latest ingestion: ${f.matches_latest_ingestion?'yes':'no'}</p></div><div class="evidence-card"><h3>02 / Model → evaluation</h3><p>HistGradientBoosting, compared with same-hour previous-day persistence. Model test MAE: ${fmt(d.metrics.test_model_mae_c,3)}°C. Baseline: ${fmt(d.metrics.test_baseline_mae_c,3)}°C. Risk-category accuracy: ${fmt(d.risk_metrics.accuracy*100,1)}%. Categories are derived from heat index, not measured health outcomes.</p></div>`+d.sources.map((s,i)=>`<div class="evidence-card"><h3>0${i+3} / ${escapeHTML(s.publisher)}</h3><p>${escapeHTML(s.title)}</p><a href="${escapeHTML(s.url)}" target="_blank" rel="noopener noreferrer">Read the official source ↗</a></div>`).join('')+`<div class="view-footnote">The assistant uses the project’s integrity-checked retrieval index. A generative AI layer is planned. Weather-derived heat index is not an on-site WBGT measurement. Data collected ${date(d.freshness.collected_at_utc)} PKT.</div>`;
  }
  $('#close-view').addEventListener('click',()=>showView('network'));
}
function drawChart(){
  const d=state.data,rows=d.hourly;if(!rows?.length){$('#forecast-chart').textContent='No hourly evidence is available.';return;}
  const W=580,H=215,L=36,R=17,T=17,B=35;const values=rows.map(r=>temp(r.heat_index_c));const ml=temp(d.forecast.model_prediction_c);
  const min=Math.floor((Math.min(...values,ml)-2)/5)*5,max=Math.ceil((Math.max(...values,ml)+2)/5)*5;
  const start=new Date(d.forecast.issue_time_utc).getTime();const end=new Date(d.forecast.target_time_utc).getTime();
  const x=t=>L+(new Date(t).getTime()-start)/(end-start)*(W-L-R);const y=v=>H-B-(v-min)/(max-min)*(H-T-B);
  const svg=svgEl('svg',{viewBox:`0 0 ${W} ${H}`,role:'img','aria-label':'24 hours of formula heat-index values with a separate 24-hour ML prediction'},$('#forecast-chart'));
  for(let value=min;value<=max;value+=5){svgEl('line',{x1:L,x2:W-R,y1:y(value),y2:y(value),class:'chart-grid'},svg);svgEl('text',{x:L-8,y:y(value)+3,'text-anchor':'end',class:'chart-label'},svg).textContent=value+'°';}
  [0,6,12,18,24].forEach(h=>{const t=new Date(start+h*3600000);svgEl('text',{x:x(t),y:H-10,'text-anchor':h===24?'end':h===0?'start':'middle',class:'chart-label'},svg).textContent=hour(t);});
  const line=rows.map((r,i)=>`${i?'L':'M'}${x(r.time_utc)},${y(values[i])}`).join(' ');
  svgEl('path',{d:line+` L${x(rows.at(-1).time_utc)},${H-B} L${x(rows[0].time_utc)},${H-B} Z`,fill:'#9bd0aa',opacity:'.06'},svg);
  svgEl('path',{d:line,fill:'none',stroke:'#a3d1b4','stroke-width':'2'},svg);
  const guide=svgEl('line',{x1:L,x2:L,y1:T,y2:H-B,stroke:'#c2e0c2','stroke-dasharray':'3 4',opacity:'0'},svg);
  rows.forEach((r,i)=>{const dot=svgEl('circle',{cx:x(r.time_utc),cy:y(values[i]),r:8,fill:'transparent',tabindex:'0',role:'img','aria-label':`${date(r.time_utc)} PKT: formula heat index ${fmt(values[i])} degrees ${state.unit}`},svg);const show=()=>{$('#chart-tooltip').textContent=`${date(r.time_utc)} PKT · Formula heat index ${fmt(values[i])}°${state.unit} · Humidity ${fmt(r.humidity_pct,0)}%`;guide.setAttribute('x1',x(r.time_utc));guide.setAttribute('x2',x(r.time_utc));guide.setAttribute('opacity','.5');};dot.addEventListener('mouseenter',show);dot.addEventListener('focus',show);dot.addEventListener('click',show);});
  svgEl('circle',{cx:x(d.forecast.target_time_utc),cy:y(ml),r:9,fill:'#d9c28d',opacity:'.14'},svg);const mlDot=svgEl('circle',{cx:x(d.forecast.target_time_utc),cy:y(ml),r:4,fill:'#e5cd98',stroke:'#fff2c3','stroke-width':'.8',tabindex:'0',role:'img','aria-label':`24-hour ML prediction ${fmt(ml)} degrees ${state.unit}`},svg);const mlHover=()=>$('#chart-tooltip').textContent=`${date(d.forecast.target_time_utc)} PKT · 24-hour ML prediction ${fmt(ml)}°${state.unit}`;mlDot.addEventListener('mouseenter',mlHover);mlDot.addEventListener('focus',mlHover);
}

const topicQuestions={safety:'How can outdoor workers stay safe in the heat?',hydration:'How should workers hydrate in the heat?',rest:'How do rest breaks and shade help heat safety?',index:'What is heat index and how does humidity affect it?',acclimatization:'How should workers acclimatize to heat?'};
function askTopic(topic){$('#question').value=topicQuestions[topic]||topicQuestions.safety;ask($('#question').value);}
async function ask(question){
  question=question.trim();if(!question)return;
  const panel=$('#answer-panel'),body=$('#answer-content');panel.hidden=false;
  body.innerHTML=`<div class="answer-question">${escapeHTML(question)}</div><div class="answer-text loading-pulse">Retrieving verified evidence…</div>`;
  const submit=$('.send-button');submit.disabled=true;let answer,note;
  try{
    if(state.api){const response=await fetch('/api/insight',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question}),signal:AbortSignal.timeout(20000)});const result=await response.json();if(!response.ok)throw Error(result.error||'Assistant unavailable');answer=result.answer;note='Answered by the existing HeatSafe retrieval agent · official citations included where evidence is available.';}
    else{
      if(!state.guidance){const response=await fetch('guidance.json');if(!response.ok)throw Error('Saved guidance unavailable');state.guidance=await response.json();}
      const q=question.toLowerCase();
      if(/\b(aqi|pollution|hospital\w*|death\w*|injur\w*|admission\w*)\b/.test(q))answer="I don't know based on the available verified evidence. This information is not supported by the project's current verified gold data.";
      else if(/\b(current|latest|today|tomorrow|forecast|prediction|predicted)\b/.test(q))answer='This hosted view contains saved project evidence, not a live forecast feed. Explore the forecast tab for the dated snapshot. Run the local website to query the DuckDB-connected agent and its freshness checks.';
      else if(/\b(heat index|humidity|feels like)\b/.test(q))answer=state.guidance.index;
      else if(/\b(acclimati[sz]\w*)\b/.test(q))answer=state.guidance.acclimatization;
      else if(/\b(hydrat\w*|water|drink\w*)\b/.test(q))answer=state.guidance.hydration;
      else if(/\b(rest|shade|breaks?)\b/.test(q))answer=state.guidance.rest;
      else if(/\b(safe|safety|protect\w*|prevent\w*|precaution\w*|hot|heat)\b/.test(q))answer=state.guidance.safety;
      else answer="I don't know based on the available verified evidence. In this saved project view, I can show the existing agent’s exported guidance on heat safety, hydration, rest, acclimatization and heat index.";
      note='Saved response from the project’s retrieval agent, matched by topic. Local mode supports the full existing retrieval assistant. This is not a generative chat model.';
    }
    const safeAnswer=escapeHTML(answer).replace(/https:\/\/[^\s<]+/g,url=>`<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`);
    body.innerHTML=`<div class="answer-question">${escapeHTML(question)}</div><div class="answer-text">${safeAnswer}</div><div class="answer-note">${escapeHTML(note)}</div><div class="suggestions"><button data-topic="hydration">Water & hydration</button><button data-topic="rest">Rest & shade</button><button data-topic="index">Explain heat index</button></div>`;
    $$('[data-topic]',body).forEach(b=>b.addEventListener('click',()=>askTopic(b.dataset.topic)));
  }catch(error){body.innerHTML=`<div class="answer-question">Unable to retrieve an answer</div><div class="answer-text">${escapeHTML(error.message)}. Please try again or explore the official sources in the Evidence tab.</div>`;}
  finally{submit.disabled=false;}
}

function applyTransform(){state.zoom=Math.max(.55,Math.min(2,state.zoom));$('#map-transform').setAttribute('transform',`translate(${705+state.pan.x} ${398+state.pan.y}) scale(${state.zoom}) translate(-705 -398)`);$('#zoom-reset').textContent=Math.round(state.zoom*100)+'%';}
function resetMap(){state.zoom=1;state.pan={x:0,y:0};applyTransform();}
function showInfo(kind){
  const dialog=$('#info-dialog');
  const content={
    about:`<span class="dialog-label">HACKINSIGHT 2026 · CLIMATE & HEAT-HEALTH</span><h2>Heat intelligence.<br>Human impact.</h2><p>HeatSafe Pakistan combines weather evidence, a trained heat-index forecasting model, and official workplace guidance to help outdoor workers and decision-makers understand heat risk.</p><p>The initial focus is Karachi. This workspace connects the existing project’s DuckDB warehouse, HistGradientBoosting model evaluation and retrieval-based Insight Agent.</p><p>Heat index is not an on-site WBGT measurement. Informational estimates and general guidance support workplace assessment; they do not replace official alerts or medical advice.</p>`,
    guide:`<span class="dialog-label">YOUR MAP GUIDE</span><h2>Follow the connections.</h2><ul><li>Select any glowing node to inspect its evidence.</li><li>Use the legend to focus on weather, forecasts, safety or sources. Select the same category again to show everything.</li><li>Drag the map to pan. Scroll or use + / − to zoom. The fit button resets your view.</li><li>Switch to Forecast to explore hourly formula values and the separate ML prediction.</li><li>Ask the Insight Agent about heat safety. Answers use official project guidance.</li></ul><p>Keyboard: Tab between nodes and controls, Enter to select, Escape to close panels. Use the pause button to stop ambient animation.</p>`,
    workspace:`<span class="dialog-label">PROJECT WORKSPACE</span><h2>HeatSafe Pakistan</h2><p>Location: Karachi, Pakistan<br>Timezone: Asia/Karachi (PKT)<br>Forecast horizon: 24 hours<br>Model: HistGradientBoosting<br>Assistant: evidence retrieval, not generative AI</p><p>${state.api?(state.data?.storage?'The Python backend reads the read-only database bundled with this deployment. Updates require rebuilding and redeploying the package. Your computer can be switched off.':'Local mode reads the project’s DuckDB warehouse on refresh.'):'This static view uses a saved export of project evidence; the Python API is unavailable.'}</p><p>Evidence collected: ${state.data?date(state.data.freshness.collected_at_utc)+' PKT':'unavailable'}.</p>`
  };
  $('#dialog-content').innerHTML=content[kind];dialog.showModal();
}

$$('.view-tab').forEach(b=>b.addEventListener('click',()=>showView(b.dataset.view)));
$$('.legend-item').forEach(b=>b.addEventListener('click',()=>{showView('network');state.filter=state.filter===b.dataset.filter?null:b.dataset.filter;updateFilter();if(state.filter)showDetail(state.filter);}));
$('#close-detail').addEventListener('click',()=>$('#detail-panel').classList.add('closed'));
$('#close-answer').addEventListener('click',()=>$('#answer-panel').hidden=true);
$('#analysis-button').addEventListener('click',()=>showView('forecast'));
$('#refresh-button').addEventListener('click',()=>loadData());
$('#ask-form').addEventListener('submit',e=>{e.preventDefault();ask($('#question').value);});
$('#zoom-in').addEventListener('click',()=>{state.zoom+=.15;applyTransform();});
$('#zoom-out').addEventListener('click',()=>{state.zoom-=.15;applyTransform();});
$('#zoom-reset').addEventListener('click',resetMap);$('#fit-map').addEventListener('click',resetMap);
$('#network-layout').addEventListener('click',()=>{state.orbit=false;$('#network-layout').classList.add('active');$('#orbit-layout').classList.remove('active');buildNetwork();resetMap();});
$('#orbit-layout').addEventListener('click',()=>{state.orbit=true;$('#network-layout').classList.remove('active');$('#orbit-layout').classList.add('active');buildNetwork();resetMap();});
function setMotion(){document.body.classList.toggle('motion-paused',state.paused);$('#motion-toggle').textContent=state.paused?'▷':'Ⅱ';$('#motion-toggle').setAttribute('aria-pressed',state.paused);$('#motion-toggle').setAttribute('aria-label',state.paused?'Resume ambient animation':'Pause ambient animation');const svg=$('#network');if(state.paused)svg.pauseAnimations();else svg.unpauseAnimations();}
$('#motion-toggle').addEventListener('click',()=>{state.paused=!state.paused;setMotion();});
$('#about-button').addEventListener('click',()=>showInfo('about'));$('#settings-button').addEventListener('click',()=>showInfo('workspace'));$('#help-button').addEventListener('click',()=>showInfo('guide'));$('.dialog-close').addEventListener('click',()=>$('#info-dialog').close());$('#info-dialog').addEventListener('click',e=>{if(e.target===$('#info-dialog')){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close();}});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){$('#answer-panel').hidden=true;$('#detail-panel').classList.add('closed');if(state.view!=='network')showView('network');}});
const network=$('#network');let dragging=null;
network.addEventListener('wheel',e=>{e.preventDefault();state.zoom+=e.deltaY>0?-.07:.07;applyTransform();},{passive:false});
network.addEventListener('pointerdown',e=>{if(e.target.closest('.graph-node'))return;dragging={x:e.clientX,y:e.clientY,pan:{...state.pan}};network.setPointerCapture(e.pointerId);network.classList.add('dragging');});
network.addEventListener('pointermove',e=>{if(!dragging)return;const ratio=1440/network.getBoundingClientRect().width;state.pan={x:dragging.pan.x+(e.clientX-dragging.x)*ratio,y:dragging.pan.y+(e.clientY-dragging.y)*ratio};applyTransform();});
const stopDrag=()=>{dragging=null;network.classList.remove('dragging');};network.addEventListener('pointerup',stopDrag);network.addEventListener('pointercancel',stopDrag);
setMotion();loadData(true);
