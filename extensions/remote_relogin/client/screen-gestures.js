const clamp=(value,min,max)=>Math.max(min,Math.min(max,value));
const distance=(a,b)=>Math.hypot(a.x-b.x,a.y-b.y);
const center=(a,b)=>({x:(a.x+b.x)/2,y:(a.y+b.y)/2});

// Dragging always moves the local view. Only a completed tap clicks the PC.
export class ScreenGestures {
 constructor({screen,viewport,send,ready,changed=()=>{}}) {
  Object.assign(this,{screen,viewport,send,ready,changed});
  this.pointers=new Map();this.zoom=1;this.suppressed=false;
  for(const name of ['pointerdown','pointermove','pointerup','pointercancel','lostpointercapture'])
   screen.addEventListener(name,e=>this[name](e));
 }
 point(position){const r=this.screen.getBoundingClientRect();return{x:clamp((position.x-r.left)/r.width,0,1),y:clamp((position.y-r.top)/r.height,0,1)}}
 command(phase,position){return this.send({action:'pointer',...this.point(position),phase})}
 reset(){this.pointers.clear();this.single=null;this.pinch=null;this.suppressed=false;if(this.ready())this.send({action:'release'})}
 setZoom(value,anchor){
  const v=this.viewport,r=v.getBoundingClientRect();
  const p=anchor||{x:r.left+v.clientWidth/2,y:r.top+v.clientHeight/2};
  const x=p.x-r.left,y=p.y-r.top,old=this.zoom;
  const contentX=(v.scrollLeft+x)/old,contentY=(v.scrollTop+y)/old;
  this.zoom=clamp(value,1,5);
  this.screen.style.width=this.zoom===1?'100%':`${v.clientWidth*this.zoom}px`;
  v.scrollLeft=contentX*this.zoom-x;v.scrollTop=contentY*this.zoom-y;
  this.changed(this.zoom);
 }
 beginPinch(){
  this.single=null;this.suppressed=true;
  const [a,b]=[...this.pointers.values()],mid=center(a,b),r=this.viewport.getBoundingClientRect();
  this.pinch={distance:Math.max(1,distance(a,b)),zoom:this.zoom,
   contentX:(this.viewport.scrollLeft+mid.x-r.left)/this.zoom,
   contentY:(this.viewport.scrollTop+mid.y-r.top)/this.zoom};
 }
 pointerdown(e){
  if(!this.ready()||e.button!==0)return;
  e.preventDefault();const p={x:e.clientX,y:e.clientY};
  this.pointers.set(e.pointerId,p);this.screen.setPointerCapture(e.pointerId);
  if(this.pointers.size===2){this.beginPinch();return}
  if(this.pointers.size>2){this.single=null;this.pinch=null;this.suppressed=true;return}
  if(this.suppressed)return;
  this.single={id:e.pointerId,start:p,moved:false,left:this.viewport.scrollLeft,top:this.viewport.scrollTop};
 }
 pointermove(e){
  if(!this.pointers.has(e.pointerId))return;
  e.preventDefault();const p={x:e.clientX,y:e.clientY};this.pointers.set(e.pointerId,p);
  if(this.pinch&&this.pointers.size===2){
   const [a,b]=[...this.pointers.values()],mid=center(a,b),r=this.viewport.getBoundingClientRect();
   const zoom=this.pinch.zoom*distance(a,b)/this.pinch.distance;
   this.setZoom(zoom,mid);
   this.viewport.scrollLeft=this.pinch.contentX*this.zoom-(mid.x-r.left);
   this.viewport.scrollTop=this.pinch.contentY*this.zoom-(mid.y-r.top);return;
  }
  if(this.suppressed||!this.single||this.single.id!==e.pointerId)return;
  if(distance(p,this.single.start)>7)this.single.moved=true;
  if(this.single.moved){
   this.viewport.scrollLeft=this.single.left-(p.x-this.single.start.x);
   this.viewport.scrollTop=this.single.top-(p.y-this.single.start.y);return;
  }
 }
 pointerup(e){
  if(!this.pointers.has(e.pointerId))return;
  e.preventDefault();this.pointers.delete(e.pointerId);
  if(this.single?.id===e.pointerId&&!this.suppressed&&!this.single.moved&&
    distance({x:e.clientX,y:e.clientY},this.single.start)<=7&&this.ready()){
   const p={x:e.clientX,y:e.clientY};
   // Calculate both positions before any async RPC can update the frame.
   this.command('down',p);this.command('up',p);
  }
  this.single=null;
  if(this.pointers.size===0){this.pinch=null;this.suppressed=false}
 }
 pointercancel(e){if(this.pointers.has(e.pointerId)){e.preventDefault();this.reset()}}
 lostpointercapture(e){if(this.pointers.has(e.pointerId))this.reset()}
}
