let dataset={reports:[],ots:[],areas:{},counts:{}};
let currentPage=1;
const PAGE_SIZE=10;
const expandedOts=new Set();
const $=id=>document.getElementById(id);
const escapeHTML=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=n=>n===null||n===undefined?'—':`${Math.round(n*10)/10}%`;
const shortDate=value=>{
  if(value===null||value===undefined||value==='')return 'Sin fecha';
  let text=String(value).trim();
  if(/^\d+(\.\d+)?$/.test(text)){
    const serial=Number(text);
    if(serial>=1&&serial<=2958465){
      const date=new Date(Date.UTC(1899,11,30)+serial*86400000);
      text=`${date.getUTCFullYear()}-${String(date.getUTCMonth()+1).padStart(2,'0')}-${String(date.getUTCDate()).padStart(2,'0')} ${String(date.getUTCHours()).padStart(2,'0')}:${String(date.getUTCMinutes()).padStart(2,'0')}`;
    }
  }
  const match=text.match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/);
  return match?`${match[3]}-${match[2]}-${match[1]}${match[4]?` ${match[4]}:${match[5]}`:''}`:text.slice(0,16);
};
const santiagoDate=value=>{
  const date=new Date(value);
  if(Number.isNaN(date.getTime()))return shortDate(value);
  const parts=new Intl.DateTimeFormat('es-CL',{timeZone:'America/Santiago',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}).formatToParts(date);
  const part=type=>parts.find(item=>item.type===type)?.value||'';
  return `${part('day')}-${part('month')}-${part('year')} ${part('hour')}:${part('minute')}`;
};
const toast=(text,error=false)=>{const el=$('toast');el.textContent=text;el.className=error?'error':'';el.style.display='block';setTimeout(()=>el.style.display='none',4300)};
const reviewReasons=r=>r.flags.map(flag=>{
  if(flag==='Actividad por revisar'){
    const op=String(r.activity??'').trim();
    return !op||!op.replace(/-/g,'').trim()?'OP sin informar':`OP inválida: "${op}" (debe ser numérica)`;
  }
  if(flag==='Avance inválido')return 'Avance sin informar o fuera del rango 0–100';
  if(flag==='Recursos incompletos'){
    const missing=[];
    if(r.workers===null||r.workers===undefined||r.workers<0)missing.push('trabajadores');
    if(r.hours===null||r.hours===undefined||r.hours<0)missing.push('horas');
    return `Recursos incompletos: falta ${missing.join(' y ')||'información'}`;
  }
  return flag;
});
function render(){
  const {reports,ots,areas,counts,last_sync,current_user}=dataset;
  $('account-name').textContent=current_user?`${current_user.username} · ${current_user.role_label}`:'';
  $('users-nav').classList.toggle('hidden',current_user?.role!=='admin');
  $('n-ots').textContent=counts.ots??0;$('n-reports').textContent=counts.reports??0;$('n-flags').textContent=counts.flagged??0;
  $('n-progress').textContent=counts.progress===null||counts.progress===undefined?'—':fmt(counts.progress);
  $('sync').textContent=last_sync?`Última sincronización: ${santiagoDate(last_sync.at)} hora de Santiago · ${last_sync.records} importado(s) · ${last_sync.pending||0} pendiente(s) · ${last_sync.discrepancies||0} discrepancia(s) OT · ${last_sync.conflicts||0} conflicto(s) · ${last_sync.errors||0} error(es) · ${last_sync.source}`:'Sin sincronización';
  const total=Math.max(...Object.values(areas),1);
  $('area-bars').innerHTML=Object.entries(areas).sort((a,b)=>b[1]-a[1]).map(([name,n])=>`<div><div class="bar-label"><span>${escapeHTML(name)}</span><b>${n} reportes</b></div><div class="bar-track"><div class="bar-fill" style="width:${n/total*100}%"></div></div></div>`).join('')||'<p class="empty">Importa el Excel para comenzar.</p>';
  const selected=$('area').value;
  $('area').innerHTML='<option value="">Todas las áreas</option>'+Object.keys(areas).sort().map(s=>`<option value="${escapeHTML(s)}">${escapeHTML(s)}</option>`).join('');$('area').value=selected;
  filterOrders();
  $('log').innerHTML=reports.slice(0,12).map(r=>`<article class="log-item"><strong>OT ${escapeHTML(r.ot)}</strong>${r.flags.length?'<span class="status warn">Revisar</span>':''}<p>${escapeHTML(r.comment||'Sin reseña técnica ingresada.')}</p><small>${escapeHTML(r.area)} · ${escapeHTML(r.reporter)} · ${escapeHTML(shortDate(r.submitted_at))}</small></article>`).join('')||'<p class="empty">Aún no hay reportes.</p>';
}
function filterOrders(resetPage=false){
 if(resetPage)currentPage=1;
 const q=$('search').value.trim().toLowerCase().replace(/\s+/g,' '),area=$('area').value,shift=$('shift').value,attention=$('attention').value;
 const searchText=r=>[r.ot,`OT ${r.ot??''}`,r.activity,`OP ${r.activity??''}`,r.comment,r.reporter].join(' ').toLowerCase().replace(/\s+/g,' ');
 const matches=r=>(!area||r.area===area)&&(!shift||r.shift===shift)&&(!q||searchText(r).includes(q))&&(!attention||(attention==='flag'?r.flags.length:r.progress===0));
 const groups=dataset.ots.map(parent=>{
   const reports=dataset.reports.filter(r=>r.ot===parent.ot).sort((a,b)=>String(b.submitted_at??'').localeCompare(String(a.submitted_at??''))||String(b.id??'').localeCompare(String(a.id??''),undefined,{numeric:true}));
   const latestOperations=[];
   const seenActivities=new Set();
   reports.forEach(report=>{const key=String(report.activity??'').trim()||'__SIN_OP__';if(!seenActivities.has(key)){seenActivities.add(key);latestOperations.push(report)}});
   const candidates=attention?latestOperations:reports;
   return {parent,reports,visibleReports:candidates.filter(matches)};
 }).filter(group=>group.visibleReports.length).sort((a,b)=>String(b.parent.submitted_at??'').localeCompare(String(a.parent.submitted_at??''))||String(b.parent.id??'').localeCompare(String(a.parent.id??''),undefined,{numeric:true}));
 const totalPages=Math.max(1,Math.ceil(groups.length/PAGE_SIZE));
 currentPage=Math.min(currentPage,totalPages);
 const start=(currentPage-1)*PAGE_SIZE;
 const visibleGroups=groups.slice(start,start+PAGE_SIZE);
 $('ot-table').innerHTML=visibleGroups.map(({parent,reports,visibleReports})=>{const isOpen=expandedOts.has(parent.ot);const reviewReason=parent.flags.length?escapeHTML(reviewReasons(parent).join(' · ')):'';const activities=new Set(reports.map(r=>r.activity).filter(Boolean)).size;const opLabel=parent.activity?` · OP ${escapeHTML(parent.activity)}`:'';const parentRow=`<tr class="ot-parent clickable" data-detail-ot="${escapeHTML(parent.ot)}"><td data-label="Orden / actividad"><button class="ot-link" type="button">OT ${escapeHTML(parent.ot)}${opLabel}</button><span class="secondary">${activities} actividad(es) · ${reports.length} reporte(s)</span></td><td data-label="Área / turno">${escapeHTML(parent.area)}<span class="secondary">${escapeHTML(parent.shift||'Sin turno')}</span></td><td data-label="Avance"><strong>${fmt(parent.progress)}</strong><span class="progress"><i style="width:${Math.min(Math.max(parent.progress??0,0),100)}%"></i></span></td><td data-label="Responsable">${escapeHTML(parent.reporter||'Sin asignar')}<span class="secondary">Último: ${escapeHTML(shortDate(parent.submitted_at))}</span></td><td data-label="Estado"><span class="status ${parent.flags.length?'warn has-tooltip':''}"${parent.flags.length?` tabindex="0" aria-label="Revisar: ${reviewReason}" data-tooltip="${reviewReason}"`:''}>${parent.flags.length?'Revisar':'Registrada'}</span></td><td class="card-expand"><button class="expand-ot" type="button" data-expand-ot="${escapeHTML(parent.ot)}" aria-expanded="${isOpen}" aria-label="${isOpen?'Ocultar':'Mostrar'} reportes de OT ${escapeHTML(parent.ot)}">${isOpen?'⌃':'⌄'}</button></td></tr>`;const childRows=isOpen?visibleReports.map(r=>{const childReason=r.flags.length?escapeHTML(reviewReasons(r).join(' · ')):'';return `<tr class="ot-child clickable" data-detail-ot="${escapeHTML(parent.ot)}"><td data-label="Actividad"><span class="activity-id">OP ${escapeHTML(r.activity||'Sin indicar')}</span><span class="secondary">${escapeHTML(r.comment||'Sin comentario técnico')}</span></td><td data-label="Área / turno">${escapeHTML(r.area)}<span class="secondary">${escapeHTML(r.shift||'Sin turno')}</span></td><td data-label="Avance"><strong>${fmt(r.progress)}</strong><span class="progress"><i style="width:${Math.min(Math.max(r.progress??0,0),100)}%"></i></span></td><td data-label="Responsable">${escapeHTML(r.reporter||'Sin asignar')}<span class="secondary">${escapeHTML(shortDate(r.submitted_at))}</span></td><td data-label="Estado"><span class="status ${r.flags.length?'warn has-tooltip':''}"${r.flags.length?` tabindex="0" aria-label="Revisar: ${childReason}" data-tooltip="${childReason}"`:''}>${r.flags.length?'Revisar':'Registrada'}</span></td><td></td></tr>`}).join(''):'';return parentRow+childRows}).join('')||'<tr><td colspan="6" class="empty">No hay OT que coincidan.</td></tr>';
 $('page-info').textContent=groups.length?`${start+1}–${Math.min(start+PAGE_SIZE,groups.length)} de ${groups.length} OT · Página ${currentPage} de ${totalPages}`:'0 resultados';
 $('page-prev').disabled=currentPage===1;
 $('page-next').disabled=currentPage===totalPages;
 document.querySelectorAll('[data-expand-ot]').forEach(button=>button.addEventListener('click',event=>{event.stopPropagation();const ot=button.dataset.expandOt;expandedOts.has(ot)?expandedOts.delete(ot):expandedOts.add(ot);filterOrders()}));
 document.querySelectorAll('[data-detail-ot]').forEach(row=>row.addEventListener('click',()=>detail(row.dataset.detailOt)));
}
function detail(ot){
 const entries=dataset.reports.filter(r=>r.ot===ot).sort((a,b)=>String(b.submitted_at??'').localeCompare(String(a.submitted_at??''))||String(b.id??'').localeCompare(String(a.id??''),undefined,{numeric:true}));
 $('detail-title').textContent=`OT ${ot} · ${entries.length} reporte(s)`;
 $('detail-content').innerHTML='<div class="timeline">'+entries.map(r=>`<div class="event"><h3>${fmt(r.progress)} · ${escapeHTML(r.area)} · ${escapeHTML(r.shift)}</h3><small>${escapeHTML(shortDate(r.submitted_at))} · ${escapeHTML(r.reporter)} · OP ${escapeHTML(r.activity||'Sin informar')}</small><p>${escapeHTML(r.comment||'Sin comentario técnico')}</p>${r.flags.length?`<p class="status warn">${escapeHTML(reviewReasons(r).join(' · '))}</p>`:''}<p class="secondary">${r.workers??'—'} trabajadores · ${r.hours??'—'} horas reportadas</p><div class="photo-links">${r.photos.map((url,i)=>/^https:\/\//i.test(url)?`<a href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">Foto ${['antes','durante','final'][i]} ↗</a>`:'').join('')}</div></div>`).join('')+'</div>';
 $('detail').showModal();
}
async function load(){try{const r=await fetch('/api/status');if(!r.ok)throw Error('No fue posible consultar datos');dataset=await r.json();render()}catch(e){toast(e.message,true)}}
['search','area','shift','attention'].forEach(id=>$(id).addEventListener(id==='search'?'input':'change',()=>filterOrders(true)));
$('page-prev').addEventListener('click',()=>{currentPage--;filterOrders()});
$('page-next').addEventListener('click',()=>{currentPage++;filterOrders()});
$('close').addEventListener('click',()=>$('detail').close());
load();
