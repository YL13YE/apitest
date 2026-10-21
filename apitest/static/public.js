/* Static data adapter. All requests stay on this GitHub Pages site. */
(() => {
  'use strict';
  window.APITEST_PUBLIC = true;
  let pending=null,loaded=null,loadedAt=0;
  const date = value => new Date(value*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
  function freshness() {
    const el=document.getElementById('public-freshness');
    if(!el||!loaded)return;
    const age=loaded.last_sample?Math.max(0,Date.now()/1000-loaded.last_sample):null;
    const ago=age==null?'暂无测试':age<60?'刚刚测试':age<3600?`${Math.floor(age/60)} 分钟前测试`:`${Math.floor(age/3600)} 小时前测试`;
    el.textContent=ago;el.title=`数据更新：${date(loaded.generated_at)}（北京时间）${loaded.last_sample?'；最后测试：'+date(loaded.last_sample):''}`;
  }
  async function load() {
    if(loaded&&Date.now()-loadedAt<1500)return loaded;
    if(!pending)pending=fetch('./data.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error('暂时无法获取测试数据');return r.json();}).then(data=>{
      if(data.format!=='apitest-public-v1')throw Error('展示数据格式不匹配');
      loaded=data;loadedAt=Date.now();freshness();return data;
    }).finally(()=>{pending=null;});
    return pending;
  }
  window.api=async path=>{
    const url=new URL(path,location.origin);
    if(url.pathname!=='/api/overview')throw Error('此页面仅提供测速指标展示');
    const data=await load(),hours=url.searchParams.get('hours')||'24',profile=url.searchParams.get('profile')||'interactive';
    const snapshot=data.windows[hours]?.[profile]||data.windows['24'][profile]||data.windows['24'].interactive;
    const requested=url.searchParams.get('cohort');
    const chosen=snapshot.cohorts.find(c=>c.id===requested)||snapshot.cohorts.find(c=>c.settings.preset==='short')||snapshot.cohorts[0];
    const variants=snapshot.variants.filter(v=>v.cohort===chosen?.id);
    return {...snapshot,generated_at:data.generated_at,cohort:chosen?.id||null,variants,
      summary:{...snapshot.summary,variants:variants.length,models:new Set(variants.map(v=>v.model)).size,requests:variants.reduce((n,v)=>n+v.metrics.n,0)}};
  };
  addEventListener('DOMContentLoaded',()=>{
    const button=document.getElementById('public-theme');
    let dark=false;try{dark=localStorage.getItem('apitest-public-theme')==='dark';}catch(e){}
    const theme=()=>{document.documentElement.dataset.theme=dark?'dark':'light';button.textContent=dark?'浅色':'深色';};theme();
    button.onclick=()=>{dark=!dark;theme();try{localStorage.setItem('apitest-public-theme',dark?'dark':'light');}catch(e){}};
    const show=()=>{if(!location.hash.startsWith('#/overview'))history.replaceState(null,'','#/overview');window.loadModelOverview();};
    addEventListener('hashchange',show);show();
    document.getElementById('ov-auto').dispatchEvent(new Event('change'));
    setInterval(freshness,60000);
  });
})();
