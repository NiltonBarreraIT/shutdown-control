let dataset={reports:[],ots:[],areas:{},counts:{}};
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
const toast=(text,error=false)=>{const el=$('toast');el.textContent=text;el.className=error?'error':'';el.style.display='block';setTimeout(()=>el.style.display='none',4300)};
function render(){
  const {reports,ots,areas,counts,last_sync}=dataset;
  $('n-ots').textContent=counts.ots??0;$('n-reports').textContent=counts.reports??0;$('n-flags').textContent=counts.flagged??0;
  const valid=ots.filter(r=>r.progress!==null&&r.progress!==undefined);
  $('n-progress').textContent=valid.length?fmt(valid.reduce((s,r)=>s+r.progress,0)/valid.length):'—';
  $('sync').textContent=last_sync?`Última sincronización: ${shortDate(last_sync.at)} UTC · ${last_sync.records} importado(s) · ${last_sync.pending||0} pendiente(s) · ${last_sync.discrepancies||0} discrepancia(s) OT · ${last_sync.errors||0} error(es) · ${last_sync.source}`:'Sin sincronización';
  const total=Math.max(...Object.values(areas),1);
  $('area-bars').innerHTML=Object.entries(areas).sort((a,b)=>b[1]-a[1]).map(([name,n])=>`<div><div class="bar-label"><span>${escapeHTML(name)}</span><b>${n} reportes</b></div><div class="bar-track"><div class="bar-fill" style="width:${n/total*100}%"></div></div></div>`).join('')||'<p class="empty">Importa el Excel para comenzar.</p>';
  const selected=$('area').value;
  $('area').innerHTML='<option value="">Todas las áreas</option>'+Object.keys(areas).sort().map(s=>`<option value="${escapeHTML(s)}">${escapeHTML(s)}</option>`).join('');$('area').value=selected;
  filterOrders();
  $('log').innerHTML=reports.slice(0,12).map(r=>`<article class="log-item"><strong>OT ${escapeHTML(r.ot)}</strong>${r.flags.length?'<span class="status warn">Revisar</span>':''}<p>${escapeHTML(r.comment||'Sin reseña técnica ingresada.')}</p><small>${escapeHTML(r.area)} · ${escapeHTML(r.reporter)} · ${escapeHTML(shortDate(r.submitted_at))}</small></article>`).join('')||'<p class="empty">Aún no hay reportes.</p>';
}
function filterOrders(){
 const q=$('search').value.toLowerCase(),area=$('area').value,shift=$('shift').value,attention=$('attention').value;
 const rows=dataset.ots.filter(r=>(!area||r.area===area)&&(!shift||r.shift===shift)&&(!q||[r.ot,r.activity,r.comment,r.reporter].join(' ').toLowerCase().includes(q))&&(!attention||(attention==='flag'?r.flags.length:r.progress===0)));
 $('ot-table').innerHTML=rows.map(r=>{const reviewReason=r.flags.length?escapeHTML(r.flags.join(' · ')):'';return `<tr class="clickable" data-ot="${escapeHTML(r.ot)}"><td><span class="ot-id">${escapeHTML(r.ot)}</span><span class="secondary">Actividad ${escapeHTML(r.activity||'Sin indicar')}</span></td><td>${escapeHTML(r.area)}<span class="secondary">${escapeHTML(r.shift||'Sin turno')}</span></td><td><strong>${fmt(r.progress)}</strong><span class="progress"><i style="width:${Math.min(Math.max(r.progress??0,0),100)}%"></i></span></td><td>${escapeHTML(r.reporter||'Sin asignar')}<span class="secondary">${escapeHTML(shortDate(r.report_date))}</span></td><td><span class="status ${r.flags.length?'warn has-tooltip':''}"${r.flags.length?` tabindex="0" aria-label="Revisar: ${reviewReason}" data-tooltip="${reviewReason}"`:''}>${r.flags.length?'Revisar':'Registrada'}</span></td><td>›</td></tr>`}).join('')||'<tr><td colspan="6" class="empty">No hay OT que coincidan.</td></tr>';
 document.querySelectorAll('[data-ot]').forEach(el=>el.addEventListener('click',()=>detail(el.dataset.ot)));
}
function detail(ot){
 const entries=dataset.reports.filter(r=>r.ot===ot);
 $('detail-title').textContent=`OT ${ot} · ${entries.length} reporte(s)`;
 $('detail-content').innerHTML='<div class="timeline">'+entries.map(r=>`<div class="event"><h3>${fmt(r.progress)} · ${escapeHTML(r.area)} · ${escapeHTML(r.shift)}</h3><small>${escapeHTML(shortDate(r.submitted_at))} · ${escapeHTML(r.reporter)} · Actividad ${escapeHTML(r.activity)}</small><p>${escapeHTML(r.comment||'Sin comentario técnico')}</p>${r.flags.length?`<p class="status warn">${escapeHTML(r.flags.join(' · '))}</p>`:''}<p class="secondary">${r.workers??'—'} trabajadores · ${r.hours??'—'} horas reportadas</p><div class="photo-links">${r.photos.map((url,i)=>/^https:\/\//i.test(url)?`<a href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">Foto ${['antes','durante','final'][i]} ↗</a>`:'').join('')}</div></div>`).join('')+'</div>';
 $('detail').showModal();
}
async function load(){try{const r=await fetch('/api/status');if(!r.ok)throw Error('No fue posible consultar datos');dataset=await r.json();render()}catch(e){toast(e.message,true)}}
['search','area','shift','attention'].forEach(id=>$(id).addEventListener(id==='search'?'input':'change',filterOrders));
$('close').addEventListener('click',()=>$('detail').close());
load();
