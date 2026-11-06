const $ = id => document.getElementById(id);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const clamp = value => Math.max(0, Math.min(100, Number(value) || 0));
const shortDate = value => { if (!value) return 'Sin fecha'; const m=String(value).match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/); return m?`${m[3]}-${m[2]}-${m[1]}${m[4]?` ${m[4]}:${m[5]}`:''}`:String(value).slice(0,16); };
const planDate = value => { const m=String(value||'').match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?/); return m?new Date(+m[1],+m[2]-1,+m[3],+(m[4]||0),+(m[5]||0),+(m[6]||0)):null; };
const toast=(message,error=false)=>{const e=$('toast');e.textContent=message;e.className=error?'error':'';e.style.display='block';setTimeout(()=>e.style.display='none',4300)};
let plan={tasks:[],counts:{}};

const actualProgress=task=>task.actual_progress===null?0:clamp(task.actual_progress);
const plannedProgress=(task,now)=>{if(task.planned_progress!==undefined&&task.planned_progress!==null)return clamp(task.planned_progress);const start=planDate(task.start_at),finish=planDate(task.finish_at);if(!start||!finish||finish<=start)return null;if(now<=start)return 0;if(now>=finish)return 100;return clamp((now-start)/(finish-start)*100)};
const taskWeight=task=>{const supplied=Number(task.duration_hours);if(supplied>0)return supplied;const start=planDate(task.start_at),finish=planDate(task.finish_at);return start&&finish&&finish>start?(finish-start)/3600000:1};
const isCompleted=task=>actualProgress(task)>=100;
const isActive=(task,now)=>{const start=planDate(task.start_at),finish=planDate(task.finish_at);return !isCompleted(task)&&start&&finish&&start<=now&&now<=finish};
const isOverdue=(task,now)=>{const finish=planDate(task.finish_at);return !isCompleted(task)&&finish&&finish<now};
const isUpcoming=(task,now)=>{const start=planDate(task.start_at);return !isCompleted(task)&&start&&start>now&&start<=new Date(now.getTime()+12*60*60*1000)};
function comparisonState(task,now){const planned=plannedProgress(task,now);if(planned===null||(planned===0&&task.report_id===null))return{key:'not_started',label:'Sin comparación',className:'neutral',variance:null};const variance=actualProgress(task)-planned;if(variance<=-5)return{key:'behind',label:'Atrasada vs plan',className:'danger',variance};if(variance>=5)return{key:'ahead',label:'Adelantada vs plan',className:'success',variance};return{key:'on_track',label:'En línea con plan',className:'info',variance}}
function operationalState(task,now){
  if(isCompleted(task))return{key:'completed',label:'Completada',className:'success'};
  if(isOverdue(task,now))return{key:'overdue',label:'Vencida',className:'danger'};
  if(isActive(task,now)&&task.report_id===null)return{key:'no_report',label:'Sin reporte iniciado',className:'warn'};
  if(isActive(task,now))return{key:'active',label:'En ejecución',className:'info'};
  const start=planDate(task.start_at);
  if(start&&start>now)return{key:'future',label:isUpcoming(task,now)?'Próxima ≤ 12 h':'Programada',className:'neutral'};
  return{key:'unscheduled',label:'Sin programación',className:'neutral'};
}
function programMetrics(tasks,now){
  const scheduled=tasks.filter(task=>plannedProgress(task,now)!==null);
  const totalWeight=scheduled.reduce((sum,task)=>sum+taskWeight(task),0);
  const planned=totalWeight?scheduled.reduce((sum,task)=>sum+plannedProgress(task,now)*taskWeight(task),0)/totalWeight:0;
  const actual=totalWeight?scheduled.reduce((sum,task)=>sum+actualProgress(task)*taskWeight(task),0)/totalWeight:0;
  const variance=actual-planned;
  const starts=scheduled.map(task=>planDate(task.start_at)).filter(Boolean);
  const finishes=scheduled.map(task=>planDate(task.finish_at)).filter(Boolean);
  const start=starts.length?new Date(Math.min(...starts)):null;
  const finish=finishes.length?new Date(Math.max(...finishes)):null;
  let forecast=null;
  if(start&&finish&&actual>0&&now>start)forecast=new Date(start.getTime()+(now-start)/(actual/100));
  return{planned,actual,variance,totalWeight,start,finish,forecast};
}
const reportMoment=report=>planDate(report.submitted_at)||planDate(report.report_date);
function historyIndex(history){const index=new Map();(history||[]).forEach(report=>{const key=`${report.ot}\u0000${report.activity}`;if(!index.has(key))index.set(key,[]);index.get(key).push(report)});return index}
function actualAt(task,cutoff,index){const reports=index.get(`${task.ot}\u0000${task.activity}`)||[];let progress=0;for(const report of reports){const moment=reportMoment(report);if(moment&&moment<=cutoff)progress=clamp(report.progress)}return progress}
function curveSeries(tasks,history,now){
  const scheduled=tasks.filter(task=>plannedProgress(task,now)!==null),metrics=programMetrics(scheduled,now);
  if(!metrics.start||!metrics.finish)return[];
  const index=historyIndex(history),end=new Date(Math.min(now,metrics.finish)),days=Math.max(1,Math.ceil((end-metrics.start)/86400000)),step=Math.max(1,Math.ceil(days/60)),points=[];
  for(let day=0;day<=days;day+=step){const cutoff=new Date(metrics.start);cutoff.setDate(cutoff.getDate()+day);cutoff.setHours(23,59,59,999);if(cutoff>end)cutoff.setTime(end.getTime());const total=scheduled.reduce((sum,task)=>sum+taskWeight(task),0);const planned=total?scheduled.reduce((sum,task)=>sum+(plannedProgress(task,cutoff)||0)*taskWeight(task),0)/total:0;const actual=total?scheduled.reduce((sum,task)=>sum+actualAt(task,cutoff,index)*taskWeight(task),0)/total:0;points.push({at:new Date(cutoff),planned,actual});if(cutoff>=end)break}
  if(points.length&&points[points.length-1].at<end){const total=scheduled.reduce((sum,task)=>sum+taskWeight(task),0);points.push({at:end,planned:total?scheduled.reduce((sum,task)=>sum+(plannedProgress(task,end)||0)*taskWeight(task),0)/total:0,actual:total?scheduled.reduce((sum,task)=>sum+actualAt(task,end,index)*taskWeight(task),0)/total:0})}
  return points;
}
async function api(path,options={}){const response=await fetch(path,options);if(response.status===401){location.href='/login';throw Error('Sesión expirada')}const data=await response.json();if(!response.ok)throw Error(data.error||'No fue posible completar la operación');return data}
function fillSelect(id,values,fallback){$(id).innerHTML=`<option value="">${fallback}</option>${values.map(value=>`<option value="${escapeHTML(value)}">${escapeHTML(value)}</option>`).join('')}`}
function renderPhaseTwo(tasks,now){
  const metrics=plan.analytics||programMetrics(tasks,now);
  $('plan-planned').textContent=`${metrics.planned.toFixed(1)}%`;
  $('plan-actual').textContent=`${metrics.actual.toFixed(1)}%`;
  $('plan-bhp-estimate').textContent=metrics.estimated_bhp===null||metrics.estimated_bhp===undefined?'Sin HH':`${metrics.estimated_bhp.toFixed(1)}%`;
  $('plan-variance').textContent=`${metrics.variance>=0?'+':''}${metrics.variance.toFixed(1)} pp`;
  $('plan-variance-detail').textContent=metrics.counts?.over_100_ops?`${metrics.counts.over_100_ops} OP con acumulado superior a 100%`:'Acumulado validado por OT/OP';
  $('plan-method').textContent=`Ponderación utilizada: ${metrics.method==='planned_hh'?'HH planificadas':metrics.method==='duration'?'duración programada':'uniforme'}. Los incrementos originales se conservan para auditoría.`;
  const health=$('plan-health');
  health.className=`status ${metrics.planned===0?'neutral':metrics.variance>=-5?'success':metrics.variance>=-15?'warn':'danger'}`;
  health.textContent=metrics.planned===0?'Programa aún no iniciado':metrics.variance>=-5?'En línea':metrics.variance>=-15?'Atención':'Desviación crítica';
  const forecast=metrics.projection?.base;
  if(forecast){
    $('plan-forecast').textContent=shortDate(forecast);
    $('plan-forecast-detail').textContent=`Base · ritmo ${Number(metrics.projection.daily_rate||0).toFixed(2)} pp/día`;
  }else{
    $('plan-forecast').textContent='Sin proyección';
    $('plan-forecast-detail').textContent='Se requiere avance real mayor a 0%';
  }
}
function renderPhaseThree(tasks,now){
  const metrics=plan.analytics||programMetrics(tasks,now),spi=metrics.spi??(metrics.planned>0?metrics.actual/metrics.planned:null);
  $('plan-spi').textContent=spi===null?'—':spi.toFixed(2);
  $('plan-maximum').textContent=metrics.projection?`${Number(metrics.projection.maximum_attainable||0).toFixed(1)}%`:'—';
  $('plan-hh-planned').textContent=Number(metrics.planned_hh||0).toLocaleString('es-CL',{maximumFractionDigits:1});
  $('plan-hh-executed').textContent=Number(metrics.executed_hh||0).toLocaleString('es-CL',{maximumFractionDigits:1});
  $('plan-hh-pending').textContent=Number(metrics.pending_hh||0).toLocaleString('es-CL',{maximumFractionDigits:1});
  $('plan-ot-op').textContent=`${metrics.counts?.ots||0} / ${metrics.counts?.ops||0}`;
  $('plan-op-detail').textContent=`${metrics.counts?.completed_ops||0} terminadas · ${metrics.counts?.delayed_ops||0} atrasadas`;
  const areas=metrics.areas||[];
  const risk=areas[0];
  $('plan-risk-area').textContent=risk?risk.area:'—';
  $('plan-risk-detail').textContent=risk?`${risk.variance>=0?'+':''}${risk.variance.toFixed(1)} pp`:'Por área';
  $('plan-lookahead').textContent=tasks.filter(task=>isUpcoming(task,now)).length;
  $('plan-area-table').innerHTML=areas.map(area=>{const cls=area.planned===0?'neutral':area.variance>=-5?'success':area.variance>=-15?'warn':'danger';const label=cls==='success'?'En línea':cls==='warn'?'Atención':cls==='danger'?'Crítica':'Sin inicio';return`<tr><td><strong>${escapeHTML(area.area)}</strong></td><td>${area.planned.toFixed(1)}%</td><td>${area.actual.toFixed(1)}%</td><td>${area.variance>=0?'+':''}${area.variance.toFixed(1)} pp</td><td><span class="status ${cls}">${label}</span></td></tr>`}).join('')||'<tr><td colspan="5" class="empty">No hay áreas para comparar.</td></tr>';
  const series=metrics.curve||curveSeries(tasks,plan.history||[],now),svg=$('plan-curve');
  if(!series.length){svg.innerHTML='<text x="380" y="130" text-anchor="middle" class="curve-empty">No hay fechas para construir la curva</text>';$('curve-cutoff').textContent='Sin historial';return}
  const left=48,right=742,top=18,bottom=222,width=right-left,height=bottom-top,x=i=>left+(series.length===1?0:i/(series.length-1))*width,y=value=>bottom-clamp(value)/100*height;
  const path=(field,items=series)=>items.filter(point=>point[field]!==null&&point[field]!==undefined).map(point=>`${x(series.indexOf(point)).toFixed(1)},${y(point[field]).toFixed(1)}`).join(' ');
  const planned=path('planned'),actual=path('actual'),bhp=path('estimated_bhp');
  const grid=[0,25,50,75,100].map(value=>`<line x1="${left}" y1="${y(value)}" x2="${right}" y2="${y(value)}" class="curve-grid"/><text x="40" y="${y(value)+4}" text-anchor="end" class="curve-axis">${value}%</text>`).join('');
  const chartDate=date=>date.toLocaleDateString('es-CL',{day:'2-digit',month:'2-digit'});
  const pointDate=point=>new Date(point.cutoff||point.at),projection=metrics.projection?.base;
  let projectionLine='';
  if(projection&&actual){let lastIndex=series.length-1;while(lastIndex>0&&(series[lastIndex].actual===null||series[lastIndex].actual===undefined))lastIndex--;const last=series[lastIndex];projectionLine=`<line x1="${x(lastIndex)}" y1="${y(last.actual)}" x2="${right}" y2="${y(100)}" class="curve-line curve-projection"/>`}
  svg.innerHTML=`${grid}<polyline points="${planned}" class="curve-line curve-plan"/><polyline points="${actual}" class="curve-line curve-real"/>${bhp?`<polyline points="${bhp}" class="curve-line curve-bhp"/>`:''}${projectionLine}<text x="${left}" y="246" class="curve-axis">${chartDate(pointDate(series[0]))}</text><text x="${right}" y="246" text-anchor="end" class="curve-axis">${chartDate(pointDate(series[series.length-1]))}</text>`;
  $('curve-cutoff').textContent=`${plan.history?.length||0} reportes históricos`;
}
function render(){
  const now=new Date(),tasks=plan.tasks||[];
  $('plan-overdue').textContent=tasks.filter(task=>isOverdue(task,now)).length;
  $('plan-active').textContent=tasks.filter(task=>isActive(task,now)).length;
  $('plan-upcoming').textContent=tasks.filter(task=>isUpcoming(task,now)).length;
  $('plan-completed').textContent=tasks.filter(isCompleted).length;
  $('plan-cutoff').textContent=`Estado al ${now.toLocaleString('es-CL',{dateStyle:'short',timeStyle:'short'})}`;
  $('plan-version').textContent=plan.version?`${plan.version.filename} · ${plan.version.task_count} tareas · ${shortDate(plan.version.imported_at)} · ${plan.version.imported_by_name}`:'No hay una Carta Gantt importada.';
  fillSelect('plan-area',[...new Set(tasks.map(task=>task.area).filter(Boolean))].sort(),'Todas las áreas');
  fillSelect('plan-ot',[...new Set(tasks.map(task=>task.ot).filter(Boolean))].sort(),'Todas las OT');
  fillSelect('plan-op',[...new Set(tasks.map(task=>task.activity).filter(Boolean))].sort(),'Todas las OP');
  fillSelect('plan-shift',[...new Set(tasks.map(task=>task.shift).filter(Boolean))].sort(),'Todos los turnos');
  renderPhaseTwo(tasks,now);
  renderPhaseThree(tasks,now);
  filter();
}
function filter(){
  const now=new Date(),query=$('plan-search').value.trim().toLowerCase(),area=$('plan-area').value,ot=$('plan-ot').value,op=$('plan-op').value,shift=$('plan-shift').value,state=$('plan-state').value,date=$('plan-date').value;
  const tasks=(plan.tasks||[]).filter(task=>{
    const search=[task.ot,`OT ${task.ot}`,task.activity,`OP ${task.activity}`,task.name,task.area,task.wbs,task.shift,task.journey].join(' ').toLowerCase();
    const taskState=operationalState(task,now).key,comparison=comparisonState(task,now).key;
    const matchesState=!state||taskState===state||comparison===state||(state==='active'&&isActive(task,now))||(state==='upcoming'&&isUpcoming(task,now))||(state==='missing_ids'&&(!task.ot||!task.activity));
    const matchesDate=!date||String(task.start_at||'').slice(0,10)===date||String(task.finish_at||'').slice(0,10)===date;
    return(!query||search.includes(query))&&(!area||task.area===area)&&(!ot||task.ot===ot)&&(!op||task.activity===op)&&(!shift||task.shift===shift)&&matchesState&&matchesDate;
  });
  $('plan-results').textContent=`${tasks.length} resultado${tasks.length===1?'':'s'}`;
  $('plan-table').innerHTML=tasks.map(task=>{
    const status=operationalState(task,now),comparison=comparisonState(task,now),planned=plannedProgress(task,now),actual=actualProgress(task),missing=!task.ot||!task.activity;
    const variance=comparison.variance===null?'Sin comparación':`${comparison.variance>=0?'+':''}${comparison.variance.toFixed(1)} pp`;
    return `<tr><td><strong>${escapeHTML(task.wbs||'—')} · ${escapeHTML(task.name)}</strong>${task.critical?'<span class="secondary">Actividad crítica</span>':''}</td><td>${task.ot?`OT ${escapeHTML(task.ot)}`:'Sin OT'}<span class="secondary">${task.activity?`OP ${escapeHTML(task.activity)}`:'Sin OP'}</span>${task.report_count?`<button type="button" class="report-toggle" data-ot="${escapeHTML(task.ot)}" data-op="${escapeHTML(task.activity)}">${task.report_count} reporte${task.report_count===1?'':'s'}</button>`:''}</td><td>${escapeHTML(task.area||'Sin área')}<span class="secondary">${escapeHTML(task.shift||task.journey||'Sin turno')}</span></td><td>${escapeHTML(shortDate(task.start_at))}<span class="secondary">${escapeHTML(shortDate(task.finish_at))}</span></td><td><strong>${planned===null?'—':`${planned.toFixed(1)}%`} / ${task.report_id===null?'—':`${actual.toFixed(1)}%`}</strong><span class="secondary ${comparison.className==='danger'?'data-warning':''}">${variance}</span></td><td><span class="status ${status.className}">${status.label}</span><span class="secondary"><span class="status compact ${comparison.className}">${comparison.label}</span></span>${missing?'<span class="secondary data-warning">Falta OT/OP</span>':''}</td></tr>`;
  }).join('')||'<tr><td colspan="6" class="empty">No hay tareas que coincidan.</td></tr>';
}
async function load(){try{const status=await api('/api/status');$('account-name').textContent=`${status.current_user.username} · ${status.current_user.role_label}`;$('users-nav').classList.toggle('hidden',status.current_user.role!=='admin');$('plan-import').classList.toggle('hidden',!['admin','planner'].includes(status.current_user.role));plan=await api('/api/plan');render()}catch(error){toast(error.message,true)}}
async function refreshAnalytics(){const params=new URLSearchParams();[['area','plan-area'],['ot','plan-ot'],['op','plan-op'],['shift','plan-shift'],['cutoff','plan-date']].forEach(([name,id])=>{if($(id).value)params.set(name,$(id).value)});try{const result=await api(`/api/plan/analytics?${params}`);plan.analytics=result.analytics;const now=result.analytics?.cutoff?new Date(result.analytics.cutoff):new Date();renderPhaseTwo(plan.tasks||[],now);renderPhaseThree(plan.tasks||[],now);$('plan-cutoff').textContent=`Corte: ${now.toLocaleString('es-CL',{dateStyle:'short',timeStyle:'short'})}`}catch(error){toast(error.message,true)}}
function showOperationReports(ot,op){const reports=(plan.history||[]).filter(report=>String(report.ot)===ot&&String(report.activity)===op).sort((a,b)=>String(a.submitted_at).localeCompare(String(b.submitted_at)));let accumulated=0;$('op-reports-title').textContent=`OT ${ot} · OP ${op}`;$('op-reports-table').innerHTML=reports.map(report=>{const increment=clamp(report.progress);accumulated+=increment;const executed=(Number(report.workers)||0)*(Number(report.hours)||0);return`<tr><td>${escapeHTML(shortDate(report.submitted_at||report.report_date))}</td><td>${escapeHTML(report.id)}</td><td>+${increment.toFixed(1)}%</td><td>${accumulated.toFixed(1)}%${accumulated>100?' ⚠':''}</td><td>${executed.toFixed(1)}</td></tr>`}).join('');$('op-reports-summary').textContent=`${reports.length} incrementos · acumulado original ${accumulated.toFixed(1)}% · presentado ${Math.min(100,accumulated).toFixed(1)}%`;$('op-reports-dialog').showModal()}
if(typeof module!=='undefined')module.exports={planDate,plannedProgress,actualProgress,taskWeight,comparisonState,programMetrics,historyIndex,actualAt,curveSeries};
if(typeof document!=='undefined'){
  $('plan-search').addEventListener('input',filter);
  $('plan-state').addEventListener('change',filter);
  ['plan-area','plan-ot','plan-op','plan-shift','plan-date'].forEach(id=>$(id).addEventListener('change',()=>{filter();refreshAnalytics()}));
  $('plan-table').addEventListener('click',event=>{const button=event.target.closest('.report-toggle');if(button)showOperationReports(button.dataset.ot,button.dataset.op)});
  $('op-reports-close').addEventListener('click',()=>$('op-reports-dialog').close());
  $('plan-upload').addEventListener('click',async()=>{const file=$('plan-file').files[0];if(!file){toast('Seleccione un archivo Excel o XML',true);return}const type=file.name.toLowerCase().endsWith('.xml')?'application/xml':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';try{const result=await api('/api/plan/import',{method:'POST',headers:{'Content-Type':type,'X-Filename':file.name},body:file});toast(`${result.imported} tareas importadas`);plan=await api('/api/plan');render()}catch(error){toast(error.message,true)}});
  load();
}
