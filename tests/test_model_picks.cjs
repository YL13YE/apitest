const assert = require('node:assert/strict');
const picks = require('../apitest/static/overview.js');

function item(model, effort, first, speed, changes={}) {
  return {id:model+'-'+effort,model,effort,metrics:{n:1,complete:1,complete_rate:1,ttfa_p50:first,tps_p50:speed,total_p50:null,...changes}};
}

const data=[item('a','low',.8,100),item('a','high',2,200),item('b','low',1,150),item('c','medium',.5,50),item('unreliable','low',.01,1000,{complete_rate:.5})];
const original=JSON.stringify(data);
const selected=picks.choose(data);
assert.deepEqual(selected.map(x=>x.variant.model),['b','a','c']);
assert.equal(selected.find(x=>x.variant.model==='a').variant.effort,'low');
assert.equal(selected[0].basis,'answer100');
assert.ok(Math.abs(selected[0].value-(1+100/150))<1e-12);
assert.equal(JSON.stringify(data),original);

assert.deepEqual(picks.choose([item('bad','low',.1,100,{complete_rate:.2})]),[]);
assert.deepEqual(picks.choose([item('missing','low',NaN,Infinity)]),[]);
assert.deepEqual(picks.choose([]),[]);

const mixed=picks.choose([item('measured','low',2,50),item('only-first','low',.001,null)]);
assert.deepEqual(mixed.map(x=>x.variant.model),['measured']);
const firstOnly=picks.choose([item('b','low',2,null),item('a','low',1,null)]);
assert.equal(firstOnly[0].variant.model,'a');
assert.equal(firstOnly[0].basis,'first');
const totalOnly=picks.choose([item('b','low',null,null,{total_p50:5}),item('a','high',null,null,{total_p50:3})]);
assert.equal(totalOnly[0].variant.model,'a');
assert.equal(totalOnly[0].basis,'total');
assert.deepEqual(picks.fastestStarts(data).map(x=>x.model).filter(x=>x==='a'),['a']);
assert.equal(picks.rank(data).at(-1).model,'unreliable');
console.log('Recommendation checks passed: distinct models, fastest effort, reliability, missing metrics, fallback modes, immutable data.');
