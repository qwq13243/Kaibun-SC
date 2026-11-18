// Render local Spine fixtures for the expanded review. No provider requests.
const fs=require('node:fs/promises'),path=require('node:path'),http=require('node:http');
const root=path.resolve(__dirname,'../..'),out=path.resolve(process.argv[2]);
const {chromium}=require(path.join(root,'frontend/node_modules/playwright'));
const ts=require(path.join(root,'frontend/node_modules/typescript'));
const roots={'/src/':path.join(root,'frontend/src'),'/vendor/':path.join(root,'frontend/public/vendor'),'/assets/':path.join(root,'assets')};
const html=`<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:white}canvas{display:block;width:360px;height:540px}</style><script type="importmap">{"imports":{"@/":"/src/"}}</script><script src="/vendor/spine-3.6/spine-webgl.js"></script><canvas></canvas><script type="module">
import {StandEngine} from '/src/components/conversations/stand/StandEngine';let engine;
window.loadCharacter=async outfit=>{engine?.dispose();engine=new StandEngine(document.querySelector('canvas'),window.spine,'full');engine.setPaused(true);await engine.load({id:outfit,url:'/assets/spine/idols/'+outfit+'/'});return true;};
window.snapshot=(action)=>{const p=engine.pose,s=p.state;s.clearTracks();p.skeleton.setToSetupPose();
const faceOnly=action.kind==='expression';const overlay=faceOnly||/^(yes|no)\d*$/.test(action.name);if(overlay){const idle=s.setAnimation(0,'wait',true);idle.trackTime=1;}
const entry=s.setAnimation(faceOnly?1:overlay?2:0,action.name,false);entry.trackTime=action.sample_time;entry.mixDuration=0;
if(p.animations.has(action.neutral_face)){const face=s.setAnimation(1,action.neutral_face,true);face.mixDuration=0;}
if(!faceOnly&&p.animations.has('lip_wait')){const mouth=s.setAnimation(5,'lip_wait',false);mouth.animationEnd=0;mouth.mixDuration=0;}
engine.draw(p,0);return true;};window.ready=true;
</script>`;
const server=http.createServer(async(req,res)=>{try{
 const url=decodeURIComponent(new URL(req.url,'http://localhost').pathname);
 if(url==='/'){res.setHeader('Content-Type','text/html');return res.end(html);}
 const prefix=Object.keys(roots).find(p=>url.startsWith(p));if(!prefix){res.writeHead(404);return res.end();}
 let file=path.resolve(roots[prefix],url.slice(prefix.length));if(!file.startsWith(path.resolve(roots[prefix])+path.sep)){res.writeHead(403);return res.end();}
 if(prefix==='/src/'){if(!file.endsWith('.ts'))file+='.ts';res.setHeader('Content-Type','text/javascript');return res.end(ts.transpileModule(await fs.readFile(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText);}
 res.setHeader('Content-Type',({'.js':'text/javascript','.json':'application/json','.atlas':'text/plain','.png':'image/png'})[path.extname(file)]||'application/octet-stream');res.end(await fs.readFile(file));
}catch(error){res.writeHead(404);res.end();}});
(async()=>{
 await fs.mkdir(out,{recursive:true});
 const data=JSON.parse(await fs.readFile(path.join(out,'motion-annotations-all.json'),'utf8'));
 const inventory=JSON.parse(await fs.readFile(path.join(out,'stand-catalog.json'),'utf8'));
 const manifest=[];
 for(const c of data.characters){
  const faces=inventory.characters.find(row=>row.character_id===c.character_id).animations.filter(a=>a.kind==='expression');
  const actions=[...c.actions,...faces.map(a=>({...a,id:c.character_id+'/'+a.name,image:'images/'+c.character_id+'/'+a.name+'.png',neutral_face:null,sample_time:0,kind:'expression'}))];
  for(const a of c.actions){const target=path.join(out,a.image),source=path.join(path.dirname(out),a.image);
   await fs.mkdir(path.dirname(target),{recursive:true});
   if(a.observation_status.startsWith('existing-'))try{await fs.copyFile(source,target);}catch{}
  }
  manifest.push({id:c.character_id,name:c.character_name,outfit:c.outfit,actions});
 }
 await fs.writeFile(path.join(out,'render-manifest.json'),JSON.stringify(manifest,null,2));
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));let browser;
 try{
  let last;for(const channel of ['msedge',undefined])try{browser=await chromium.launch({...(channel?{channel}:{}),headless:true,args:['--enable-unsafe-swiftshader','--use-angle=swiftshader']});break;}catch(error){last=error;}
  if(!browser)throw last;
  let next=0,completed=0;
  await Promise.all(Array.from({length:3},async()=>{
   const page=await browser.newPage({viewport:{width:360,height:540},deviceScaleFactor:1});page.setDefaultTimeout(45000);
   await page.goto('http://127.0.0.1:'+server.address().port);await page.waitForFunction(()=>window.ready);
   while(next<manifest.length){const char=manifest[next++];await fs.mkdir(path.join(out,'images',char.id),{recursive:true});await page.evaluate(outfit=>window.loadCharacter(outfit),char.outfit);
    for(const action of char.actions){const target=path.join(out,action.image);try{if((await fs.stat(target)).size>1000)continue;}catch{}
     await page.evaluate(action=>window.snapshot(action),action);await page.locator('canvas').screenshot({path:target,type:'png'});
    }
    completed++;console.log(JSON.stringify({event:'rendered',character:char.id,actions:char.actions.length,completed,total:manifest.length}));
   }
   await page.close();
  }));
 }finally{await browser?.close();server.close();}
 console.log('RENDER COMPLETE');
})().catch(error=>{console.error(error.stack);server.close();process.exitCode=1;});
