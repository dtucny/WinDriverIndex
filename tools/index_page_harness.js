const fs=require('fs');
const dash=JSON.parse(fs.readFileSync('/home/dtucny/git/WinDriverIndex/public/v1/dashboard.json','utf8'));
const els={};
const mkEl=id=>els[id]??(els[id]={id,innerHTML:'',textContent:'',hidden:true,style:{},setAttribute(){},addEventListener(){},querySelectorAll:()=>[],classList:{add(){},remove(){},toggle(){}}});
global.document={getElementById:mkEl,documentElement:{setAttribute(){},removeAttribute(){}},addEventListener(){},querySelectorAll:()=>[]};
global.matchMedia=()=>({matches:false,addEventListener(){}});
global.localStorage={getItem:()=>null,setItem(){}};
global.fetch=async()=>({json:async()=>dash});
(async()=>{
  try{ require('./index_page.extracted.js'); }
  catch(e){ console.error('SYNC ERROR:', e.message); process.exit(1); }
  setTimeout(()=>{
    const ch=els['chwrap'], note=els['chnote'], card=els['chcard'];
    console.log('chwrap hidden:', ch?ch.hidden:'(missing)');
    console.log('chnote:', note?note.textContent.slice(0,90):'');
    console.log('chcard rows:', card?(card.innerHTML.match(/<tr>/g)||[]).length:0);
    console.log('vgrid rendered:', els['vgrid']?(els['vgrid'].innerHTML.match(/ptlbl/g)||[]).length+' type sections':0);
    console.log('heatwrap rendered:', els['heatwrap']?els['heatwrap'].innerHTML.length>100:false);
    console.log('bios rendered:', els['biosbody']?els['biosbody'].innerHTML.length>10:('(no biosbody el used)'));
  },300);
})();
