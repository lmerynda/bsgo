// Exercises the actual inline image viewer without a browser or native game input.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const scripts=[...fs.readFileSync(process.argv[2],'utf8').matchAll(/<script>([\s\S]*?)<\/script>/g)];
assert.equal(scripts.length,2);
let now=0,nextTimer=0,rows=[],pendingImages=[],intervals=[];
const timers=new Map(),elements={};
class Element {
  constructor(id){this.id=id;this.hidden=false;this.textContent='';this.className='';this.classes=new Set();this.classList={toggle:(name,on)=>on?this.classes.add(name):this.classes.delete(name)};}
  removeAttribute(name){delete this[name];}
  replaceWith(other){elements[this.id]=other;}
}
for(const id of ['livecapture','captureplaceholder','capturestatus','capturedetail']) elements[id]=new Element(id);
class Img extends Element {constructor(){super('');pendingImages.push(this);}}
const identity='a'.repeat(32);
const frame=(version=1)=>({available:true,identity,version,image_url:`/api/live-image?identity=${identity}&version=${version}`,status:'current',fresh:true,connected:true,age_seconds:.1,acquisition_sequence:version,frame_size:[2560,1440],observation_healthy:false});
rows.push(frame());
vm.runInNewContext(scripts.at(-1)[1],{
  document:{getElementById:id=>elements[id]},Image:Img,performance:{now:()=>now},AbortController,
  setTimeout:(callback,ms)=>{const id=++nextTimer;timers.set(id,{callback,ms});return id;},clearTimeout:id=>timers.delete(id),setInterval:fn=>intervals.push(fn),
  fetch:async()=>{const row=rows.shift();if(row instanceof Error)throw row;assert.ok(row,'unexpected request');return{ok:true,json:async()=>row};}
});
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
async function poll(row){rows.push(row);const entry=[...timers].find(([_,timer])=>timer.ms===1000);assert.ok(entry);timers.delete(entry[0]);entry[1].callback();await flush();}
(async()=>{
  await flush();assert.equal(pendingImages.length,1);assert.equal(elements.livecapture.hidden,true);
  pendingImages[0].onload();assert.equal(elements.livecapture,pendingImages[0]);assert.equal(elements.livecapture.hidden,false);
  assert.match(elements.capturestatus.textContent,/^CURRENT/);assert.match(elements.capturedetail.textContent,/HUD reading needs attention/);
  // Fresh pixels remain visible even if OCR reports a partial image.
  now=2200;intervals[0]();assert.match(elements.capturestatus.textContent,/^LAST KNOWN/);
  await poll(frame(2));const late=pendingImages.at(-1);assert.equal(elements.livecapture.hidden,true);
  await poll(frame(3));const latest=pendingImages.at(-1);late.onload();assert.equal(elements.livecapture.hidden,true);
  latest.onload();assert.equal(elements.livecapture,latest);assert.equal(elements.livecapture.hidden,false);
  await poll(new Error('service unavailable'));assert.equal(elements.livecapture.hidden,false);assert.match(elements.capturestatus.textContent,/^LAST KNOWN/);
  await poll({...frame(3),connected:false});assert.match(elements.capturestatus.textContent,/^LAST KNOWN/);
  await poll({...frame(3),age_seconds:-1});assert.match(elements.capturestatus.textContent,/^LAST KNOWN/);
  await poll({...frame(4),image_url:'https://wrong.example/frame.jpg'});assert.equal(elements.livecapture.hidden,true);
  // A mismatched URL cannot smuggle pixels from a different identity/version.
  await poll({...frame(4),image_url:`/api/live-image?identity=${identity}&version=3`});assert.equal(elements.livecapture.hidden,true);
  const otherIdentity='b'.repeat(32);
  await poll({...frame(1),identity:otherIdentity,image_url:`/api/live-image?identity=${otherIdentity}&version=1`});
  const oldSession=pendingImages.at(-1);
  await poll({...frame(2),identity:otherIdentity,image_url:`/api/live-image?identity=${otherIdentity}&version=2`});
  oldSession.onload();assert.equal(elements.livecapture.hidden,true);
  pendingImages.at(-1).onload();assert.equal(elements.livecapture.hidden,false);
  await poll({available:false,status:'waiting',reason:'waiting_for_game'});assert.equal(elements.livecapture.hidden,true);assert.equal(elements.captureplaceholder.hidden,false);
  assert.equal(elements.capturestatus.textContent,'Waiting for capture');
  console.log('Viewer checks passed: decoded image reuse, fresh partial OCR, age expiry, version/session race, status failure, URL fencing, waiting state.');
})().catch(error=>{console.error(error);process.exitCode=1;});
