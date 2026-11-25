import {test} from 'node:test';
import assert from 'node:assert/strict';
import {ScreenGestures} from './screen-gestures.js';

function fixture(){
 const sent=[];
 const viewport={clientWidth:400,clientHeight:300,scrollLeft:0,scrollTop:0,getBoundingClientRect:()=>({left:0,top:0})};
 const screen={style:{},addEventListener(){},setPointerCapture(){},getBoundingClientRect:()=>({left:-viewport.scrollLeft,top:-viewport.scrollTop,width:400*g.zoom,height:300*g.zoom})};
 const g=new ScreenGestures({screen,viewport,ready:()=>true,send:c=>{sent.push(c);return Promise.resolve()}});
 const event=(pointerId,x,y)=>({pointerId,clientX:x,clientY:y,button:0,preventDefault(){}});
 return {g,sent,viewport,down:(id,x,y)=>g.pointerdown(event(id,x,y)),move:(id,x,y)=>g.pointermove(event(id,x,y)),up:(id,x,y)=>g.pointerup(event(id,x,y)),cancel:id=>g.pointercancel(event(id,0,0)),lost:id=>g.lostpointercapture(event(id,0,0))};
}
test('a tap sends exactly one PC click, only after the finger lifts',()=>{
 const f=fixture();f.down(1,100,75);assert.equal(f.sent.length,0);f.up(1,100,75);
 assert.deepEqual(f.sent.map(c=>c.phase),['down','up']);assert.equal(f.sent[0].x,.25);assert.equal(f.sent[0].y,.25);
});
test('two fingers pinch around their anchor without clicking or dragging the PC',()=>{
 const f=fixture();f.down(1,100,100);f.down(2,200,100);f.move(2,300,100);
 assert.equal(f.g.zoom,2);assert.equal(f.sent.length,0);assert.equal(f.viewport.scrollLeft,100);
 f.up(2,300,100);f.move(1,110,110);f.up(1,110,110);assert.equal(f.sent.length,0);
 f.down(3,100,100);f.up(3,100,100);assert.equal(f.sent.length,2);
});
test('a single finger drag always moves the view and never sends PC mouse input',()=>{
 const f=fixture();f.g.setZoom(3);f.down(1,200,150);const left=f.viewport.scrollLeft,top=f.viewport.scrollTop;
 f.move(1,100,100);f.up(1,100,100);
 assert.equal(f.viewport.scrollLeft,left+100);assert.equal(f.viewport.scrollTop,top+50);assert.equal(f.sent.length,0);
});
test('dragging away and back to the start cannot become a click on release',()=>{
 const f=fixture();f.down(1,100,100);f.move(1,150,100);f.move(1,100,100);f.up(1,100,100);assert.equal(f.sent.length,0);
});
test('an end position far from the start is not a tap even when move events were dropped',()=>{
 const f=fixture();f.down(1,100,100);f.up(1,150,100);assert.equal(f.sent.length,0);
});
test('cancellation and lost capture release any PC button and allow the next gesture',()=>{
 for(const cancel of ['cancel','lost']){
  const f=fixture();f.down(1,100,100);f[cancel](1);assert.deepEqual(f.sent,[{action:'release'}]);assert.equal(f.g.pointers.size,0);
  f.down(2,50,50);f.up(2,50,50);assert.deepEqual(f.sent.slice(-2).map(c=>c.phase),['down','up']);
 }
});
test('three fingers do not leave behind a one finger click',()=>{
 const f=fixture();f.down(1,100,100);f.down(2,200,100);f.down(3,150,150);
 f.up(3,150,150);f.up(2,200,100);f.up(1,100,100);assert.equal(f.sent.length,0);
});
test('zoom limits and image-relative coordinates survive repeated view changes',()=>{
 const f=fixture();f.g.setZoom(20);assert.equal(f.g.zoom,5);f.g.setZoom(.2);assert.equal(f.g.zoom,1);
 f.g.setZoom(2,{x:0,y:0});f.down(1,200,150);f.up(1,200,150);assert.equal(f.sent[0].x,.25);
});
