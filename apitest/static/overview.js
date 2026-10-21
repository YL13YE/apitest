/* Recommendation order is based only on measured speed and completed responses. */
(function(root, factory) {
  const picks = factory();
  if(typeof module === 'object' && module.exports) module.exports = picks;
  else root.ModelPicks = picks;
})(typeof globalThis !== 'undefined' ? globalThis : this, function() {
  const positive = value => typeof value === 'number' && Number.isFinite(value) && value > 0;
  function timing(v) {
    const m=v.metrics||{};
    if(positive(m.ttfa_p50)&&positive(m.tps_p50))return {basis:'answer100',value:m.ttfa_p50+100/m.tps_p50};
    if(positive(m.ttfa_p50))return {basis:'first',value:m.ttfa_p50};
    if(positive(m.total_p50))return {basis:'total',value:m.total_p50};
    return {basis:'unknown',value:Infinity};
  }
  function rank(variants) {
    const order={answer100:0,first:1,total:2,unknown:3};
    return [...variants].sort((a,b)=>{
      const A=a.metrics||{},B=b.metrics||{},at=timing(a),bt=timing(b);
      const healthyA=A.complete>0&&A.complete_rate>=.9,healthyB=B.complete>0&&B.complete_rate>=.9;
      return Number(!healthyA)-Number(!healthyB)||order[at.basis]-order[bt.basis]||at.value-bt.value||(B.n||0)-(A.n||0)||String(a.model).localeCompare(String(b.model));
    });
  }
  function choose(variants, limit=3) {
    const available=rank(variants).filter(v=>v.metrics?.complete>0&&v.metrics.complete_rate>=.9&&timing(v).basis!=='unknown');
    if(!available.length)return [];
    const basis=timing(available[0]).basis,used=new Set(),result=[];
    for(const v of available) {
      if(timing(v).basis!==basis||used.has(v.model))continue;
      used.add(v.model);result.push({variant:v,...timing(v)});
      if(result.length>=limit)break;
    }
    return result;
  }
  function fastestStarts(variants, limit=8) {
    const used=new Set();return [...variants].filter(v=>v.metrics?.complete>0&&positive(v.metrics.ttfa_p50)).sort((a,b)=>a.metrics.ttfa_p50-b.metrics.ttfa_p50).filter(v=>{if(used.has(v.model))return false;used.add(v.model);return true;}).slice(0,limit);
  }
  function roundTiming({results=[],targetCount=0,retries=0,progress=null,pending=null,phase=null,phaseObservedAt=0,evidenceAt=0,running=true,now=Date.now()}) {
    const rows=results.filter(r=>!r.warmup&&Number.isInteger(r.index)&&r.index>=0);
    const rowIndex=rows.reduce((value,r)=>Math.max(value,r.index),-1);
    const validHint=hint=>hint&&Number.isInteger(hint.index)&&hint.index>=0;
    const index=Math.max(0,rowIndex,validHint(progress)?progress.index:-1,validHint(pending)?pending.index:-1);
    const current=rows.filter(r=>r.index===index),starts=[],ends=[],latest=new Map();
    for(const row of current) {
      if(Number.isFinite(row.started_at)&&row.started_at>0){starts.push(row.started_at*1000);if(Number.isFinite(row.total)&&row.total>=0)ends.push((row.started_at+row.total)*1000);}
      const key=JSON.stringify([row.target,row.model]),prior=latest.get(key);
      if(!prior||(row.attempt||1)>=(prior.attempt||1))latest.set(key,row);
    }
    if(validHint(progress)&&progress.index===index&&Number.isFinite(progress.startedAt))starts.push(progress.startedAt);
    const completed=[...latest.values()].filter(r=>r.ok||[400,401,403,404].includes(r.status_code)||(r.attempt||1)>=retries+1).length;
    const complete=targetCount>0&&completed>=targetCount;
    const start=starts.length?Math.min(...starts):(validHint(pending)&&pending.index===index?pending.startedAt:null);
    const waiting=phase==='waiting'&&phaseObservedAt>=evidenceAt;
    const active=!!running&&!complete&&!waiting&&Number.isFinite(start);
    const end=active?now:(ends.length?Math.max(...ends):start);
    return {round:index+1,index,seconds:Number.isFinite(start)&&Number.isFinite(end)?Math.max(0,(end-start)/1000):0,
      active,waiting:!!running&&(waiting||complete),completed,rowIndex};
  }
  return {choose,rank,timing,fastestStarts,roundTiming};
});
/* Read-only experience view. No endpoint configuration or model calls. */
(() => {
  'use strict';
  if(typeof document === 'undefined') return;
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = (n, digits=1) => n == null || !Number.isFinite(Number(n)) ? '—' : Number(n).toFixed(digits);
  const sec = n => n == null ? '—' : n < 1 ? `${num(n*1000,0)} ms` : `${num(n,2)} s`;
  const pct = n => n == null ? '—' : `${num(n*100,1)}%`;
  const PRESET_EFFORTS = ['low','medium','high','xhigh'];
  const state = {data:null, chart:null, bars:null, selected:null, bound:false, request:0, timer:null};
  const tone = value => ['good','warn','bad','neutral'].includes(value) ? value : 'neutral';
  const pill = b => `<span class="ov-pill" data-tone="${tone(b.tone)}" title="${esc(b.detail)}">${esc(b.label)}</span>`;
  const label = v => v.effort ? `${v.model} · ${v.effort}` : v.model;

  function hashSettings() { return new URLSearchParams((location.hash.split('?')[1] || '')); }
  function linkSettings() {
    const p = new URLSearchParams();
    for (const key of ['hours','profile','cohort','effort','sort','ttfa','tps','gap','search']) if ($('ov-'+key)?.value) p.set(key,$('ov-'+key).value);
    history.replaceState(null,'', '#/overview?'+p.toString());
  }
  function init() {
    if(state.bound) return; state.bound=true;
    $('ov-refresh').onclick=()=>fetchData();
    for(const key of ['hours','profile','cohort']) $('ov-'+key).onchange=event=>{
      if(key==='profile') {
        const reasoning=$('ov-profile').value==='reasoning';
        $('ov-ttfa').value=reasoning?15:3; $('ov-tps').value=reasoning?20:30; $('ov-gap').value=reasoning?1.5:1;
      }
      fetchData(key==='cohort'?event.currentTarget.value:undefined);
    };
    $('ov-apply').onclick=()=>fetchData();
    for(const key of ['effort','sort']) $('ov-'+key).onchange=()=>{linkSettings();render();};
    $('ov-search').oninput=()=>{linkSettings();render();};
    $('ov-print').onclick=()=>window.print();
    $('ov-copy').onclick=async()=>{
      linkSettings(); const text=location.href;
      try{await navigator.clipboard.writeText(text); $('ov-status').textContent='链接已复制';}
      catch(e){$('ov-status').textContent='当前视图链接：'+text;}
    };
    $('ov-export').onclick=()=>{
      if(!state.data) return;
      const rows=filtered(), payload={version:state.data.version,generated_at:state.data.generated_at,hours:state.data.hours,profile:state.data.profile,policy:state.data.policy,cohort:state.data.cohorts.find(c=>c.id===state.data.cohort),filters:{search:$('ov-search').value,effort:$('ov-effort').value},variants:rows,sources:state.data.sources,limitations:state.data.limitations}; const blob=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');
      a.href=url;a.download='model-experience.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    };
    $('ov-auto').onchange=()=>{
      clearInterval(state.timer);state.timer=null;
      if($('ov-auto').checked)state.timer=setInterval(()=>{if(location.hash.startsWith('#/overview'))fetchData();},window.APITEST_PUBLIC?60000:30000);
    };
    new MutationObserver(()=>{if(state.data && location.hash.startsWith('#/overview'))drawChart(filtered());}).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
  }
  window.loadModelOverview=async()=>{
    init(); const p=hashSettings(); if(p.get('profile')==='reasoning') { $('ov-ttfa').value=15; $('ov-tps').value=20; $('ov-gap').value=1.5; }
    for(const key of ['hours','profile','effort','sort','ttfa','tps','gap','search']) if(p.has(key)) { const el=$('ov-'+key); if(el.tagName!=='SELECT'||[...el.options].some(o=>o.value===p.get(key))) el.value=p.get(key); }
    await fetchData(p.get('cohort')||'short');
  };
  async function fetchData(cohortOverride) {
    const serial=++state.request;
    const params=new URLSearchParams({hours:$('ov-hours').value,profile:$('ov-profile').value});
    const requested=typeof cohortOverride==='string'&&cohortOverride?cohortOverride:($('ov-cohort').value||'short');
    const cohort=requested==='all'?'short':requested;params.set('cohort',cohort);
    for(const key of ['ttfa','tps','gap']) {
      const value=Number($('ov-'+key).value);
      if(!Number.isFinite(value)||value<=0){$('ov-status').textContent='评价阈值须为正数。';return;}
      params.set(key,value);
    }
    $('ov-refresh').disabled=true;$('ov-status').textContent='更新中…';
    try {
      let response=await api('/api/overview?'+params);
      if(serial!==state.request)return;
      const selected=response.cohorts.find(c=>c.id===cohort)||response.cohorts.find(c=>c.settings?.preset==='short')||response.cohorts[0];
      if(selected&&response.cohort!==selected.id) {
        params.set('cohort',selected.id);
        response=await api('/api/overview?'+params);
        if(serial!==state.request)return;
      }
      state.data=response;
      const choices=response.cohorts.map(c=>`<option value="${esc(c.id)}">${esc(c.label)}</option>`).join('')||'<option value="">暂无测试记录</option>';
      if($('ov-cohort').innerHTML!==choices)$('ov-cohort').innerHTML=choices;
      $('ov-cohort').value=response.cohort||'';
      const chosen=$('ov-effort').value,present=new Set(response.variants.map(v=>v.effort)),efforts=PRESET_EFFORTS;
      $('ov-effort').innerHTML='<option value="">全部强度</option>'+efforts.map(e=>`<option value="${esc(e)}">${esc(e)}${present.has(e)?'':'（暂无记录）'}</option>`).join('');
      if(efforts.includes(chosen))$('ov-effort').value=chosen;
      else if(hashSettings().has('effort')&&efforts.includes(hashSettings().get('effort')))$('ov-effort').value=hashSettings().get('effort');
      linkSettings();render();
      $('ov-status').textContent=`更新 ${new Date(response.generated_at*1000).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit'})}`;
    } catch(e) {if(serial===state.request)$('ov-status').textContent='数据加载失败：'+e.message+(window.APITEST_PUBLIC?'；请稍后重试。':'；会话过期请刷新登录。');}
    finally{if(serial===state.request)$('ov-refresh').disabled=false;}
  }
  function filtered() {
    if(!state.data)return[];
    const query=$('ov-search').value.trim().toLowerCase(),effort=$('ov-effort').value;
    const variants=state.data.variants.filter(v=>(!effort||v.effort===effort)&&(!query||`${v.model} ${v.endpoint} ${v.effort||''}`.toLowerCase().includes(query)));
    const key=$('ov-sort').value;
    if(key==='fast')return ModelPicks.rank(variants);
    return variants.sort((a,b)=>{
      if(key==='model')return label(a).localeCompare(label(b));
      if(key==='ttfa')return (a.metrics.ttfa_p50??Infinity)-(b.metrics.ttfa_p50??Infinity);
      if(key==='total')return (a.metrics.total_p50??Infinity)-(b.metrics.total_p50??Infinity);
      if(key==='tps')return (b.metrics.tps_p50??-1)-(a.metrics.tps_p50??-1);
      if(key==='samples')return b.metrics.n-a.metrics.n;
      return (a.metrics.n<5)-(b.metrics.n<5)||(b.metrics.good_rate??-1)-(a.metrics.good_rate??-1)||(a.metrics.ttfa_p50??Infinity)-(b.metrics.ttfa_p50??Infinity);
    });
  }
  function modelTitle(v) { return `<h3 class="ov-model-title" title="${esc(label(v))}"><span class="ov-model-name">${esc(v.model)}</span>${v.effort?`<span class="ov-effort">${esc(v.effort)}</span>`:''}</h3>`; }
  function sampleTime(v) {
    const date=new Date(v.last_sample*1000);
    if(!Number.isFinite(v.last_sample)||!Number.isFinite(date.getTime()))return '<span class="ov-test-time">测试时间未知</span>';
    const options={timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'};
    const short=date.toLocaleString('zh-CN',options).replace(/\//g,'-');
    const full=date.toLocaleString('zh-CN',{...options,year:'numeric',second:'2-digit'});
    return `<time class="ov-test-time" datetime="${date.toISOString()}" title="最近一次测试：${esc(full)}（北京时间）">测试于 ${esc(short)}</time>`;
  }
  function render() {
    if(!state.data)return;const variants=filtered();
    $('ov-result-count').textContent=variants.length;
    renderPicks(variants);
    $('ov-cards').innerHTML=variants.length?variants.map(card).join(''):'<div class="empty big">暂无匹配结果</div>';
    $('ov-cards').querySelectorAll('[data-ov-select]').forEach(button=>button.onclick=()=>{
      state.selected=button.dataset.ovSelect;renderDetail();$('ov-detail').scrollIntoView({behavior:'smooth',block:'start'});
    });
    if(!variants.some(v=>v.id===state.selected))state.selected=null;
    drawChart(variants);renderDetail();renderHeatmap(variants);renderCriteria();
    $('ov-method-sources').innerHTML='参考资料：'+state.data.sources.map(s=>`<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title.replace('NVIDIA：Goodput 与体验约束','NVIDIA 性能指标'))}</a>`).join(' · ');
  }
  function renderPicks(variants) {
    const choices=ModelPicks.choose(variants);
    $('ov-picks').innerHTML=choices.length?choices.map(({variant:v,basis},index)=>{
      const m=v.metrics,reason=basis==='answer100'?'综合速度优先':basis==='first'?'首字等待优先':'总耗时优先';
      return `<article class="ov-pick"><div class="ov-pick-top"><span class="ov-pick-rank">0${index+1}</span>${modelTitle(v)}</div><div class="ov-pick-values"><div><span>${m.ttfa_p50==null?'总耗时':'首字等待'}</span><strong>${sec(m.ttfa_p50??m.total_p50)}</strong></div><div><span>输出速度</span><strong>${num(m.tps_p50)} <small>tok/s</small></strong></div></div><div class="ov-pick-foot"><span>${reason} · ${m.n} 次测试</span><button class="btn btn-sm" data-ov-card="${v.id}">查看数据</button></div></article>`;
    }).join(''):'<div class="empty">暂无可推荐的测速结果</div>';
    $('ov-picks').querySelectorAll('[data-ov-card]').forEach(button=>button.onclick=()=>{
      document.querySelectorAll('.ov-card-selected').forEach(el=>el.classList.remove('ov-card-selected'));
      const target=$('ov-card-'+button.dataset.ovCard);if(target){target.classList.add('ov-card-selected');target.scrollIntoView({behavior:'smooth',block:'center'});}
    });
  }
  function card(v) {
    const m=v.metrics,a=v.assessment;
    const badges=[a.response,a.output,a.fluidity,a.stability].filter(b=>b.tone!=='neutral');
    return `<article class="ov-card" id="ov-card-${v.id}"><div class="ov-card-title"><div class="ov-card-heading">${modelTitle(v)}<small>${esc(v.endpoint)}</small></div><div class="ov-card-meta">${sampleTime(v)}${a.overall.tone!=='neutral'?pill(a.overall):''}</div></div>
      ${badges.length?'<div class="ov-badges">'+badges.map(pill).join('')+'</div>':''}
      <div class="ov-values"><div><span>${m.ttfa_p50==null?'总耗时':'首字等待'}</span><strong>${sec(m.ttfa_p50??m.total_p50)}</strong></div><div><span>输出速度</span><strong>${num(m.tps_p50)} <small>tok/s</small></strong></div><div><span>完成率</span><strong>${pct(m.complete_rate)}</strong></div><div><span>最长停顿</span><strong>${sec(m.max_gap)}</strong></div></div>
      <div class="ov-card-foot"><span>${m.n} 次测试${v.cost==null?'':' · '+num(v.cost,6)+' '+esc(v.currency)}</span><button class="btn btn-sm" data-ov-select="${v.id}">详细数据</button></div></article>`;
  }
  function renderCriteria() {
    const p=state.data.policy;
    const rules=[['响应快',`首字等待中位数 ≤ ${sec(p.ttfa/2)}`],['输出快',`输出速度中位数 ≥ ${num(p.tps,0)} token/s`],['流畅',`至少95%的有效请求，最长正文停顿 ≤ ${sec(p.gap)}`],['初步好用 / 好用',`5–19次 / 至少20次测试，完成率 ≥ 90%，体验达标率 ≥ 90%；达标要求首字 ≤ ${sec(p.ttfa)}、输出 ≥ ${num(p.tps,0)} token/s、停顿 ≤ ${sec(p.gap)}`],['稳','至少100次测试，跨度 ≥ 1小时；完成率95%区间下限 ≥ 95%，P90/P50 ≤ 1.5，标准化MAD/P50 ≤ 25%，流畅请求 ≥ 95%']];
    $('ov-criteria').innerHTML='<table><tbody>'+rules.map(([name,rule])=>`<tr><td class="l">${name}</td><td class="l">${esc(rule)}</td></tr>`).join('')+'</tbody></table>';
  }
  function drawChart(variants) {
    if(state.chart){state.chart.destroy();state.chart=null;}
    if(state.bars){state.bars.destroy();state.bars=null;}
    const bars=ModelPicks.fastestStarts(variants); $('ov-bars-empty').hidden=bars.length>0; $('ov-bars').hidden=!bars.length;
    if(bars.length && typeof Chart!=='undefined')state.bars=new Chart($('ov-bars'),{type:'bar',data:{labels:bars.map(label),datasets:[{data:bars.map(v=>v.metrics.ttfa_p50),backgroundColor:'#527d73',borderRadius:2,maxBarThickness:18}]},options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,animation:false,plugins:{legend:{display:false}},scales:{x:{beginAtZero:true,title:{display:true,text:'秒'}},y:{grid:{display:false}}}}});
    const valid=variants.filter(v=>v.metrics.ttfa_p50!=null&&v.metrics.tps_p50!=null);
    $('ov-chart-empty').hidden=valid.length>0;$('ov-scatter').hidden=!valid.length;
    if(!valid.length||typeof Chart==='undefined')return;
    const colors={good:'#168268',warn:'#ae791c',bad:'#c54c4c',neutral:'#748493'};
    state.chart=new Chart($('ov-scatter'),{type:'scatter',data:{datasets:valid.map(v=>({label:label(v),data:[{x:v.metrics.ttfa_p50,y:v.metrics.tps_p50}],pointRadius:Math.min(13,5+Math.log2(v.metrics.n+1)),pointHoverRadius:13,backgroundColor:colors[tone(v.assessment.overall.tone)]}))},options:{responsive:true,maintainAspectRatio:false,animation:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:ctx=>`${ctx.dataset.label}：首正文 ${sec(ctx.parsed.x)}，${num(ctx.parsed.y)} tok/s`}}},scales:{x:{beginAtZero:true,title:{display:true,text:'首正文等待（秒）→ 越靠左越快'}},y:{beginAtZero:true,title:{display:true,text:'正文 token/s → 越高越快'}}}}});
  }
  function renderDetail() {
    const v=state.data?.variants.find(v=>v.id===state.selected),box=$('ov-detail');if(!v){box.hidden=true;return;}box.hidden=false;
    const m=v.metrics,a=v.assessment;
    $('ov-detail-title').textContent=label(v)+' · '+v.endpoint;
    $('ov-detail-badges').innerHTML=pill(a.overall)+' '+pill(a.stability)+' '+pill(a.evidence);
    $('ov-detail-metrics').innerHTML=[['完整耗时 P50',sec(m.total_p50)],['首正文 P50',sec(m.ttfa_p50)],['首正文 P90',m.ttfa_p90==null?'样本不足20次':sec(m.ttfa_p90)],['首正文 P95',m.ttfa_p95==null?'样本不足100次':sec(m.ttfa_p95)],['正文中位速度',num(m.tps_p50)+' tok/s'],['辅助字符速度',num(m.chars_p50)+' 字符/s'],['最长正文停顿',sec(m.max_gap)],['流畅请求占比',pct(m.smooth_rate)],['完整率95%区间',m.complete_interval?m.complete_interval.map(pct).join(' ～ '):'—'],['尾延迟比 P90/P50',num(m.tail_ratio,2)],['稳健波动 MAD/P50',pct(m.robust_cv)],['无法判定体验的请求',m.unmeasured+' 次'],...(state.data.public?[]:[['费用',v.cost==null?'无法估算（不等于免费）':num(v.cost,6)+' '+v.currency]])].map(([name,value])=>`<div><span>${name}</span><strong>${esc(value)}</strong></div>`).join('');
    const notes=[a.overall.detail,a.stability.detail,...v.notes].filter(Boolean);
    $('ov-detail-notes').innerHTML=notes.map(s=>`<li>${esc(s)}</li>`).join('');
    $('ov-errors').textContent=Object.entries(v.errors).map(([key,n])=>key+' × '+n).join('；')||'所选记录中没有接口错误。';
  }
  function renderHeatmap(variants) {
    const hours=[...new Set(variants.flatMap(v=>v.hourly.map(h=>h.hour)))].sort().slice(-24);
    $('ov-heatmap').innerHTML=hours.length?'<table><thead><tr><th class="l">模型 / 强度</th>'+hours.map(h=>`<th title="${esc(h)}">${esc(h.slice(5,16))}</th>`).join('')+'</tr></thead><tbody>'+variants.map(v=>'<tr><td class="l">'+esc(label(v))+'<small class="muted"> · '+esc(v.endpoint)+'</small></td>'+hours.map(hour=>{
      const h=v.hourly.find(x=>x.hour===hour);if(!h)return'<td class="ov-cell-missing">—</td>';
      const color=h.ttfa==null?'neutral':h.ttfa<=state.data.policy.ttfa?'good':h.ttfa<=2*state.data.policy.ttfa?'warn':'bad';
      return `<td class="ov-heat-cell" data-tone="${color}" title="${esc(hour)} · ${h.n}次 · 完整率${pct(h.complete_rate)} · 达标${pct(h.good_rate)}">${sec(h.ttfa)}<small>${h.n} 次</small></td>`;
    }).join('')+'</tr>').join('')+'</tbody></table>':'<div class="empty">暂无小时记录</div>';
  }
})();
