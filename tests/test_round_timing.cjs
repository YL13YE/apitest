const assert = require('node:assert/strict');
const {roundTiming} = require('../apitest/static/overview.js');
const row=(model,start,total,index=0,extra={})=>({target:'relay',model,index,started_at:start,total,ok:true,attempt:1,...extra});
const first=[row('a',1000,3),row('b',1003,7)];

let time=roundTiming({results:first,targetCount:2,now:2000000});
assert.equal(time.seconds,10);
assert.equal(time.active,false);
assert.equal(time.waiting,true);
assert.equal(roundTiming({results:first,targetCount:2,now:4000000}).seconds,10);

time=roundTiming({results:first,targetCount:2,progress:{index:1,startedAt:4600000},now:4604000});
assert.equal(time.round,2);
assert.equal(time.seconds,4);
assert.equal(time.active,true);

time=roundTiming({results:first,targetCount:2,pending:{index:1,startedAt:4600000},phase:'sampling',now:4607000});
assert.equal(time.round,2);
assert.equal(time.seconds,7);

time=roundTiming({results:[...first,row('a',4600,2,1)],targetCount:2,pending:{index:1,startedAt:4500000},now:4605000});
assert.equal(time.seconds,5); // Actual request timestamps replace the initial scheduling hint.

time=roundTiming({results:[row('a',1000,2,0,{ok:false,status_code:503})],targetCount:1,retries:1,now:1003000});
assert.equal(time.active,true);
assert.equal(time.seconds,3);
time=roundTiming({results:[row('a',1000,2,0,{ok:false,status_code:503}),row('a',1004,3,0,{attempt:2})],targetCount:1,retries:1,now:2000000});
assert.equal(time.seconds,7); // Includes retry delay within this round.
assert.equal(time.active,false);

assert.equal(roundTiming({results:[row('a',1000,5),row('b',1001,10)],targetCount:2,now:2000000}).seconds,11);
assert.equal(roundTiming({results:[row('a',1,1000,-1,{warmup:true}),...first],targetCount:2,now:2000000}).seconds,10);

time=roundTiming({results:[row('a',1000,3)],targetCount:24,phase:'waiting',phaseObservedAt:1010000,evidenceAt:1003000,now:2000000});
assert.equal(time.seconds,3);
assert.equal(time.waiting,true);
time=roundTiming({results:first,targetCount:2,phase:'waiting',phaseObservedAt:4000000,evidenceAt:4603000,progress:{index:1,startedAt:4600000},now:4604000});
assert.equal(time.active,true); // New round evidence wins over the last idle-state poll.
assert.equal(time.seconds,4);

assert.equal(roundTiming({results:[row('a',1000,3)],targetCount:24,running:false,now:2000000}).seconds,3);
assert.equal(roundTiming({results:[],phase:'waiting',now:2000000}).seconds,0);
assert.equal(roundTiming({results:[row('a',NaN,NaN)],targetCount:1,now:2000000}).seconds,0);
console.log('Round timing checks passed: idle freeze, next round reset, refresh, retries, warmups, parallel wall time, and partial stops.');
