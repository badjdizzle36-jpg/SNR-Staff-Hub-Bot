// No browser or npm dependencies required: exercise the shipped DOM handlers.
const {execFileSync}=require('child_process');
const vm=require('vm');
const assert=require('assert');
const source=execFileSync('python3',['-c','from city_dashboard import DASHBOARD_JS; print(DASHBOARD_JS)'],{cwd:require('path').resolve(__dirname,'..'),encoding:'utf8'});
let click,scheduled,focused=false,scrolled=false;
const collection={open:false,scrollIntoView:()=>scrolled=true,querySelector:()=>({focus:()=>focused=true})};
const clock={dataset:{cityCountdown:new Date(Date.now()+86400000).toISOString()},textContent:''};
const document={addEventListener:(_,fn)=>fn(),querySelectorAll:selector=>selector==='[data-city-collection]'?[{addEventListener:(_,fn)=>click=fn}]:selector==='[data-city-countdown]'?[clock]:[],getElementById:()=>collection};
vm.runInNewContext(source,{document,Date,Number,Math,setInterval:fn=>scheduled=fn});
assert(clock.textContent.includes('remaining'));
click();assert(collection.open&&focused&&scrolled);
clock.dataset.cityCountdown='2000-01-01T00:00:00Z';scheduled();
assert.equal(clock.textContent,'Season deadline reached');
console.log('Dashboard JavaScript checks passed: countdown, expiry, collection expansion and focus.');
