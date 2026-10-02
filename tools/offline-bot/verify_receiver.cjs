// Optional browser smoke test. Requires Playwright and an installed Chromium browser.
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('playwright');

async function main() {
  const directory = path.resolve(process.argv[2] || 'logs/offline-bot/nominal');
  const executablePath = process.argv[3];
  const browser = await chromium.launch({headless:true, ...(executablePath ? {executablePath} : {})});
  try {
    const page = await browser.newPage({viewport:{width:1200,height:900},acceptDownloads:true});
    const errors=[];
    page.on('pageerror',error=>errors.push(String(error)));
    await page.goto(require('node:url').pathToFileURL(path.join(directory,'receiver.html')).href);
    const surface=page.locator('#surface'), bounds=await surface.boundingBox();
    const x=bounds.x+bounds.width*1200/2560, y=bounds.y+bounds.height*600/1440;
    await page.mouse.click(x,y);
    await page.keyboard.press('PageUp');
    await page.keyboard.press('PageDown');
    await page.keyboard.press('a');
    await page.mouse.click(x,y,{button:'right'});
    await page.keyboard.down('Shift');
    await page.locator('#export').scrollIntoViewIfNeeded();
    const download=page.waitForEvent('download');
    await page.locator('#export').click();
    await page.keyboard.up('Shift');
    await (await download).saveAs(path.join(directory,'browser-received-events.json'));
    const trace=JSON.parse(await fs.readFile(path.join(directory,'browser-received-events.json'),'utf8'));
    const assert=(condition,message)=>{if(!condition)throw new Error(message);};
    const click=trace.events.find(e=>e.kind==='mouse_click'&&e.button===0);
    assert(click&&Math.abs(click.x-1200)<=1&&Math.abs(click.y-600)<=1,'Frame coordinates mismatch');
    assert(trace.events.some(e=>e.kind==='mouse_click'&&e.button===2),'Right click missing');
    for(const key of ['PageUp','PageDown','a'])for(const kind of ['key_down','key_up'])
      assert(trace.events.some(e=>e.kind===kind&&e.key===key),`${kind} ${key} missing`);
    assert(trace.events.some(e=>e.kind==='focus_lost'&&e.previously_held.includes('ShiftLeft')&&e.release_observed===false),'Focus loss did not preserve unobserved release evidence');
    assert(trace.events.filter(e=>e.kind!=='focus_lost').every(e=>e.trusted),'Browser did not receive trusted input events');
    await page.screenshot({path:path.join(directory,'receiver-live-preview.png')});
    await surface.scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(directory,'receiver-preview.png')});
    await page.locator('#clear').click();
    assert(await page.locator('#live-events').textContent()==='','Clear failed');
    assert(await surface.locator('.live-click').count()===0,'Clear retained click markers');
    await surface.click();
    for(let i=0;i<130;i++)await page.keyboard.press('a');
    const limited=JSON.parse(await page.locator('#live-events').textContent());
    assert(limited.length===256,'Event limit failed');
    assert((await page.locator('#live-status').textContent()).includes('Event limit reached'),'Limit status missing');
    assert(errors.length===0,'Browser script errors: '+errors.join('; '));
    const report={passed:true,received_events:trace.events.length,coordinate_space:trace.coordinate_space,
      trusted_input:true,verified:['left/right click coordinates','key down/up','focus loss','JSON export','clear','256 event bound'],
      boundary:'Browser-delivered input only; does not verify native Windows dispatch or BSGO input.'};
    await fs.writeFile(path.join(directory,'browser-verification.json'),JSON.stringify(report,null,2));
    process.stdout.write(JSON.stringify(report,null,2)+'\n');
  } finally {await browser.close();}
}
main().catch(error=>{process.stderr.write(String(error)+'\n');process.exitCode=1;});
