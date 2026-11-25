import test from 'node:test';
import assert from 'node:assert/strict';
import {monthCells,dueFor,dateLabel} from './schedule.js';
test('calendar has fixed seven columns including leap day',()=>{
 for(const [year,month,total] of [[2028,2,29],[2026,11,30],[2026,8,31]]){
  const cells=monthCells(year,month);assert.equal(cells.length%7,0);
  assert.equal(cells.filter(x=>x!==null).length,total);
  assert.equal(cells.indexOf(1),new Date(Date.UTC(year,month-1,1)).getUTCDay());
 }
});
test('all sixty minutes map to correct Japan timestamps without ranges',()=>{
 for(let minute=0;minute<60;minute++){
  const selected={year:2028,month:2,day:29,hour:0,minute};
  assert.equal(new Date(dueFor(selected)*1000).toISOString(),`2028-02-28T15:${String(minute).padStart(2,'0')}:00.000Z`);
  assert.ok(dateLabel(selected).includes(`00:${String(minute).padStart(2,'0')}`));
 }
});
