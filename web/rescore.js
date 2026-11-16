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
  const EARTH_R_KM=6371, MARS_R_KM=3389.5;
  function greatCircle(a,b,radius=EARTH_R_KM){
    const deg=Math.PI/180;
    const dlat=(a.lat-b.lat)*deg,dlon=wrap(a.lon-b.lon)*deg;
    const h=Math.sin(dlat/2)**2+Math.cos(a.lat*deg)*Math.cos(b.lat*deg)*Math.sin(dlon/2)**2;
    return 2*radius*Math.asin(Math.min(1,Math.sqrt(Math.max(0,h))));
  }
  const featureScale=f=>f.compare==='steps'?Number(f.max)-Number(f.min):f.compare==='percentile'||f.compare==='same'?1:Number(f.scale);
  function weightsFor(band,weights){
    const w=band.features.map(f=>Math.max(0,Number(weights?.[f.name]??band.weights?.[f.name]??1)));
    const all=w.reduce((a,b)=>a+b,0);
    if(all===0)throw new Error('At least one weight must be greater than zero');
    return {w,all};
  }
  /* Weighted Gower distance between one Earth place (normalized `v`) and one Mars cell (normalized values).
     The same function serves both directions, so Mars→Earth and Earth→Mars give identical numbers for a pair. */
  function pairDistance(earthV,marsV,features,w,weightAll){
    let total=0,used=0;const parts=[];
    for(let i=0;i<features.length;i++){
      if(w[i]<=0 || marsV[i]===null || earthV[i]===null || earthV[i]===undefined)continue;
      const e=num(earthV[i]),m=num(marsV[i]);if(e===null||m===null)continue;
      const f=features[i],s=featureScale(f);
      if(f.compare!=='same' && !(s>0))continue;
      const d=f.compare==='same'?(e===m?0:1):Math.min(1,Math.abs(e-m)/s);
      used+=w[i];total+=w[i]*d;parts.push({name:f.name,d,weighted:w[i]*d});
    }
    return used?{distance:total/used,coverage:used/weightAll,parts}:null;
  }
  function verdict(pair,band,library){
    const steepness=Number(band.steepness)||.1;
    const match_pct=Math.round(1000*Math.exp(-pair.distance/steepness))/10;
    const levels=library.levels||{};
    const level=pair.coverage<Number(library.min_coverage??.5)?'insufficient_data':match_pct>=Number(levels.excellent??85)?'excellent':match_pct>=Number(levels.good??70)?'good':match_pct>=Number(levels.fair??50)?'fair':match_pct>=Number(levels.poor??30)?'poor':'no_match';
    const best=pair.parts.filter(p=>p.d<.5).sort((a,b)=>a.d-b.d).slice(0,3).map(p=>p.name);
    const worst=pair.parts.filter(p=>p.d>0).sort((a,b)=>b.weighted-a.weighted).slice(0,3).map(p=>p.name);
    return {match_pct,level,best,worst};
  }
  function scorePlaces(mars,band,library,weights){
    if(!band?.features?.length || !Array.isArray(band.places))throw new Error('Invalid Earth library data');
    if(!mars || typeof mars!=='object')throw new Error('Mars features are unavailable for this cell');
    const features=band.features;
    const values=features.map(f=>normalized(mars,f));
    const {w,all}=weightsFor(band,weights);
    const matches=[];
    for(const place of band.places){
      if(!Array.isArray(place.v)||!Number.isFinite(Number(place.lat))||!Number.isFinite(Number(place.lon)))continue;
      const pair=pairDistance(place.v,values,features,w,all);
      if(!pair)continue;
      matches.push({region_id:place.id,name:place.name,new_site:!!place.new_site,lat:Number(place.lat),lon:Number(place.lon),...verdict(pair,band,library),confidence:'not recalculated',_dist:pair.distance});
    }
    return matches.sort((a,b)=>a._dist-b._dist);
  }

  /* ---------- reverse direction: one Earth place → every Mars cell ---------- */
  const marsCache=new WeakMap();   // grid object → {feature key → normalized rows}
  function marsRows(grid,band){
    let byKey=marsCache.get(grid);if(!byKey){byKey=new Map();marsCache.set(grid,byKey);}
    const key=band.features.map(f=>`${f.name}:${f.compare}`).join('|');
    if(!byKey.has(key))byKey.set(key,(grid.cells||[]).map(c=>band.features.map(f=>normalized(c.mars,f))));
    return byKey.get(key);
  }
  /* Returns {top: best Mars cells (at least spacingKm apart on Mars), pct: Float32Array of match % per grid cell (NaN = not comparable)}. */
  function scoreMars(place,band,library,grid,weights,{topN=5,spacingKm=300}={}){
    if(!band?.features?.length)throw new Error('Invalid Earth library data');
    if(!place || !Array.isArray(place.v))throw new Error('This Earth place has no values for this depth');
    const cells=grid?.cells||[], rows=marsRows(grid,band);
    const {w,all}=weightsFor(band,weights);
    const pct=new Float32Array(cells.length).fill(NaN), order=[];
    const steepness=Number(band.steepness)||.1, minCov=Number(library.min_coverage??.5);
    for(let i=0;i<cells.length;i++){
      const pair=pairDistance(place.v,rows[i],band.features,w,all);
      if(!pair || pair.coverage<minCov)continue;
      pct[i]=100*Math.exp(-pair.distance/steepness);
      order.push([pair.distance,i,pair]);
    }
    order.sort((a,b)=>a[0]-b[0]);
    const top=[];
    for(const [,i,pair] of order){
      const c=cells[i],pt={lat:Number(c.lat),lon:Number(c.lon)};
      if(top.some(t=>greatCircle(t,pt,MARS_R_KM)<spacingKm))continue;
      top.push({cell_id:c.cell_id,lat:pt.lat,lon:pt.lon,mars:c.mars,index:i,...verdict(pair,band,library)});
      if(top.length>=topN)break;
    }
    return {top,pct};
  }
  window.TwinRescore={score,scorePlaces,scoreMars,normalized,percentile,greatCircle,MARS_R_KM};
  function score(mars,band,library,weights,topN=3){
    const matches=scorePlaces(mars,band,library,weights);
    const chosen=[];
    for(const m of matches){if(chosen.every(c=>greatCircle(c,m)>=Number(library.spacing_km??200)))chosen.push(m);if(chosen.length>=topN)break;}
    return chosen.map(({_dist,...rest})=>rest);
  }
})();
