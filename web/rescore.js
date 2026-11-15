/* Optional in-browser kNN sensitivity calculation from docs/plan.md §15a.
   It reads the existing earth_library.json without changing any backend files or values. */
(() => {
  'use strict';
  const num = x => x !== null && x !== undefined && x !== '' && Number.isFinite(Number(x)) ? Number(x) : null;
  const wrap = lon => ((lon+180)%360+360)%360-180;
  function percentile(value, quantiles) {
    if (!Array.isArray(quantiles) || quantiles.length < 2) return null;
    const x = num(value); if (x === null) return null;
    const last = quantiles.length-1;
    if (x <= quantiles[0]) return 0;
    if (x >= quantiles[last]) return 1;
    let lo=0,hi=last;
    while(lo+1<hi){const mid=(lo+hi)>>1;if(quantiles[mid] < x)lo=mid;else hi=mid;}
    const a=Number(quantiles[lo]), b=Number(quantiles[hi]);
    return (lo+(b===a?0:(x-a)/(b-a)))/last;
  }
  function normalized(mars, f) {
    const key=f.name === 'temp_mean_rank'?'temp_mean':f.name;
    let raw=mars?.[key];if(raw && typeof raw === 'object')raw=raw.value??raw.value_num??raw.value_cat;
    if(raw===null||raw===undefined||raw==='')return null;
    switch(f.compare){
      case 'percentile':return percentile(raw,f.mars_quantiles);
      case 'absolute': case 'steps':return num(raw);
      case 'log_z':{const x=num(raw),mean=num(f.earth_log_mean),sd=num(f.earth_log_std);return x===null||mean===null||!sd?null:(Math.log1p(Math.max(0,x))-mean)/sd;}
      case 'same': {
        const key=String(raw).toLowerCase();
        if(!f.codes || typeof f.codes!=='object')return null;
        const alias=key==='true'?'yes':key==='false'?'no':key;
        return f.codes[alias]??null;
      }
      default:return null;
    }
  }
  function greatCircle(a,b){
    const deg=Math.PI/180;
    const dlat=(a.lat-b.lat)*deg,dlon=wrap(a.lon-b.lon)*deg;
    const h=Math.sin(dlat/2)**2+Math.cos(a.lat*deg)*Math.cos(b.lat*deg)*Math.sin(dlon/2)**2;
    return 12742*Math.asin(Math.min(1,Math.sqrt(Math.max(0,h))));
  }
  function scorePlaces(mars,band,library,weights){
    if(!band?.features?.length || !Array.isArray(band.places))throw new Error('Invalid Earth library data');
    if(!mars || typeof mars!=='object')throw new Error('Mars features are unavailable for this cell');
    const features=band.features;
    const values=features.map(f=>normalized(mars,f));
    const w=features.map(f=>Math.max(0,Number(weights?.[f.name]??band.weights?.[f.name]??1)));
    const weightAll=w.reduce((a,b)=>a+b,0);
    if(weightAll===0)throw new Error('At least one weight must be greater than zero');
    const matches=[];
    for(const place of band.places){
      if(!Array.isArray(place.v)||!Number.isFinite(Number(place.lat))||!Number.isFinite(Number(place.lon)))continue;
      let total=0,used=0;const parts=[];
      for(let i=0;i<features.length;i++){
        if(w[i]<=0 || values[i]===null || place.v[i]===null || place.v[i]===undefined)continue;
        const e=num(place.v[i]),m=num(values[i]);if(e===null||m===null)continue;
        const f=features[i];
        const s=f.compare==='steps'?Number(f.max)-Number(f.min):f.compare==='percentile'||f.compare==='same'?1:Number(f.scale);
        if(f.compare!=='same' && !(s>0))continue;
        const d=f.compare==='same'?(e===m?0:1):Math.min(1,Math.abs(e-m)/s);
        used+=w[i];total+=w[i]*d;parts.push({name:f.name,d,weighted:w[i]*d});
      }
      if(!used)continue;
      const coverage=used/weightAll,distance=total/used;
      const steepness=Number(band.steepness)||.1;
      const match_pct=Math.round(1000*Math.exp(-distance/steepness))/10;
      const insufficient=coverage<Number(library.min_coverage??.5);
      const levels=library.levels||{};
      const level=insufficient?'insufficient_data':match_pct>=Number(levels.excellent??85)?'excellent':match_pct>=Number(levels.good??70)?'good':match_pct>=Number(levels.fair??50)?'fair':match_pct>=Number(levels.poor??30)?'poor':'no_match';
      const best=parts.filter(p=>p.d<.5).sort((a,b)=>a.d-b.d).slice(0,3).map(p=>p.name);
      const worst=parts.filter(p=>p.d>0).sort((a,b)=>b.weighted-a.weighted).slice(0,3).map(p=>p.name);
      matches.push({region_id:place.id,name:place.name,new_site:!!place.new_site,lat:Number(place.lat),lon:Number(place.lon),match_pct,level,best,worst,confidence:'not recalculated',_dist:distance});
    }
    return matches.sort((a,b)=>a._dist-b._dist);
  }
  function score(mars,band,library,weights,topN=3){
    const matches=scorePlaces(mars,band,library,weights);
    const chosen=[];
    for(const m of matches){if(chosen.every(c=>greatCircle(c,m)>=Number(library.spacing_km??200)))chosen.push(m);if(chosen.length>=topN)break;}
    return chosen.map(({_dist,...rest})=>rest);
  }
  window.TwinRescore={score,scorePlaces,normalized,percentile};
})();
