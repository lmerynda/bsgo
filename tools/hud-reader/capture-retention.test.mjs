import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { runCaptureLoop } from './capture-loop.mjs';

const root=path.dirname(fileURLToPath(import.meta.url));
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));

test('retains original screenshots and links an OCR timeout to its current image',async()=>{
  const directory=path.join(root,'runs','mock-tests','retention-'+Date.now());
  const original=await fs.readFile(path.join(root,'fixtures','live','live-01.png'));
  let worker;
  const sky={get_window_state:async()=>{
    worker??=(async()=>{
      for(let attempt=0;attempt<100;attempt++){
        const files=await fs.readdir(directory);
        const first=files.find(name=>/^request-\d+-0\.json$/.test(name));
        if(first){
          const request=JSON.parse(await fs.readFile(path.join(directory,first),'utf8'));
          const response=path.join(directory,first.replace('request-','response-'));
          await fs.writeFile(response+'.tmp',JSON.stringify({id:request.id,run_id:request.run_id,
            valid:true,reasons:[],distance:1416,speed:0,
            captured_at_utc:new Date(request.captured_at*1000).toISOString()}));
          await fs.rename(response+'.tmp',response);
          return;
        }
        await pause(10);
      }
      throw Error('Mock worker did not receive first request');
    })();
    return {window:{id:1},screenshots:[{id:'screenshot-0',url:'data:image/png;base64,'+original.toString('base64')}]};
  }};
  await assert.rejects(runCaptureLoop({sky,window:{id:1},outputDirectory:directory,
    count:2,intervalMs:100,maxDurationMs:5000}),/OCR worker response exceeded/);
  await worker;
  const files=await fs.readdir(directory);
  const images=files.filter(name=>name.startsWith('frame-')).sort();
  assert.equal(images.length,2);
  for(const name of images)assert.deepEqual(await fs.readFile(path.join(directory,name)),original);
  assert.equal(files.filter(name=>/^request-.*\.json$/.test(name)).length,2);
  assert.equal(files.filter(name=>/^response-.*\.json$/.test(name)).length,1);
  const records=(await fs.readFile(path.join(directory,files.find(name=>name.startsWith('capture-'))),'utf8'))
    .trim().split('\n').map(line=>JSON.parse(line));
  assert.equal(records[0].frame_path,path.join(directory,images[0]));
  assert.equal(records.at(-1).failed_capture.frame_path,path.join(directory,images[1]));
  assert.equal(records.at(-1).failed_capture.id,1);
  assert.equal(records.at(-1).failed_observation,null);
});

test('injected test observations are distinguishable from real capture inputs',async()=>{
  const directory=path.join(root,'runs','mock-tests','provenance-'+Date.now());
  const result=await runCaptureLoop({outputDirectory:directory,count:1,
    observer:async()=>({state:{screenshots:[{id:'injected'}]},row:{valid:true,reasons:[]}})});
  assert.equal(result.rows[0].source,'injected_observation');
  assert.equal(result.rows[0].frame_path,undefined);
});
