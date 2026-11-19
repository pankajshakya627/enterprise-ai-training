/* Run with NODE_PATH pointing to a temporary install of jsdom and axe-core. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {JSDOM, requestInterceptor, VirtualConsole} = require('jsdom');
const axe = require('axe-core');
const root = path.resolve(process.argv[2] || (fs.existsSync(path.join(__dirname,'public')) ? path.join(__dirname,'public') : __dirname));
const report = {routes:[],diagramSelections:0,workflowStages:0,links:0,accessibility:[],notVerified:['Real browser layout at 320, 768, 1024 and 1440 pixels','Real keyboard traversal and rendered color contrast','Actual browser download behavior','Render deployment and live service readiness']};
const windows = [];
const errors = [];
const external = [];
const virtualConsole = new VirtualConsole();
virtualConsole.on('jsdomError',e=>errors.push(e.message));
const localResources = {interceptors:[requestInterceptor(request=>{
  if(!request.url.startsWith('file:')){external.push(request.url);return new Response('',{status:403});}
})]};
const wait = ms => new Promise(resolve=>setTimeout(resolve,ms));
let objectBlob;
let downloadName;
async function load(file, scripts) {
  const dom = await JSDOM.fromFile(path.join(root,file), {runScripts:'dangerously',resources:localResources,pretendToBeVisual:true,virtualConsole,
    beforeParse(window) {
      window.scrollTo=()=>{};
      window.HTMLElement.prototype.scrollIntoView=()=>{};
      window.URL.createObjectURL=blob=>{objectBlob=blob;return 'blob:guide-test';};
      window.URL.revokeObjectURL=()=>{};
      const original=window.HTMLAnchorElement.prototype.click;
      window.HTMLAnchorElement.prototype.click=function(){if(this.download){downloadName=this.download;}else{original.call(this);}};
    }});
  windows.push(dom.window);
  if(dom.window.document.readyState!=='complete') await new Promise(resolve=>dom.window.addEventListener('load',resolve,{once:true}));
  await wait(10);
  if(scripts) assert.equal(dom.window.document.querySelectorAll('#page-title').length,1);
  return dom;
}
function checkDocument(document,file) {
  assert.equal(document.documentElement.lang,'en');
  assert.equal(document.querySelectorAll('h1').length,1,file+' must have one h1');
  let last=0;
  for(const h of document.querySelectorAll('h1,h2,h3,h4,h5,h6')){const n=Number(h.tagName[1]);assert(n<=last+1,file+' heading hierarchy');last=n;}
  const ids=[...document.querySelectorAll('[id]')].map(e=>e.id);
  assert.equal(new Set(ids).size,ids.length,file+' duplicate IDs');
  for(const img of document.images) assert(img.alt.trim(),'Images require alternative text');
  for(const a of document.querySelectorAll('a[href]')) {
    const href=a.getAttribute('href');
    assert(!href.startsWith('javascript:'));
    if(href.startsWith('#')) {
      if(file==='blog.html') assert(document.getElementById(href.slice(1)),href);
      else assert(href==='#content'||/^#(overview|architecture|workflows|operations|limits)(\/|$)/.test(href),href);
    } else if(!/^https?:/.test(href)) {
      const target=path.resolve(root,href.split('#')[0]);
      assert(target.startsWith(root+path.sep),'Link escapes the published site: '+href);
      assert(fs.statSync(target).isFile(),href);
    }
    report.links++;
  }
}
async function accessibility(window,label) {
  window.eval(axe.source);
  const result=await window.axe.run(window.document);
  report.accessibility.push({page:label,violations:result.violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.length})),incomplete:result.incomplete.map(v=>v.id),passes:result.passes.length});
  assert.equal(result.violations.length,0,label+' DOM accessibility violations: '+result.violations.map(v=>v.id).join(', '));
}
async function main() {
  const dom=await load('index.html',true);
  const {window}=dom;
  const doc=window.document;
  async function route(hash) {window.location.hash=hash;await wait(10);assert.equal(doc.querySelectorAll('h1').length,1);}
  for(const name of ['overview','architecture','workflows','operations','limits']) {
    await route('#'+name);
    checkDocument(doc,'index.html');
    report.routes.push(name);
    assert.equal(doc.querySelectorAll('.side-nav [aria-current="page"]').length,1);
  }
  await route('#architecture');
  assert.equal(doc.querySelectorAll('#map-list a').length,22);
  const data=window.NEXA_GUIDE_DATA;
  for(const d of data) {
    await route('#architecture/'+d.slug);
    assert.equal(doc.querySelector('#map-title').textContent,d.title);
    const img=doc.querySelector('#diagram-image');
    assert(fs.existsSync(path.join(root,img.getAttribute('src'))));
    assert.equal(doc.querySelector('.details-row code').textContent,d.code);
    report.diagramSelections++;
  }
  const search=doc.querySelector('#diagram-search');
  search.value='no-matching-diagram';search.dispatchEvent(new window.Event('input'));
  assert(doc.querySelector('#map-list .empty'));
  search.value='';search.dispatchEvent(new window.Event('input'));
  const group=doc.querySelector('#diagram-group');group.value='overall';group.dispatchEvent(new window.Event('change'));
  assert.equal(doc.querySelectorAll('#map-list a').length,1);
  const zoom=doc.querySelector('#diagram-zoom');zoom.value='150';zoom.dispatchEvent(new window.Event('input'));
  assert.equal(doc.querySelector('#zoom-output').value,'150%');
  doc.querySelector('#export-mermaid').click();
  assert(objectBlob.size>0);assert.equal(downloadName,'22-overall.mmd');
  for(const name of ['policy','customer','case','note','recovery']) {
    await route('#workflows/'+name);
    const steps=[...doc.querySelectorAll('[data-step]')];
    for(let i=0;i<steps.length;i++) {
      doc.querySelector(`[data-step="${i}"]`).click();
      assert.equal(doc.querySelector('[aria-current="step"]').dataset.step,String(i));
      assert(doc.querySelector('#step-panel h2').textContent.trim());
      assert.equal(doc.querySelector('#previous-step').disabled,i===0);
      assert.equal(doc.querySelector('#next-step').disabled,i===steps.length-1);
      report.workflowStages++;
    }
    doc.querySelector('#previous-step').click();
    assert.equal(doc.querySelector('[aria-current="step"]').dataset.step,String(steps.length-2));
    doc.querySelector('#next-step').click();
    assert.equal(doc.querySelector('[aria-current="step"]').dataset.step,String(steps.length-1));
    checkDocument(doc,'index.html');
  }
  await route('#workflows/recovery/999');
  assert.equal(doc.querySelector('#next-step').disabled,true);
  await route('#not-a-route');
  assert(doc.querySelector('h1').textContent.includes('Understand the answer'));
  doc.querySelector('#skip').click();assert.equal(doc.activeElement.id,'content');
  for(const hash of ['#overview','#architecture/22-overall','#workflows/case','#operations','#limits']) {await route(hash);await accessibility(window,hash);}
  const blog=await load('blog.html',false);
  checkDocument(blog.window.document,'blog.html');
  await accessibility(blog.window,'blog.html');
  blog.window.close();
  const meta=JSON.parse(fs.readFileSync(path.join(root,'verification.json'),'utf8'));
  for(const d of meta.diagrams) {
    const digest=crypto.createHash('sha256').update(fs.readFileSync(path.join(root,d.svg))).digest('hex');
    assert.equal(digest,d.svg_sha256);
  }
  assert.deepEqual(external,[],'Site attempted external asset requests');
  const unsupported=[...new Set(errors.filter(e=>/Not implemented.*HTMLCanvasElement|HTMLCanvasElement.*not implemented/i.test(e)))];
  const actualErrors=errors.filter(e=>!unsupported.includes(e));
  assert.deepEqual(actualErrors,[],'Site console errors');
  report.jsdomLimitations=unsupported;
  report.externalAssetRequests=external;
  report.status='Passed DOM and source checks; browser verification remains pending';
  const reportPath=process.env.GUIDE_TEST_REPORT;
  if(reportPath)fs.writeFileSync(reportPath,JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify(report,null,2));
  window.close();
}
main().catch(error=>{for(const w of windows)w.close();console.error(error);process.exitCode=1;});
