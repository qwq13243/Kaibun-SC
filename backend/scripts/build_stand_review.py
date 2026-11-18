"""Build a self-contained review page from the expanded, locally rendered catalog."""
import argparse
import json
from pathlib import Path

PAGE='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>完整姿态与表情清单</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f5f7fb;color:#20304a;font:16px/1.7 system-ui,sans-serif}header,main{max-width:1450px;margin:auto;padding:24px}h1{margin:0;font-size:30px}p{margin:8px 0}header{position:sticky;top:0;background:#f5f7fbf5;z-index:1;border-bottom:1px solid #dae1ed}.controls{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}select,input,textarea,button{font:inherit;border:1px solid #cbd5e4;background:white;color:inherit;border-radius:9px;padding:8px 12px}input{min-width:220px}button{cursor:pointer}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(480px,1fr));gap:20px}.card{background:white;border:1px solid #d8dfeb;border-radius:18px;overflow:hidden}.content{display:grid;grid-template-columns:180px 1fr;min-height:410px}.pic{width:180px;height:370px;object-fit:contain;align-self:start;position:sticky;top:250px}.info{padding:20px;border-left:1px solid #e4e9f1}.info h2{font-size:21px;margin:0}.muted{color:#71809a;font-size:14px}.tag{display:inline-block;padding:3px 9px;margin:8px 5px 8px 0;background:#e9efff;border-radius:7px;font-size:14px}.fit{padding:10px 0;border-top:1px solid #e3e8f0}.fit b{display:block;color:#355a8a}footer{padding:15px;background:#f9faff;display:flex;gap:10px}textarea{width:100%;resize:vertical;min-height:62px}details{font-size:14px;margin-top:10px}#more{display:block;margin:24px auto}.empty{padding:30px}a{color:#355a8a}@media(max-width:620px){header{position:static}.grid{grid-template-columns:1fr}.content{grid-template-columns:130px 1fr}.pic{width:130px;height:300px;position:static}.info{padding:12px}header,main{padding:14px}h1{font-size:25px}}</style>
<header><h1>完整姿态与表情清单</h1><p id="summary"></p><p class="muted">沿用已有初步标注，不再安排 Terra 复审。无保持循环的动作已从清单排除（基础 wait 为整段循环）。原始类别单列最高置信。只审查保持姿态，不审查起手和收势。</p>
<div class="controls"><select id="character" aria-label="角色"></select><select id="kind" aria-label="类型"><option value="pose">保持姿态</option><option value="expression">独立表情</option><option value="all">全部</option></select><input id="search" aria-label="搜索" placeholder="搜索名称、描述或情绪"><button id="export">导出审查意见</button></div><p class="muted" id="count"></p><a href="motion-annotations-all.json">动作 JSON</a> · <a href="stand-catalog.json">完整原始清单</a> · <a href="upstream-verification.json">线上核对结果</a></header>
<main><div id="cards" class="grid"></div><button id="more">加载更多</button></main>
<script>const DATA=__DATA__,INVENTORY=__INVENTORY__;const $=id=>document.getElementById(id);const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const emotions={calm:'平静',happy:'开心',sad:'难过',angry:'生气',surprised:'惊讶',thinking:'思考',shy:'害羞'};const confidence={highest:'最高置信 · 原始数据',high:'高置信',medium:'中置信',low:'低置信'};let shown=12;let feedback={};try{feedback=JSON.parse(localStorage.getItem('kaibun-all-poses-review-v3')||'{}')}catch{};
const rows=DATA.characters.flatMap(c=>[...c.actions.filter(a=>a.sustained).map(a=>({...a,character:c.character_name,cid:c.character_id,outfit:c.outfit,type:a.sustained?'pose':'reference'})),...INVENTORY.characters.find(r=>r.character_id===c.character_id).animations.filter(a=>a.kind==='expression').map(a=>({...a,id:c.character_id+'/'+a.name,character:c.character_name,cid:c.character_id,outfit:c.outfit,type:'expression',image:'images/'+c.character_id+'/'+a.name+'-detail.png'}))]);
$('character').innerHTML='<option value="">所有角色</option>'+DATA.characters.map(c=>'<option value="'+esc(c.character_id)+'">'+esc(c.character_name)+'</option>').join('');$('character').value=DATA.characters[0].character_id;
$('summary').textContent=`29 人 · ${rows.filter(r=>r.type!=='expression').length} 个身体姿态条目（${rows.filter(r=>r.type==='pose').length} 个保持姿态） · ${rows.filter(r=>r.type==='expression').length} 个独立表情`;
function save(){try{localStorage.setItem('kaibun-all-poses-review-v3',JSON.stringify(feedback))}catch{}};
function render(){const q=$('search').value.trim().toLowerCase(),cid=$('character').value,kind=$('kind').value;const filtered=rows.filter(r=>(!cid||r.cid===cid)&&(kind==='all'||r.type===kind)&&(!q||JSON.stringify(r).toLowerCase().includes(q)));$('count').textContent=`筛选 ${filtered.length} 项；共 ${rows.length} 项，沿用已有标注，可按需备注。`;
$('cards').innerHTML=filtered.slice(0,shown).map(r=>{const o=r.observation||{},f=feedback[r.id]||{};return `<article class="card" data-review-id="${esc(r.id)}"><div class="content"><img class="pic" loading="lazy" src="${esc(r.image)}" alt="${esc(r.character+' '+r.name)}"><div class="info"><h2>${esc(r.character)} · ${r.type==='expression'?'表情 '+esc(r.label):'姿态 '+r.pose_number}</h2><p class="muted">${esc(r.outfit)} · ${esc(r.name)}</p><p>${esc(r.type==='expression'?'原始表情：'+r.label+'。保留原名称及变体编号；具体情境待审查。':o.description)}</p><span class="tag">${r.type==='pose'?'保持姿态':r.type==='expression'?'独立面部表情':'未标明保持段 · 仅作参考'}</span><span class="tag">沿用初步标注</span>${r.type!=='expression'?`<span class="tag">姿态幅度：${esc({low:'低',medium:'中',high:'高'}[o.intensity]||'待审查')}</span>`:''}${(o.compatible||[]).map(v=>`<div class="fit"><b>${esc(emotions[v.emotion]||v.emotion)} · ${esc(confidence[v.confidence]||v.confidence)}</b>${esc(v.condition)}<p class="muted">搭配表情：${esc(v.face)}</p></div>`).join('')}${r.type!=='expression'?'<p class="muted">平静及非说话角色入镜使用该衣装可用待机；其他姿态沿用已有适配说明，不追加复审。</p>':''}<details><summary>原始信息与标注范围</summary>截图：${r.sample_time}s；原始名称：${esc(r.name)}。${esc((o.limits||[]).join('；'))}</details></div></div><footer><select data-status="${esc(r.id)}" aria-label="${esc(r.id)}审查状态">${['沿用初审','通过','需修改','不采用'].map(v=>`<option${f.status===v?' selected':''}>${v}</option>`).join('')}</select><textarea data-note="${esc(r.id)}" aria-label="${esc(r.id)}审查意见" placeholder="审查意见">${esc(f.note||'')}</textarea></footer></article>`}).join('');$('more').hidden=filtered.length<=shown;
document.querySelectorAll('[data-status]').forEach(e=>e.onchange=()=>{feedback[e.dataset.status]={...(feedback[e.dataset.status]||{}),status:e.value};save()});document.querySelectorAll('[data-note]').forEach(e=>e.oninput=()=>{feedback[e.dataset.note]={...(feedback[e.dataset.note]||{}),note:e.value};save()});}
for(const id of ['character','kind','search'])$(id).addEventListener(id==='search'?'input':'change',()=>{shown=12;render()});$('more').onclick=()=>{shown+=12;render()};$('export').onclick=()=>{const blob=new Blob([JSON.stringify({version:DATA.version,feedback},null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='motion-all-my-feedback.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};render();</script></html>'''

def main():
    parser=argparse.ArgumentParser();parser.add_argument('directory',type=Path);args=parser.parse_args()
    folder=args.directory
    data=json.loads((folder/'motion-annotations-all.json').read_text(encoding='utf-8'))
    inventory=json.loads((folder/'stand-catalog.json').read_text(encoding='utf-8'))
    from PIL import Image, ImageChops, ImageOps
    for character in inventory['characters']:
        for clip in character['animations']:
            if clip['kind']!='expression': continue
            source=folder/'images'/character['character_id']/(clip['name']+'.png')
            detail=source.with_name(source.stem+'-detail.png')
            if detail.is_file(): continue
            image=Image.open(source).convert('RGB')
            diff=ImageChops.difference(image,Image.new('RGB',image.size,'white')).convert('L')
            box=diff.point(lambda value:255 if value>35 else 0).getbbox()
            if box is None: raise ValueError('Empty expression preview: '+str(source))
            left,top,right,bottom=box
            crop=image.crop((max(0,left-12),max(0,top-8),min(image.width,right+12),min(image.height,top+int((bottom-top)*.38))))
            ImageOps.pad(crop,(360,360),color='white',method=Image.Resampling.LANCZOS).save(detail)
    page=PAGE.replace('__DATA__',json.dumps(data,ensure_ascii=False).replace('<','\\u003c')).replace('__INVENTORY__',json.dumps(inventory,ensure_ascii=False).replace('<','\\u003c'))
    (folder/'review.html').write_text(page,encoding='utf-8')
    print(folder/'review.html')

if __name__=='__main__': main()
