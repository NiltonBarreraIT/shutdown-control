const $ = id => document.getElementById(id);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const shortDate = value => { if (!value) return 'Sin fecha'; const m=String(value).match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/); return m?`${m[3]}-${m[2]}-${m[1]}${m[4]?` ${m[4]}:${m[5]}`:''}`:String(value).slice(0,16); };
const planDate = value => { const m=String(value||'').match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?/); return m?new Date(+m[1],+m[2]-1,+m[3],+(m[4]||0),+(m[5]||0),+(m[6]||0)):null; };
const toast=(message,error=false)=>{const e=$('toast');e.textContent=message;e.className=error?'error':'';e.style.display='block';setTimeout(()=>e.style.display='none',4300)};
let plan={tasks:[],counts:{}};

const isCompleted=task=>Number(task.actual_progress)>=100;
const isActive=(task,now)=>{const start=planDate(task.start_at),finish=planDate(task.finish_at);return !isCompleted(task)&&start&&finish&&start<=now&&now<=finish};
const isOverdue=(task,now)=>{const finish=planDate(task.finish_at);return !isCompleted(task)&&finish&&finish<now};
const isUpcoming=(task,now)=>{const start=planDate(task.start_at);return !isCompleted(task)&&start&&start>now&&start<=new Date(now.getTime()+12*60*60*1000)};
function operationalState(task,now){
  if(isCompleted(task))return{key:'completed',label:'Completada',className:'success'};
  if(isOverdue(task,now))return{key:'overdue',label:'Atrasada',className:'danger'};
  if(isActive(task,now)&&task.report_id===null)return{key:'no_report',label:'Sin reporte iniciado',className:'warn'};
  if(isActive(task,now))return{key:'active',label:'En ejecución',className:'info'};
  const start=planDate(task.start_at);
  if(start&&start>now)return{key:'future',label:isUpcoming(task,now)?'Próxima ≤ 12 h':'Programada',className:'neutral'};
  return{key:'unscheduled',label:'Sin programación',className:'neutral'};
}
async function api(path,options={}){const response=await fetch(path,options);if(response.status===401){location.href='/login';throw Error('Sesión expirada')}const data=await response.json();if(!response.ok)throw Error(data.error||'No fue posible completar la operación');return data}
function fillSelect(id,values,fallback){$(id).innerHTML=`<option value="">${fallback}</option>${values.map(value=>`<option value="${escapeHTML(value)}">${escapeHTML(value)}</option>`).join('')}`}
function render(){
  const now=new Date(),tasks=plan.tasks||[];
  $('plan-overdue').textContent=tasks.filter(task=>isOverdue(task,now)).length;
  $('plan-active').textContent=tasks.filter(task=>isActive(task,now)).length;
  $('plan-upcoming').textContent=tasks.filter(task=>isUpcoming(task,now)).length;
  $('plan-completed').textContent=tasks.filter(isCompleted).length;
  $('plan-cutoff').textContent=`Estado al ${now.toLocaleString('es-CL',{dateStyle:'short',timeStyle:'short'})}`;
  $('plan-version').textContent=plan.version?`${plan.version.filename} · ${plan.version.task_count} tareas · ${shortDate(plan.version.imported_at)} · ${plan.version.imported_by_name}`:'No hay una Carta Gantt importada.';
  fillSelect('plan-area',[...new Set(tasks.map(task=>task.area).filter(Boolean))].sort(),'Todas las áreas');
  fillSelect('plan-shift',[...new Set(tasks.map(task=>task.shift).filter(Boolean))].sort(),'Todos los turnos');
  filter();
}
function filter(){
  const now=new Date(),query=$('plan-search').value.trim().toLowerCase(),area=$('plan-area').value,shift=$('plan-shift').value,state=$('plan-state').value,date=$('plan-date').value;
  const tasks=(plan.tasks||[]).filter(task=>{
    const search=[task.ot,`OT ${task.ot}`,task.activity,`OP ${task.activity}`,task.name,task.area,task.wbs,task.shift,task.journey].join(' ').toLowerCase();
    const taskState=operationalState(task,now).key;
    const matchesState=!state||taskState===state||(state==='active'&&isActive(task,now))||(state==='upcoming'&&isUpcoming(task,now))||(state==='missing_ids'&&(!task.ot||!task.activity));
    const matchesDate=!date||String(task.start_at||'').slice(0,10)===date||String(task.finish_at||'').slice(0,10)===date;
    return(!query||search.includes(query))&&(!area||task.area===area)&&(!shift||task.shift===shift)&&matchesState&&matchesDate;
  });
  $('plan-results').textContent=`${tasks.length} resultado${tasks.length===1?'':'s'}`;
  $('plan-table').innerHTML=tasks.map(task=>{const status=operationalState(task,now),missing=!task.ot||!task.activity;return `<tr><td><strong>${escapeHTML(task.wbs||'—')} · ${escapeHTML(task.name)}</strong>${task.critical?'<span class="secondary">Actividad crítica</span>':''}</td><td>${task.ot?`OT ${escapeHTML(task.ot)}`:'Sin OT'}<span class="secondary">${task.activity?`OP ${escapeHTML(task.activity)}`:'Sin OP'}</span></td><td>${escapeHTML(task.area||'Sin área')}<span class="secondary">${escapeHTML(task.shift||task.journey||'Sin turno')}</span></td><td>${escapeHTML(shortDate(task.start_at))}<span class="secondary">${escapeHTML(shortDate(task.finish_at))}</span></td><td>${task.actual_progress===null?'—':`${task.actual_progress}%`}</td><td><span class="status ${status.className}">${status.label}</span>${missing?'<span class="secondary data-warning">Falta OT/OP</span>':''}</td></tr>`}).join('')||'<tr><td colspan="6" class="empty">No hay tareas que coincidan.</td></tr>';
}
async function load(){try{const status=await api('/api/status');$('account-name').textContent=`${status.current_user.username} · ${status.current_user.role_label}`;$('users-nav').classList.toggle('hidden',status.current_user.role!=='admin');$('plan-import').classList.toggle('hidden',!['admin','planner'].includes(status.current_user.role));plan=await api('/api/plan');render()}catch(error){toast(error.message,true)}}
['plan-search','plan-area','plan-shift','plan-state','plan-date'].forEach(id=>$(id).addEventListener(id==='plan-search'?'input':'change',filter));
$('plan-upload').addEventListener('click',async()=>{const file=$('plan-file').files[0];if(!file){toast('Seleccione un archivo Excel o XML',true);return}const type=file.name.toLowerCase().endsWith('.xml')?'application/xml':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';try{const result=await api('/api/plan/import',{method:'POST',headers:{'Content-Type':type,'X-Filename':file.name},body:file});toast(`${result.imported} tareas importadas`);plan=await api('/api/plan');render()}catch(error){toast(error.message,true)}});
load();
