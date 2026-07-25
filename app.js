let data={classes:[],students:[],assignments:[],mistakes:[],seats:[],performances:[]}, settings={}, dashboardClass='', token=localStorage.getItem('token')||'', currentUser=null, seatClass='', seatLesson='', selSeat=null, seatDraftImage='';
const $=s=>document.querySelector(s), esc=s=>String(s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const ROLE_NAMES={admin:'管理员',teacher:'老师',student:'学生'};
const SEAT_TAGS=['积极发言','认真听讲','思考深入','合作良好','进步明显','需要关注'];
async function api(url,opts={}){opts.headers={...(opts.headers||{}),...(token?{Authorization:`Bearer ${token}`}:{})};const r=await fetch(url,opts),d=await r.json();if(r.status===401){token='';localStorage.removeItem('token');showAuth()}if(!r.ok){const e=Error(d.error||'操作失败');if(d.conflict)e.conflict=d;throw e}return d}
function toast(msg){const t=$('#toast');t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2600)}
function showAuth(){$('#auth-screen').classList.add('show')} function hideAuth(){$('#auth-screen').classList.remove('show')}
function setSession(t,u){token=t;localStorage.setItem('token',t);currentUser=u;$('#user-chip').textContent=`${u.realName||u.username} · ${ROLE_NAMES[u.role]||u.role}`;applyRole()}
function applyRole(){const r=currentUser?.role;document.body.classList.toggle('role-student',r==='student');const acc=$('#settings-accounts');if(acc)acc.style.display=r==='admin'?'':'none';const navAcc=$('#nav-accounts');if(navAcc)navAcc.style.display='none';if(r==='student'){document.querySelectorAll('.page').forEach(x=>x.classList.remove('show'));$('#student-home').classList.add('show');$('#page-title').textContent='我的错题';$('#subtitle').textContent='老师录入并确认后的错题会显示在这里'}}
function className(id){return data.classes.find(x=>x.id===id)?.name||'未分班'} function studentName(id){return data.students.find(x=>x.id===id)?.name||'未识别学生'}
function localDate(d=new Date()){return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`}
function lessonLabel(a){const p=[];if(a.date)p.push(a.date);if(a.period)p.push(`第${a.period}节`);p.push(className(a.classId));return p.join(' · ')}
function fillSelect(selector,items,placeholder,fmt=x=>x.name){const el=$(selector);if(!el)return;const old=el.value;el.innerHTML=`<option value="">${placeholder}</option>`+items.map(x=>`<option value="${x.id}">${esc(fmt(x))}</option>`).join('');el.value=old}
function render(){
 fillSelect('#student-class',data.classes,'请选择班级');fillSelect('#lesson-class',data.classes,'请选择班级');fillSelect('#analyze-lesson',[...data.assignments].reverse(),'请选择课次',x=>`${lessonLabel(x)} · ${x.title}`);fillSelect('#dashboard-class',data.classes,'全部班级');fillSelect('#mistake-assignment',data.assignments,'请选择课次',x=>`${x.title} · ${lessonLabel(x)}`);fillSelect('#mistake-student',data.students,'请选择学生',x=>`${x.name} · ${className(x.classId)}`);
 const aClass={};data.assignments.forEach(a=>aClass[a.id]=a.classId);const classOf=m=>aClass[m.assignmentId]||data.students.find(s=>s.id===m.studentId)?.classId||'';
 const mistakes=dashboardClass?data.mistakes.filter(m=>classOf(m)===dashboardClass):data.mistakes, students=dashboardClass?data.students.filter(s=>s.classId===dashboardClass):data.students;
 $('#class-count').textContent=data.classes.length;$('#student-count').textContent=students.length;$('#mistake-count').textContent=mistakes.length;$('#pending-count').textContent=mistakes.filter(x=>x.status==='pending'||x.errorType==='待老师确认').length;
 const by=k=>mistakes.reduce((a,x)=>{a[x[k]||'待确认']=(a[x[k]||'待确认']||0)+1;return a},{}), kp=by('knowledgePoint'), et=by('errorType');
 const kc=$('#knowledge-chart');kc.className='bar-chart'+(!Object.keys(kp).length?' empty':'');kc.innerHTML=Object.keys(kp).length?Object.entries(kp).sort((a,b)=>b[1]-a[1]).slice(0,7).map(([k,v])=>`<div class="bar" style="height:${Math.max(25,v/Math.max(...Object.values(kp))*190)}px"><span>${v}人</span><label>${esc(k)}</label></div>`).join(''):'暂时还没有错题数据';
 const colors=['#5b4bda','#ee6eaa','#ff9d4d','#48b9ac','#7895e8'], max=Math.max(...Object.values(et),1),tc=$('#type-chart');tc.className='type-chart'+(!Object.keys(et).length?' empty':'');tc.innerHTML=Object.keys(et).length?Object.entries(et).sort((a,b)=>b[1]-a[1]).map(([k,v],i)=>`<div class="type-row"><span>${esc(k)}</span><div class="track"><div class="fill" style="width:${v/max*100}%;background:${colors[i%colors.length]}"></div></div><b>${v}</b></div>`).join(''):'暂时还没有错题数据';
 const freq=Object.values(mistakes.reduce((a,x)=>{const k=x.question||'未填写题目描述';(a[k]??={q:k,n:0,ids:[]}).n++;a[k].ids.push(x.studentId);return a},{})).sort((a,b)=>b.n-a.n).slice(0,5);$('#frequency-table').innerHTML=freq.length?`<div class="row-list">${freq.map(x=>`<div class="item"><div><b>${esc(x.q)}</b><div class="meta">涉及学生：${[...new Set(x.ids)].map(studentName).join('、')}</div></div><span class="tag">${x.n} 条记录</span></div>`).join('')}</div>`:'录入错题后，这里会展示最需要讲解的题目。';
 const todays=data.assignments.filter(a=>a.date===localDate()).sort((a,b)=>(+a.period||99)-(+b.period||99)),ts=$('#today-schedule');ts.className=todays.length?'':'table-empty';ts.innerHTML=todays.length?`<div class="row-list">${todays.map(a=>`<div class="item"><div><b>${a.period?`第${esc(a.period)}节`:'未指定节次'} · ${esc(className(a.classId))}</b><div class="meta">${esc(a.title)}${(a.knowledgePoints||[]).length?' · 知识点：'+esc(a.knowledgePoints.join('、')):''}</div></div></div>`).join('')}</div>`:'今天还没有上课记录，导入作业时选择今天的日期即可显示在这里。';
 $('#class-list').innerHTML=data.classes.length?`<div class="row-list">${data.classes.map(c=>{const ss=data.students.filter(s=>s.classId===c.id);return `<div class="item"><div><b>${esc(c.name)}</b><div class="meta">${esc(c.grade||'未填写年级')} · ${ss.length} 名学生${ss.length?'：'+ss.map(s=>esc(s.name)).join('、'):''}</div></div><button class="danger" onclick="del('classes','${c.id}')">删除</button></div>`}).join('')}</div>`:'请先创建一个班级。';
 $('#assignment-list').innerHTML=data.assignments.length?`<div class="row-list">${[...data.assignments].reverse().map(a=>`<div class="item"><div><b>${esc(a.title)}</b><div class="meta">${esc(lessonLabel(a))}<br>${esc(a.content||'AI 未识别课堂摘要')}<br>知识点：${esc((a.knowledgePoints||[]).join('、')||'待确认')}</div></div></div>`).join('')}</div>`:'导入课堂报告后，每节课的记录会显示在这里。';
 const recent=[...data.mistakes].reverse().slice(0,8);$('#mistake-list').innerHTML=recent.length?`<div class="row-list">${recent.map(m=>`<div class="item"><div><b>${esc(m.question||'未填写题目')}</b><div class="meta">${esc(studentName(m.studentId))} · ${esc(m.knowledgePoint)} · ${esc(m.errorType)}${m.note?'<br>'+esc(m.note):''}</div></div><span class="tag ${m.status==='pending'?'pending':''}">${m.status==='pending'?'待确认':'已确认'}</span><button class="danger" onclick="del('mistakes','${m.id}')">删除</button></div>`).join('')}</div>`:'尚未录入错题。';
}
function seatAt(r,c){return data.seats.find(s=>s.classId===seatClass&&s.row===r&&s.col===c)}
function perfAt(r,c){return data.performances.find(p=>p.classId===seatClass&&(p.assignmentId||'')===seatLesson&&p.row===r&&p.col===c)}
function renderSeats(){
 fillSelect('#seat-class',data.classes,'请选择班级');
 if(!$('#seat-class').value&&data.classes.length)$('#seat-class').value=data.classes[0].id;
 seatClass=$('#seat-class').value;
 const cls=data.classes.find(c=>c.id===seatClass);
 fillSelect('#seat-lesson',data.assignments.filter(a=>a.classId===seatClass).reverse(),'不关联课程',x=>`${lessonLabel(x)} · ${x.title}`);
 seatLesson=$('#seat-lesson').value;
 const map=$('#seat-map');
 if(!cls){map.innerHTML='<div class="table-empty">请先在“班级与学生”创建班级。</div>';return}
 if(document.activeElement!==$('#seat-rows'))$('#seat-rows').value=cls.seatRows||4;
 if(document.activeElement!==$('#seat-cols'))$('#seat-cols').value=cls.seatCols||6;
 const rows=cls.seatRows||4, cols=cls.seatCols||6;
 map.innerHTML=Array.from({length:rows},(_,i)=>i+1).map(r=>`<div class="seat-row" style="grid-template-columns:repeat(${cols},1fr)">`+Array.from({length:cols},(_,j)=>j+1).map(c=>{const seat=seatAt(r,c),perf=perfAt(r,c),stu=seat&&data.students.find(s=>s.id===seat.studentId),hasPerf=perf&&(perf.tags?.length||perf.note||perf.image);return `<button class="seat-cell${stu?'':' empty'}${hasPerf?' has-perf':''}${selSeat&&selSeat.r===r&&selSeat.c===c?' selected':''}" data-r="${r}" data-c="${c}"><span class="seat-idx">${String((r-1)*cols+c).padStart(2,'0')}</span><span class="seat-who">${stu?esc(stu.name):'空位'}</span><span class="seat-flag">${hasPerf?esc(perf.tags?.[0]||'已记录'):''}</span></button>`}).join('')+'</div>').join('');
 map.querySelectorAll('.seat-cell').forEach(b=>b.onclick=()=>selectSeat(+b.dataset.r,+b.dataset.c));
 if(selSeat)renderSeatEditor();
}
function selectSeat(r,c){selSeat={r,c};seatDraftImage='';renderSeats();renderSeatEditor()}
function renderSeatEditor(){
 const {r,c}=selSeat;$('#seat-editor-title').textContent=`第 ${r} 排 · 第 ${c} 列`;$('#seat-editor-body').style.display='grid';
 const seat=seatAt(r,c),perf=perfAt(r,c),tags=perf?.tags||[];
 fillSelect('#seat-student',data.students.filter(s=>s.classId===seatClass),'未安排');$('#seat-student').value=seat?.studentId||'';
 $('#seat-tags').innerHTML=SEAT_TAGS.map(t=>`<button type="button" class="${tags.includes(t)?'active':''}" data-tag="${t}">${t}</button>`).join('');
 $('#seat-tags').querySelectorAll('button').forEach(b=>b.onclick=()=>b.classList.toggle('active'));
 $('#seat-note').value=perf?.note||'';
 $('#seat-work-note').value=perf?.workNote||'';
 renderSeatImage(perf?.image||'');
}
function renderSeatImage(img){$('#seat-image-preview').innerHTML=img?`<div class="img-wrap"><img src="${img}"><button type="button" class="danger" id="seat-image-del">删除</button></div>`:'';if(img)$('#seat-image-del').onclick=()=>{seatDraftImage='__clear__';renderSeatImage('')}}
async function uploadSeatImage(f){try{if(f.size>5*1024*1024)throw Error('图片超过 5MB');const src=await new Promise((ok,no)=>{const r=new FileReader;r.onload=()=>ok(r.result);r.onerror=no;r.readAsDataURL(f)});const res=await api('/api/upload',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({data:src})});seatDraftImage=res.path;renderSeatImage(res.path);toast('截图已添加，点“保存记录”后生效')}catch(e){toast(e.message)}}
function renderStudent(){const ms=data.mistakes,amap={};data.assignments.forEach(a=>amap[a.id]=a);
 $('#st-mistake-count').textContent=ms.length;$('#st-pending-count').textContent=ms.filter(x=>x.status==='pending').length;
 $('#student-mistake-list').innerHTML=ms.length?`<div class="row-list">${[...ms].reverse().map(m=>{const a=amap[m.assignmentId];return `<div class="item"><div><b>${esc(m.question||'未填写题目')}</b><div class="meta">${a?esc(lessonLabel(a))+'<br>':''}知识点：${esc(m.knowledgePoint)} · ${esc(m.errorType)}${m.note?'<br>'+esc(m.note):''}</div></div><span class="tag ${m.status==='pending'?'pending':''}">${m.status==='pending'?'待确认':'已确认'}</span></div>`}).join('')}</div>`:'老师还没有录入你的错题。'}
async function loadAccounts(){const r=await api('/api/users');$('#account-list').innerHTML=r.users.length?`<div class="row-list">${r.users.map(u=>`<div class="item"><div><b>${esc(u.realName||u.username)}</b><div class="meta">${esc(u.username)} · ${ROLE_NAMES[u.role]||esc(u.role)}${u.lastLoginAt?' · 最近登录 '+esc(u.lastLoginAt):''}</div></div>${u.username==='root'?'<span class="tag">内置管理员</span>':`<button onclick="resetPwd('${u.id}')">重置密码</button><button class="danger" onclick="delUser('${u.id}')">删除</button>`}</div>`).join('')}</div>`:'暂无账号。'}
window.resetPwd=async id=>{const p=prompt('请输入新密码（至少 6 位）');if(!p)return;try{await api(`/api/users/${id}/password`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:p})});toast('密码已重置')}catch(e){toast(e.message)}};
window.delUser=async id=>{if(!confirm('确定删除这个账号吗？'))return;try{await api(`/api/users/${id}`,{method:'DELETE'});await loadAccounts();toast('账号已删除')}catch(e){toast(e.message)}};
function renderSettings(){
 const groups={};settings.models.forEach(m=>{(groups[m.providerKey]??={provider:m.provider,models:[]}).models.push(m)});
 $('#provider-list').innerHTML=Object.entries(groups).map(([pk,g])=>`<div class="provider-card"><div class="provider-head"><b>${esc(g.provider)}</b><span class="key-badge ${settings.savedKeys?.[pk]?'saved':''}">${settings.savedKeys?.[pk]?'已保存 Key':'未保存 Key'}</span></div><div class="model-options">${g.models.map(m=>`<label class="${m.id===settings.model?'active':''}"><input type="radio" name="model-radio" value="${m.id}" ${m.id===settings.model?'checked':''}>${esc(m.label)}</label>`).join('')}</div><div class="provider-key-row"><input type="password" id="key-${pk}" placeholder="${settings.savedKeys?.[pk]?'已保存此厂商 Key（留空不覆盖）':'粘贴该厂商的 API Key'}" autocomplete="off"><button class="save-key" data-pk="${pk}">保存</button><button class="test-key" data-pk="${pk}">测试连接</button>${pk==='mimo'?'<button class="import-cc">从 CC Switch 导入</button>':''}</div><p class="test-msg" id="msg-${pk}"></p></div>`).join('');
 $('#provider-list').querySelectorAll('input[name=model-radio]').forEach(r=>r.onchange=async()=>{try{await api('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:r.value})});settings.model=r.value;renderSettings();toast('已切换模型')}catch(e){toast(e.message)}});
 $('#provider-list').querySelectorAll('.save-key').forEach(b=>b.onclick=async()=>{const pk=b.dataset.pk,key=$(`#key-${pk}`).value.trim();if(!key){toast('请先粘贴 API Key');return}const model=groups[pk].models.find(m=>m.id===settings.model)?.id||groups[pk].models[0].id;try{await api('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model,apiKey:key})});$(`#key-${pk}`).value='';await refresh();toast('API Key 已保存')}catch(e){toast(e.message)}});
 $('#provider-list').querySelectorAll('.test-key').forEach(b=>b.onclick=async()=>{const pk=b.dataset.pk,msg=$(`#msg-${pk}`),model=groups[pk].models.find(m=>m.id===settings.model)?.id||groups[pk].models[0].id;try{b.disabled=true;b.textContent='测试中…';msg.textContent='';msg.className='test-msg';const r=await api('/api/models/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model,apiKey:$(`#key-${pk}`).value})});msg.textContent='✓ 连接成功，模型回复：'+r.reply;msg.classList.add('test-ok')}catch(e){msg.textContent='✗ '+e.message;msg.classList.add('test-fail')}finally{b.disabled=false;b.textContent='测试连接'}});
 const importBtn=$('#provider-list .import-cc');if(importBtn)importBtn.onclick=async()=>{try{const r=await api('/api/cc-switch/import',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});await refresh();toast(`已从 CC Switch 导入小米 Key（${r.hint}）`)}catch(e){toast(e.message)}};
}
async function refresh(){if(currentUser?.role==='student'){data=await api('/api/data');renderStudent();return}[data,settings]=await Promise.all([api('/api/data'),api('/api/settings')]);render();renderSettings();renderSeats();renderAgent();loadAgents();loadHome();if(currentUser?.role==='admin')loadAccounts()} async function del(type,id){if(confirm('确定删除这条记录吗？')){await api(`/api/${type}/${id}`,{method:'DELETE'});await refresh();toast('已删除')}}window.del=del;
function form(id,url,mapper){const f=$(id);if(!f)return;f.addEventListener('submit',async e=>{e.preventDefault();try{const obj=Object.fromEntries(new FormData(f));await api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(mapper?mapper(obj):obj)});f.reset();await refresh();toast('保存成功')}catch(err){toast(err.message)}})}
form('#class-form','/api/classes',x=>({...x,createdAt:new Date().toISOString()}));form('#student-form','/api/students');
$('#account-form').addEventListener('submit',async e=>{e.preventDefault();try{const obj=Object.fromEntries(new FormData(e.target));await api('/api/users',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(obj)});e.target.reset();await loadAccounts();toast('账号已创建')}catch(err){toast(err.message)}});
$('#mistake-form').addEventListener('submit',async e=>{e.preventDefault();try{const obj=Object.fromEntries(new FormData(e.target));await api('/api/mistakes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(obj)});e.target.reset();await refresh();toast('错题已保存')}catch(err){toast(err.message)}});
async function uploadFiles(list){const paths=[];for(const f of [...list]){if(f.size>5*1024*1024)throw Error(`${f.name} 超过 5MB`);const src=await new Promise((ok,no)=>{const r=new FileReader;r.onload=()=>ok(r.result);r.onerror=no;r.readAsDataURL(f)});paths.push((await api('/api/upload',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({data:src})})).path)}return paths}
$('#lesson-form').addEventListener('submit',async e=>{e.preventDefault();const btn=$('#lesson-btn');const classId=$('#lesson-class').value,date=$('#lesson-date').value,period=$('#lesson-period').value;try{const report=await uploadFiles($('#report-files').files),title=$('#lesson-title').value.trim();if(!report.length&&!title)throw Error('请上传课堂内容报告，或手动填写课程名称');btn.disabled=true;btn.textContent='保存中…';const lesson=await api('/api/lessons',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({classId,date,period,reportImages:report,title})});e.target.reset();$('#lesson-date').value=localDate();await refresh();$('#analyze-lesson').value=lesson.id;toast(`课程已保存：${lesson.title}`)}catch(err){if(err.conflict){const x=err.conflict.existing||{};toast(`${x.date||'当天'}第${x.period||'?'}节已有课，同一时间段不能再开课`)}else{toast(err.message)}}finally{btn.disabled=false;btn.textContent='① 保存本节课'}});
$('#analyze-form').addEventListener('submit',async e=>{e.preventDefault();const btn=$('#analyze-btn');const assignmentId=$('#analyze-lesson').value;if(!assignmentId){toast('请先选择所属课程（没有可先在第 1 步保存本节课）');return}try{if(!settings.hasApiKey)throw Error('请先在“设置”中填写 API Key');btn.disabled=true;btn.textContent='AI 正在识别与统计…';const work=await uploadFiles($('#work-files').files);const result=await api('/api/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({assignmentId,workImages:work})});e.target.reset();await refresh();$('#analyze-lesson').value=assignmentId;toast(`分析完成：已整理 ${result.mistakes.length} 条错题`);document.querySelector('[data-page="dashboard"]').click()}catch(err){toast(err.message)}finally{btn.disabled=false;btn.textContent='✦ 开始 AI 错题分析'}});
$('#seat-class').addEventListener('change',()=>{seatClass=$('#seat-class').value;selSeat=null;$('#seat-editor-body').style.display='none';$('#seat-editor-title').textContent='点击左侧座位开始记录';renderSeats()});
$('#seat-lesson').addEventListener('change',()=>{seatLesson=$('#seat-lesson').value;renderSeats()});
$('#seat-layout-btn').onclick=async()=>{if(!seatClass){toast('请先选择班级');return}try{await api('/api/seat-layout',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({classId:seatClass,rows:+$('#seat-rows').value,cols:+$('#seat-cols').value})});selSeat=null;$('#seat-editor-body').style.display='none';$('#seat-editor-title').textContent='点击左侧座位开始记录';await refresh();toast('座位布局已保存')}catch(e){toast(e.message)}};
$('#seat-student').addEventListener('change',async()=>{if(!selSeat)return;try{await api('/api/seats',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({classId:seatClass,row:selSeat.r,col:selSeat.c,studentId:$('#seat-student').value})});await refresh();toast('座位已更新')}catch(e){toast(e.message)}});
$('#seat-save').onclick=async()=>{if(!selSeat)return;const body={classId:seatClass,assignmentId:seatLesson,row:selSeat.r,col:selSeat.c,studentId:$('#seat-student').value,tags:[...$('#seat-tags').querySelectorAll('button.active')].map(b=>b.dataset.tag),note:$('#seat-note').value,workNote:$('#seat-work-note').value};if(seatDraftImage==='__clear__')body.clearImage=true;else if(seatDraftImage)body.image=seatDraftImage;try{await api('/api/performances',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});seatDraftImage='';await refresh();toast('课堂记录已保存')}catch(e){toast(e.message)}};
$('#seat-browse').onclick=()=>$('#paste-file').click();
$('#paste-file').addEventListener('change',()=>{const f=$('#paste-file').files[0];if(f){uploadSeatImage(f);$('#paste-file').value=''}});
$('#seat-work-note').addEventListener('paste',e=>{const f=[...(e.clipboardData?.files||[])].find(x=>x.type.startsWith('image/'));if(f){e.preventDefault();uploadSeatImage(f)}});
let agentList=[], agentCur='通用', agentChats={};
function chatOf(name){return agentChats[name]??={api:[],bubbles:[]}}
function renderAgent(){fillSelect('#agent-student',data.students,'请选择学生',x=>`${x.name} · ${className(x.classId)}`);fillSelect('#agent-lesson',[...data.assignments].reverse(),'请选择课程',x=>`${lessonLabel(x)} · ${x.title}`)}
function appendMsg(role,text){const d=document.createElement('div');d.className='msg '+role;d.textContent=text;const box=$('#agent-messages');box.appendChild(d);box.scrollTop=box.scrollHeight}
function agentMsg(role,text){chatOf(agentCur).bubbles.push({role,text});appendMsg(role,text)}
function downloadMd(filename,text){const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([text],{type:'text/markdown;charset=utf-8'}));a.download=filename;a.click();URL.revokeObjectURL(a.href)}
function appendDownloadBtn(filename,text){const d=document.createElement('div');d.className='msg bot';const b=document.createElement('button');b.className='dl-btn';b.textContent='⬇ 下载 MD 文档';b.onclick=()=>downloadMd(filename,text);d.appendChild(b);const box=$('#agent-messages');box.appendChild(d);box.scrollTop=box.scrollHeight}
function agentDownloadBtn(filename,text){chatOf(agentCur).bubbles.push({dl:[filename,text]});appendDownloadBtn(filename,text)}
function appendThinking(text){const d=document.createElement('div');d.className='msg bot thinking';d.innerHTML=`<span class="dot-wave">${esc(text||'思考中')}</span>`;const box=$('#agent-messages');box.appendChild(d);box.scrollTop=box.scrollHeight;return d}
function renderMd(md,baseUrl){
 let h=esc(md);
 h=h.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g,(m,a,s)=>`<img loading="lazy" src="${/^https?:|^data:/.test(s)?s:baseUrl+encodeURI(s)}" alt="${a}">`);
 h=h.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>');
 h=h.replace(/^#### +(.*)$/gm,'<h4>$1</h4>').replace(/^### +(.*)$/gm,'<h3>$1</h3>').replace(/^## +(.*)$/gm,'<h2>$1</h2>').replace(/^# +(.*)$/gm,'<h1>$1</h1>');
 h=h.replace(/\*\*([^*\n]+)\*\*/g,'<b>$1</b>').replace(/`([^`\n]+)`/g,'<code>$1</code>');
 let out='',list=null;
 for(const ln of h.split('\n')){
  const ul=ln.match(/^\s*[-*] +(.*)/),ol=ln.match(/^\s*\d+\. +(.*)/);
  if(ul){if(list!=='ul'){out+=(list?`</${list}>`:'')+'<ul>';list='ul'}out+=`<li>${ul[1]}</li>`;continue}
  if(ol){if(list!=='ol'){out+=(list?`</${list}>`:'')+'<ol>';list='ol'}out+=`<li>${ol[1]}</li>`;continue}
  if(list){out+=`</${list}>`;list=null}
  out+=ln.trim()?(/^<h\d|^<img/.test(ln.trim())?ln:`<p>${ln}</p>`):'';
 }
 if(list)out+=`</${list}>`;
 return out;
}
function appendFileCard(f){const d=document.createElement('div');d.className='msg bot file-card';d.innerHTML=`<span class="file-name">📄 ${esc(f.name)}</span><span class="file-actions"><button type="button" class="dl-btn pv">预览</button><a class="dl-btn" href="${encodeURI(f.url)}" download="${esc(f.name)}">下载</a></span>`;d.querySelector('.pv').onclick=async()=>{try{const text=await (await fetch(encodeURI(f.url))).text();openMdModal(f.name,text,f.url.slice(0,f.url.lastIndexOf('/')+1))}catch(e){toast('读取文件失败')}};const box=$('#agent-messages');box.appendChild(d);box.scrollTop=box.scrollHeight}
function agentFileCard(f){chatOf(agentCur).bubbles.push({file:f});appendFileCard(f)}
function openMdModal(name,text,baseUrl){$('#md-modal-name').textContent=name;$('#md-modal-content').innerHTML=renderMd(text,baseUrl||'');$('#md-modal-dl').style.display='';$('#md-modal-dl').onclick=()=>downloadMd(name,text);$('#md-modal').classList.add('show')}
$('#md-modal-close').onclick=()=>$('#md-modal').classList.remove('show');
$('#img-modal-close').onclick=()=>$('#img-modal').classList.remove('show');
function renderChatHistory(){const box=$('#agent-messages');box.innerHTML='';const bs=chatOf(agentCur).bubbles;if(!bs.length){appendMsg('bot',`你好，我是「${agentCur}」智能体。可以直接问我学生的情况，或把课堂表现发给我润色；左侧选好学生和课程可生成完整学习总结。`);return}for(const b of bs){if(b.file)appendFileCard(b.file);else if(b.dl)appendDownloadBtn(...b.dl);else appendMsg(b.role,b.text)}}
function agentPickerFill(){const el=$('#agent-picker');if(!el)return;const old=el.value||agentCur;el.innerHTML=agentList.map(a=>`<option value="${esc(a.name)}">${esc(a.name)}${a.source==='mine'?'（我的）':''}</option>`).join('');el.value=[...el.options].some(o=>o.value===old)?old:'通用';agentCur=el.value||'通用'}
function renderAgentMgr(){const el=$('#agent-list');if(!el)return;el.innerHTML=agentList.map(a=>`<button type="button" class="agent-item ${a.name===agentCur?'active':''}" data-name="${esc(a.name)}">${esc(a.name)}<small>${a.source==='mine'?'我的':'通用'}</small></button>`).join('');el.querySelectorAll('.agent-item').forEach(b=>b.onclick=()=>openAgent(b.dataset.name))}
async function loadAgents(){if(currentUser?.role==='student')return;try{const r=await api('/api/agents');agentList=r.agents;const ms=$('#agent-model');if(ms&&ms.options.length<=1)ms.innerHTML='<option value="">跟随当前模型</option>'+settings.models.map(m=>`<option value="${m.id}">${esc(m.label)}</option>`).join('');renderAgentMgr();agentPickerFill();openAgent(agentCur)}catch(e){}}
async function openAgent(name){try{const r=await api(`/api/agents/${encodeURIComponent(name)}`);agentCur=name;let body=r.soul,model='';const m=r.soul.match(/^---\s*\n([\s\S]*?)\n---\s*\n/);if(m){body=r.soul.slice(m[0].length);const mm=m[1].match(/model:\s*(\S+)/);if(mm)model=mm[1]}if(document.activeElement!==$('#agent-soul'))$('#agent-soul').value=body;$('#agent-model').value=model;$('#agent-cur-name').textContent=name;$('#agent-cur-src').textContent=r.source==='mine'?'我的':'通用模板';renderAgentMgr();agentPickerFill()}catch(e){}}
$('#agent-soul-save').onclick=async()=>{const model=$('#agent-model').value,head=model?`---\nmodel: ${model}\n---\n`:'';try{await api(`/api/agents/${encodeURIComponent(agentCur)}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({soul:head+$('#agent-soul').value})});toast(`智能体「${agentCur}」已保存`)}catch(e){toast(e.message)}};
$('#agent-new').onclick=async()=>{const name=prompt('新智能体名称（将从当前选中的智能体复制一份）');if(!name||!name.trim())return;try{await api('/api/agents',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name.trim(),from:agentCur})});await loadAgents();openAgent(name.trim());toast('已创建')}catch(e){toast(e.message)}};
$('#agent-delete').onclick=async()=>{if(!confirm(`删除智能体「${agentCur}」？（通用模板不受影响）`))return;try{await api(`/api/agents/${encodeURIComponent(agentCur)}`,{method:'DELETE'});agentCur='通用';await loadAgents();openAgent('通用');toast('已删除')}catch(e){toast(e.message)}};
$('#agent-picker').addEventListener('change',()=>{agentCur=$('#agent-picker').value;renderAgentMgr();renderChatHistory()});
$('#agent-form').addEventListener('submit',async e=>{e.preventDefault();const btn=$('#agent-generate');const studentId=$('#agent-student').value,assignmentId=$('#agent-lesson').value;if(!studentId||!assignmentId){toast('请先选择学生和课程');return}try{btn.disabled=true;btn.textContent='Agent 正在分析截图与记录…';agentMsg('user',`生成 ${studentName(studentId)} 这节课的学习总结`);const r=await api('/api/agent/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({studentId,assignmentId,agent:agentCur,savePath:$('#agent-path').value,extra:$('#agent-extra').value})});agentMsg('bot',r.markdown+`\n\n—— 已保存到：${r.path}`);const lesson=data.assignments.find(a=>a.id===assignmentId);agentDownloadBtn(`${studentName(studentId)}-${lesson?.date||localDate()}.md`,r.markdown);toast('总结已生成并保存')}catch(err){agentMsg('bot','生成失败：'+err.message);toast(err.message)}finally{btn.disabled=false;btn.textContent='✦ 生成并保存总结'}});
async function sendAgent(){const text=$('#agent-input').value.trim();if(!text)return;$('#agent-input').value='';agentMsg('user',text);const hist=chatOf(agentCur).api;hist.push({role:'user',content:text});$('#agent-send').disabled=true;const thinking=appendThinking('思考中…');try{const r=await api('/api/agent/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({messages:hist,agent:agentCur,studentId:$('#agent-student').value,assignmentId:$('#agent-lesson').value})});thinking.remove();hist.push({role:'assistant',content:r.reply});if(r.file){agentMsg('bot','总结已生成，文档如下，可预览或下载：');agentFileCard(r.file)}else{agentMsg('bot',r.reply)}}catch(err){thinking.remove();agentMsg('bot','出错了：'+err.message)}finally{$('#agent-send').disabled=false}}
$('#agent-send').onclick=sendAgent;
$('#agent-input').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.isComposing){e.preventDefault();sendAgent()}});
$('#tab-login').onclick=()=>{$('#tab-login').classList.add('active');$('#tab-register').classList.remove('active');$('#login-form').style.display='grid';$('#register-form').style.display='none'};
$('#tab-register').onclick=()=>{$('#tab-register').classList.add('active');$('#tab-login').classList.remove('active');$('#register-form').style.display='grid';$('#login-form').style.display='none'};
$('#login-form').addEventListener('submit',async e=>{e.preventDefault();try{const obj=Object.fromEntries(new FormData(e.target));const r=await api('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(obj)});setSession(r.token,r.user);e.target.reset();hideAuth();await refresh();toast(`欢迎回来，${r.user.realName||r.user.username}`)}catch(err){toast(err.message)}});
$('#register-form').addEventListener('submit',async e=>{e.preventDefault();try{const obj=Object.fromEntries(new FormData(e.target));if(obj.password!==obj.confirm)throw Error('两次输入的密码不一致');if(obj.role==='student'&&!obj.realName.trim())throw Error('学生注册需填写与班级名单一致的姓名');const r=await api('/api/auth/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:obj.username,realName:obj.realName,password:obj.password,role:obj.role})});setSession(r.token,r.user);e.target.reset();hideAuth();await refresh();toast('注册成功，已自动登录')}catch(err){toast(err.message)}});
$('#logout-btn').onclick=()=>{token='';localStorage.removeItem('token');location.reload()};
document.querySelectorAll('.nav').forEach(b=>b.addEventListener('click',()=>{document.querySelectorAll('.nav').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.querySelectorAll('.page').forEach(x=>x.classList.remove('show'));$('#'+b.dataset.page).classList.add('show');const n={home:['课次','围绕一节课完成全部教学工作'],workspace:['课次工作台','考勤—课堂记录—作业—分析—确认—学情'],dashboard:['班级学习总览','快速掌握全班的共性薄弱点'],import:['导入作业（旧入口）','新流程：在课次工作台②课后作业中按学生上传'],seats:['课堂座位（旧入口）','新流程：在课次工作台①课堂记录中直接使用座位图'],agent:['总结 Agent（旧入口）','新流程：在课次工作台⑤学生反馈中逐生生成'],classes:['班级与学生','创建班级；学生姓名也可由 AI 从作业中自动识别'],mistakes:['人工补充（旧入口）','新流程：在课次工作台③待确认底部补充'],settings:['设置','选择国内模型并配置本机 API Key']};$('#page-title').textContent=n[b.dataset.page][0];$('#subtitle').textContent=n[b.dataset.page][1]}));$('#dashboard-class').addEventListener('change',e=>{dashboardClass=e.target.value;render()});$('#lesson-date').value=localDate();

// ---------- V0.2 课次首页与工作台 ----------
const LESSON_STATUS={'in_class':'上课中','waiting_homework':'等待作业','analyzing':'作业分析中','pending_review':'待确认','completed':'已完成'};
const ATT_STATUS={'present':'已到','late':'迟到','leave':'请假','absent':'缺勤','early_leave':'早退'};
const SUB_STATUS={'not_submitted':'未收取','submitted':'已收取','uploaded':'已上传','analyzing':'分析中','pending_review':'待确认','completed':'已完成','not_required':'无需提交'};
let ws=null, wsTab='record';
function gotoPage(id){document.querySelectorAll('.nav').forEach(x=>x.classList.remove('active'));const b=document.querySelector(`.nav[data-page="${id}"]`);if(b)b.classList.add('active');document.querySelectorAll('.page').forEach(x=>x.classList.remove('show'));$('#'+id).classList.add('show')}

async function loadHome(){
 fillSelect('#home-class',data.classes,'请选择班级');if(!$('#home-date').value)$('#home-date').value=localDate();
 try{
  const r=await api('/api/lessons');
  homeLessons=r.lessons;
  suggestPeriod();
  const box=$('#lesson-groups');
  if(!homeLessons.length){box.innerHTML='<section class="panel"><div class="table-empty">还没有课次，先在上方开课。</div></section>';return}
  const today=localDate();
  const todays=homeLessons.filter(c=>c.lesson.date===today);
  const pendings=homeLessons.filter(c=>c.lesson.date!==today&&c.lesson.status!=='completed');
  const dones=homeLessons.filter(c=>c.lesson.status==='completed'&&c.lesson.date!==today);
  const card=c=>{
   const l=c.lesson,a=c.attendance,h=c.homework;
   const special=['late','leave','absent','early_leave'].filter(k=>a[k]).map(k=>`${a[k]} 人${ATT_STATUS[k]}`).join('，');
   const na=NEXT_ACTION[l.status]||NEXT_ACTION.in_class;
   const btnText=typeof na.text==='function'?na.text(c):na.text;
   return `<article class="lesson-card"><div class="lc-top"><b>${esc(c.className)}</b><span class="ws-badge st-${l.status}">${esc(c.statusName)}</span></div><div class="lc-sub">${esc(l.date||'未排期')} 第${esc(l.period||'?')}节</div><div class="lc-stats"><span>应到 ${c.studentCount}${special?'｜'+esc(special):''}</span><span>作业 ${h.submitted}/${h.total}${h.analyzed?`｜已分析 ${h.analyzed}`:''}</span>${c.pendingCount?`<span class="warn-text">⚠ 待确认 ${c.pendingCount} 项</span>`:''}</div><div class="lc-actions"><button class="primary${l.status==='pending_review'?' lc-urgent':''}" onclick="openWorkspace('${l.id}','${na.tab}')">${esc(btnText)}</button><button type="button" class="lc-del" title="删除这节课" onclick="deleteLesson('${l.id}')">🗑</button></div></article>`};
  let html='';
  html+=`<section class="panel"><h2>今天的课</h2>${todays.length?`<div class="lesson-cards">${todays.map(card).join('')}</div>`:`<div class="table-empty">今天还没有课，在上方开课即可开始。</div>`}</section>`;
  if(pendings.length)html+=`<section class="panel"><h2>待处理（${pendings.length}）</h2><div class="lesson-cards">${pendings.map(card).join('')}</div></section>`;
  if(dones.length)html+=`<section class="panel"><details class="done-group"><summary>已完成的课（${dones.length}）</summary><div class="lesson-cards">${dones.map(card).join('')}</div></details></section>`;
  box.innerHTML=html;
 }catch(e){/* 登录失效等情况忽略 */}
}
const NEXT_ACTION={in_class:{text:'继续上课 →',tab:'record'},waiting_homework:{text:'去收作业 →',tab:'homework'},analyzing:{text:'查看分析进度 →',tab:'homework'},pending_review:{text:c=>`去确认 ${c.pendingCount} 项 →`,tab:'review'},completed:{text:'查看学情与反馈 →',tab:'insights'}};
let homeLessons=[];
window.deleteLesson=async id=>{const c=homeLessons.find(x=>x.lesson.id===id);if(!c)return;if(!confirm(`确定删除「${c.className} ${c.lesson.date} 第${c.lesson.period||'?'}节」这节课吗？\n这节课的考勤、作业记录与错题会一起删除，不可恢复。`))return;try{await api(`/api/lessons/${id}`,{method:'DELETE'});await loadHome();toast('课次已删除')}catch(e){toast(e.message)}};
function suggestPeriod(){const cls=$('#home-class').value,date=$('#home-date').value||localDate();const todays=homeLessons.filter(c=>c.lesson.classId===cls&&c.lesson.date===date);const next=todays.length?Math.max(...todays.map(c=>+c.lesson.period||0))+1:1;$('#home-period').value=Math.min(next,10)}
window.openWorkspace=async function(lessonId,tab){
 try{
  ws=await api(`/api/lessons/${lessonId}`);
  if(tab)wsTab=tab;
  gotoPage('workspace');
  $('#page-title').textContent='课次工作台';$('#subtitle').textContent='考勤—课堂记录—作业—分析—确认—学情';
  renderWs();
 }catch(e){toast(e.message)}
};
function wsStudent(sid){return ws.students.find(s=>s.id===sid)}
function renderWs(){
 const l=ws.lesson,cls=ws.class||{};
 $('#ws-title').textContent=`${cls.name||'未分班'} · ${l.date||'未排期'} 第${l.period||'?'}节`;
 $('#ws-lesson-title').textContent=ws.assignment?`《${ws.assignment.title}》`:'（未命名，点 ✎ 填写课名）';
 $('#ws-status').textContent=LESSON_STATUS[l.status]||l.status;$('#ws-status').className=`ws-badge st-${l.status}`;
 $('#ws-status-set').value=l.status;
 const got=ws.submissions.filter(s=>s.images&&s.images.length).length;
 $('#ws-hw-chip').textContent=`作业 ${got}/${ws.students.length}`;
 $('#ws-pending-chip').textContent=`待确认 ${ws.pendingCount}`;$('#ws-pending-chip').style.display=ws.pendingCount?'':'none';
 const badge=$('#ws-tab-badge');badge.textContent=ws.pendingCount||'';badge.style.display=ws.pendingCount?'inline-block':'none';
 $('#ws-tabs').querySelectorAll('button').forEach(x=>x.classList.toggle('active',x.dataset.tab===wsTab));
 ({record:renderWsRecord,homework:renderWsHomework,review:renderWsReview,insights:renderWsInsights,feedback:renderWsFeedback})[wsTab]();
}
$('#ws-title-edit').onclick=async()=>{const cur=ws.assignment?.title||'';const t=prompt('修改这节课的课名：',cur);if(t===null)return;try{await api(`/api/lessons/${ws.lesson.id}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:t.trim()})});await reloadWs();toast('课名已更新')}catch(e){toast(e.message)}};
async function reloadWs(){ws=await api(`/api/lessons/${ws.lesson.id}`);renderWs()}

// ① 课堂记录：考勤（分段按钮）+ 内嵌座位图
const ATT_KEYS=Object.keys(ATT_STATUS),ATT_SHORT={present:'已到',late:'迟',leave:'假',absent:'缺',early_leave:'早退'};
const ATT_DEFAULT='absent'; // 默认缺勤：安排到座位上的学生才自动改为“已到”
function renderWsRecord(){
 const att={};for(const a of ws.attendance)att[a.studentId]=a;
 const counts={present:0,late:0,leave:0,absent:0,early_leave:0};
 ws.students.forEach(s=>{const st=(att[s.id]?.status)||ATT_DEFAULT;counts[st]=(counts[st]||0)+1});
 const endBtn=ws.lesson.status==='in_class'?`<button class="primary" id="ws-end-class">下课，开始收作业 →</button>`:'';
 // 调课生：本节课名单里不属于本班的学生 + 可添加的候选（其他班、且不在本名单）
 const classIdSet=new Set(ws.students.filter(s=>s.classId===ws.lesson.classId).map(s=>s.id));
 const extraStudents=ws.students.filter(s=>s.classId!==ws.lesson.classId);
 const candidates=data.students.filter(s=>s.classId!==ws.lesson.classId&&!ws.students.some(x=>x.id===s.id));
 const attRow=(s,isExtra)=>{const st=(att[s.id]?.status)||ATT_DEFAULT;return `<div class="att-row${st==='absent'||st==='leave'?' att-off':''}" data-sid="${s.id}"><span class="att-name">${esc(s.name)}${isExtra?'<span class="att-extra-tag">调课</span>':''}</span><span class="att-seg">${ATT_KEYS.map(k=>`<button type="button" class="${k===st?'active':''}" data-k="${k}">${ATT_STATUS[k]}</button>`).join('')}</span>${isExtra?`<button type="button" class="att-extra-del" title="从本节课移除">✕</button>`:''}</div>`};
 $('#ws-body').innerHTML=`<section class="panel"><div class="hw-toolbar"><h2>考勤</h2><button type="button" id="att-reset">全部缺勤</button>${endBtn}</div><p class="privacy">座位上有人的学生自动判定为“已到”，其余默认“缺勤”；也可手动点改为迟到/请假等。应到 ${ws.students.length}｜已到 ${counts.present}｜迟到 ${counts.late}｜请假 ${counts.leave}｜缺勤 ${counts.absent}｜早退 ${counts.early_leave}</p><div class="att-list">${ws.students.map(s=>attRow(s,extraStudents.some(x=>x.id===s.id))).join('')}</div><details class="extra-box"><summary>调课 / 外班学生（${extraStudents.length}）</summary><div class="extra-add"><select id="extra-student-sel"><option value="">选择要加入本节课的外班学生…</option>${candidates.map(s=>`<option value="${s.id}">${esc(s.name)}（${esc(className(s.classId))}）</option>`).join('')}</select><button type="button" id="extra-add-btn">＋ 加入本节课</button></div>${extraStudents.length?`<p class="privacy">已加入：${extraStudents.map(s=>esc(s.name)).join('、')}</p>`:'<p class="privacy">调课时，别的班的学生可临时加入这节课一起考勤、交作业。</p>'}</details></section><section class="panel"><h2>座位与课堂表现</h2><p class="privacy">点击座位记录该学生的表现标签、课堂记录与做题截图，自动关联本节课；座位上有人的学生考勤自动为“已到”，座位角标显示考勤状态。</p><div class="seat-layout ws-seat-layout"><div><div class="seat-toolbar"><label>行<input type="number" id="ws-seat-rows" min="1" max="12"></label><label>列<input type="number" id="ws-seat-cols" min="1" max="12"></label><button class="primary" id="ws-seat-layout-btn">应用布局</button></div><div class="teacher-desk"><span class="desk-line"></span><div>▤ 讲台</div><span class="desk-line"></span></div><div id="ws-seat-map" class="seat-map"></div></div><section class="panel" id="ws-seat-editor"><h2 id="ws-seat-editor-title">点击座位开始记录</h2><div id="ws-seat-editor-body" style="display:none"><label>这个位置的学生<select id="ws-seat-student"></select></label><div class="field-label">课堂表现</div><div class="tag-row" id="ws-seat-tags"></div><label>课堂记录<textarea id="ws-seat-note" placeholder="例如：小组讨论主动梳理思路；练习第 3 题方法用错……"></textarea></label><label>做题情况<textarea id="ws-seat-work-note" placeholder="可填写做题情况，也可以在这个框里直接 Ctrl + V 粘贴做题截图"></textarea></label><div id="ws-seat-image-preview"></div><button type="button" id="ws-seat-browse">没有截图？点击选择图片文件</button><input type="file" id="ws-paste-file" accept="image/*" hidden><button class="primary" id="ws-seat-save">保存记录</button></div></section></div></section>`;
 // 考勤：分段按钮逐条保存；调课生可移除
 $('#ws-body').querySelectorAll('.att-row').forEach(row=>{
  row.querySelectorAll('.att-seg button').forEach(b=>b.onclick=async()=>{
   try{await api(`/api/lessons/${ws.lesson.id}/attendance`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({records:[{studentId:row.dataset.sid,status:b.dataset.k}]})});await reloadWs();toast('考勤已更新')}catch(e){toast(e.message)}
  });
  const del=row.querySelector('.att-extra-del');if(del)del.onclick=async()=>{if(!confirm('把这个调课学生从本节课移除？（其本节课的考勤记录会一并删除）'))return;try{await api(`/api/lessons/${ws.lesson.id}/extra-students`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({studentId:row.dataset.sid,action:'remove'})});await reloadWs();toast('已移除')}catch(e){toast(e.message)}};
 });
 const extraBtn=$('#extra-add-btn');if(extraBtn)extraBtn.onclick=async()=>{const sid=$('#extra-student-sel').value;if(!sid){toast('先选择要加入的外班学生');return}try{await api(`/api/lessons/${ws.lesson.id}/extra-students`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({studentId:sid,action:'add'})});await reloadWs();toast('已加入本节课')}catch(e){toast(e.message)}};
 $('#att-reset').onclick=async()=>{if(!confirm('把全班考勤重置为“全部缺勤”？（之后可重新通过安排座位标记已到）'))return;try{await api(`/api/lessons/${ws.lesson.id}/attendance`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({records:ws.students.map(s=>({studentId:s.id,status:'absent'}))})});await reloadWs();toast('已重置为全部缺勤')}catch(e){toast(e.message)}};
 const end=$('#ws-end-class');if(end)end.onclick=async()=>{try{await api(`/api/lessons/${ws.lesson.id}/status`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:'waiting_homework'})});wsTab='homework';await reloadWs();toast('已开始收作业，去②课后作业上传')}catch(e){toast(e.message)}};
 renderWsSeats();
}

// ---- 工作台内嵌座位图（参数化，自动关联本节课）----
let wsSeat=null, wsSeatDraft='';
function wsSeatAt(r,c){return (ws.seats||[]).find(s=>s.row===r&&s.col===c)}
function wsPerfAt(r,c){return (ws.performances||[]).find(p=>p.row===r&&p.col===c)}
function wsAttOf(sid){const a=(ws.attendance||[]).find(a=>a.studentId===sid);return a?a.status:ATT_DEFAULT}
function renderWsSeats(){
 const cls=ws.class||{};
 const rowsEl=$('#ws-seat-rows'),colsEl=$('#ws-seat-cols');
 if(!cls.id){$('#ws-seat-map').innerHTML='<div class="table-empty">班级信息缺失。</div>';return}
 if(document.activeElement!==rowsEl)rowsEl.value=cls.seatRows||4;
 if(document.activeElement!==colsEl)colsEl.value=cls.seatCols||6;
 const rows=cls.seatRows||4, cols=cls.seatCols||6;
 $('#ws-seat-map').innerHTML=Array.from({length:rows},(_,i)=>i+1).map(r=>`<div class="seat-row" style="grid-template-columns:repeat(${cols},1fr)">`+Array.from({length:cols},(_,j)=>j+1).map(c=>{const seat=wsSeatAt(r,c),perf=wsPerfAt(r,c),stu=seat&&ws.students.find(s=>s.id===seat.studentId),hasPerf=perf&&(perf.tags?.length||perf.note||perf.image),att=stu?wsAttOf(stu.id):'';return `<button class="seat-cell${stu?'':' empty'}${hasPerf?' has-perf':''}${wsSeat&&wsSeat.r===r&&wsSeat.c===c?' selected':''}" data-r="${r}" data-c="${c}"><span class="seat-idx">${String((r-1)*cols+c).padStart(2,'0')}</span><span class="seat-who">${stu?esc(stu.name):'空位'}</span>${att&&att!=='present'?`<span class="seat-att">${ATT_SHORT[att]}</span>`:''}<span class="seat-flag">${hasPerf?esc(perf.tags?.[0]||'已记录'):''}</span></button>`}).join('')+'</div>').join('');
 $('#ws-seat-map').querySelectorAll('.seat-cell').forEach(b=>b.onclick=()=>{wsSeat={r:+b.dataset.r,c:+b.dataset.c};wsSeatDraft='';renderWsSeats();renderWsSeatEditor()});
 $('#ws-seat-layout-btn').onclick=async()=>{const nr=+rowsEl.value,nc=+colsEl.value;if(nr<(cls.seatRows||4)||nc<(cls.seatCols||6)){if(!confirm('缩小布局会清掉超出范围的座位安排，继续吗？'))return}try{await api('/api/seat-layout',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({classId:ws.lesson.classId,rows:nr,cols:nc})});wsSeat=null;$('#ws-seat-editor-body').style.display='none';$('#ws-seat-editor-title').textContent='点击座位开始记录';await reloadWs();toast('座位布局已保存')}catch(e){toast(e.message)}};
 if(wsSeat)renderWsSeatEditor();
}
function renderWsSeatEditor(){
 const {r,c}=wsSeat;$('#ws-seat-editor-title').textContent=`第 ${r} 排 · 第 ${c} 列`;$('#ws-seat-editor-body').style.display='grid';
 const seat=wsSeatAt(r,c),perf=wsPerfAt(r,c),tags=perf?.tags||[];
 const sel=$('#ws-seat-student');sel.innerHTML='<option value="">未安排</option>'+ws.students.map(s=>`<option value="${s.id}">${esc(s.name)}${s.classId!==ws.lesson.classId?'（调课）':''}</option>`).join('');sel.value=seat?.studentId||'';
 sel.onchange=async()=>{try{await api('/api/seats',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({classId:ws.lesson.classId,row:r,col:c,studentId:sel.value})});await reloadWs();toast('座位已更新')}catch(e){toast(e.message)}};
 $('#ws-seat-tags').innerHTML=SEAT_TAGS.map(t=>`<button type="button" class="${tags.includes(t)?'active':''}" data-tag="${t}">${t}</button>`).join('');
 $('#ws-seat-tags').querySelectorAll('button').forEach(b=>b.onclick=()=>b.classList.toggle('active'));
 $('#ws-seat-note').value=perf?.note||'';
 $('#ws-seat-work-note').value=perf?.workNote||'';
 renderWsSeatImage(perf?.image||'');
}
function renderWsSeatImage(img){$('#ws-seat-image-preview').innerHTML=img?`<div class="img-wrap"><img src="${img}"><button type="button" class="danger" id="ws-seat-image-del">删除</button></div>`:'';if(img)$('#ws-seat-image-del').onclick=()=>{wsSeatDraft='__clear__';renderWsSeatImage('')}}
async function uploadWsSeatImage(f){try{if(f.size>5*1024*1024)throw Error('图片超过 5MB');const src=await new Promise((ok,no)=>{const r=new FileReader;r.onload=()=>ok(r.result);r.onerror=no;r.readAsDataURL(f)});const res=await api('/api/upload',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({data:src})});wsSeatDraft=res.path;renderWsSeatImage(res.path);toast('截图已添加，点“保存记录”后生效')}catch(e){toast(e.message)}}
// 座位编辑器事件（委托到 #ws-body，因①页每次重渲染）
document.addEventListener('click',e=>{
 if(e.target.id==='ws-seat-browse')$('#ws-paste-file').click();
 if(e.target.id==='ws-seat-save'){(async()=>{if(!wsSeat)return;const {r,c}=wsSeat;const body={classId:ws.lesson.classId,assignmentId:ws.assignment?.id||'',row:r,col:c,studentId:$('#ws-seat-student').value,tags:[...$('#ws-seat-tags').querySelectorAll('button.active')].map(b=>b.dataset.tag),note:$('#ws-seat-note').value,workNote:$('#ws-seat-work-note').value};if(wsSeatDraft==='__clear__')body.clearImage=true;else if(wsSeatDraft)body.image=wsSeatDraft;try{await api('/api/performances',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});wsSeatDraft='';await reloadWs();toast('课堂记录已保存')}catch(err){toast(err.message)}})()}
});
document.addEventListener('change',e=>{if(e.target.id==='ws-paste-file'){const f=e.target.files[0];if(f){uploadWsSeatImage(f);e.target.value=''}}});
document.addEventListener('paste',e=>{if(e.target.id==='ws-seat-work-note'){const f=[...(e.clipboardData?.files||[])].find(x=>x.type.startsWith('image/'));if(f){e.preventDefault();uploadWsSeatImage(f)}}});

// ② 课后作业：按学生上传 / 追加 / 分析 / 整课次分析
function renderWsHomework(){
 const subs={};for(const s of ws.submissions)subs[s.studentId]=s;
 const attMap={};for(const a of ws.attendance)attMap[a.studentId]=a.status;
 const rank=s=>{const sub=subs[s.id];if(attMap[s.id]==='absent'||attMap[s.id]==='leave')return 3;if(sub&&sub.status==='not_required')return 3;if(!sub||!sub.images.length)return 0;if(sub.status==='completed')return 2;return 1};
 const ordered=[...ws.students].sort((a,b)=>rank(a)-rank(b));
 const rows=ordered.map(s=>{const sub=subs[s.id];const st=sub?sub.status:'not_submitted';const imgs=sub?sub.images.length:0;const absent=attMap[s.id]==='absent'||attMap[s.id]==='leave';
  return `<div class="hw-row${absent?' hw-off':''}" data-sid="${s.id}"><span class="att-name">${esc(s.name)}</span><span class="ws-badge sub-${st}">${absent?'缺勤/请假':SUB_STATUS[st]||st}</span><span class="muted hw-imgs" ${imgs?`title="点击查看作业图"`:''}>${imgs?imgs+' 张图':''}</span><input type="file" accept="image/*" multiple class="hw-files"><button class="hw-upload">上传</button><button class="hw-paste" title="也可直接在输入框 Ctrl+V 粘贴截图">粘贴</button><button class="hw-analyze" ${imgs?'':'disabled'}>AI 分析</button><span class="hw-msg"></span></div>`}).join('');
 $('#ws-body').innerHTML=`<section class="panel"><div class="hw-toolbar"><h2>课后作业</h2><label class="muted"><input type="checkbox" id="hw-continuous"> 连续录入（上传后跳下一位）</label><button class="primary" id="hw-analyze-all">✦ 分析本课次全部待分析作业</button></div><p class="privacy">先选学生再传图；可一次多张、可分批追加，也可在“选择图片”框里 Ctrl+V 直接粘贴截图。AI 只分析图片内容，不识别姓名、不改变归属。未交作业的排在最前。</p><div class="hw-list">${rows}</div></section>`;
 $('#hw-analyze-all').onclick=async()=>{const b=$('#hw-analyze-all');b.disabled=true;b.textContent='分析中…';try{const r=await api(`/api/lessons/${ws.lesson.id}/analyze`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});await reloadWs();toast(`分析完成 ${r.analyzed} 人${r.pending?`，${r.pending} 项待确认`:''}${r.failed.length?`，失败 ${r.failed.length} 人：${r.failed.map(f=>f.student).join('、')}`:''}`)}catch(e){toast(e.message);await reloadWs()}finally{b.disabled=false;b.textContent='✦ 分析本课次全部待分析作业'}};
 $('#ws-body').querySelectorAll('.hw-row').forEach(row=>{
  const sid=row.dataset.sid,sub=subs[s.id],msg=row.querySelector('.hw-msg');
  const doUpload=async(files)=>{
   if(!files.length){toast('请先选择或粘贴图片');return}
   try{
    row.classList.add('hw-busy');msg.textContent='上传中…';
    let n=0;const paths=[];
    for(const f of files){n++;msg.textContent=`上传中 ${n}/${files.length} 张…`;paths.push(...await uploadFiles([f]))}
    if(sub){await api(`/api/submissions/${sub.id}/images`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({images:paths})})}
    else{await api(`/api/lessons/${ws.lesson.id}/submissions`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({studentId:sid,images:paths})})}
    await reloadWs();toast('作业已上传');
    if($('#hw-continuous')?.checked){const all=[...$('#ws-body').querySelectorAll('.hw-row')];const idx=all.findIndex(r=>r.dataset.sid===sid);const next=all[idx+1];if(next){next.scrollIntoView({behavior:'smooth',block:'center'});next.querySelector('.hw-files').focus()}}
   }catch(e){msg.textContent='';row.classList.remove('hw-busy');toast(e.message)}
  };
  row.querySelector('.hw-upload').onclick=()=>doUpload([...row.querySelector('.hw-files').files]);
  row.querySelector('.hw-paste').onclick=async()=>{try{const items=await navigator.clipboard.read();const files=[];for(const it of items){for(const t of it.types){if(t.startsWith('image/'))files.push(await it.getType(t))}}if(!files.length){toast('剪贴板里没有图片，可先截图再点粘贴');return}doUpload(files)}catch(e){toast('无法读取剪贴板，请改用“选择图片”或在文件框里 Ctrl+V')}};
  row.querySelector('.hw-files').addEventListener('paste',e=>{const f=[...(e.clipboardData?.files||[])].filter(x=>x.type.startsWith('image/'));if(f.length){e.preventDefault();doUpload(f)}});
  row.querySelector('.hw-imgs').onclick=()=>{if(sub&&sub.images.length)openImgGallery(sub.images,`${esc(s.name)} 的作业`)};
  row.querySelector('.hw-analyze').onclick=async()=>{
   if(!sub)return;const b=row.querySelector('.hw-analyze');b.disabled=true;b.textContent='分析中…';msg.textContent='AI 分析中…';
   try{const r=await api(`/api/submissions/${sub.id}/analyze`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});await reloadWs();toast(`分析完成：${r.mistakes.length} 条错题${r.pending?`，${r.pending} 项待确认`:''}`)}catch(e){msg.textContent='分析失败，可重试';b.disabled=false;b.textContent='重试分析';toast(e.message);await reloadWs()}
  };
 });
}
// 简易图片查看（作业原图 / 待确认证据）
function openImgGallery(images,title){let i=0;const m=$('#img-modal');const show=()=>{$('#img-modal-title').textContent=`${title}（${i+1}/${images.length}）`;$('#img-modal-img').src=images[i];$('#img-modal-prev').style.display=images.length>1?'':'none';$('#img-modal-next').style.display=images.length>1?'':'none'};$('#img-modal-prev').onclick=()=>{i=(i-1+images.length)%images.length;show()};$('#img-modal-next').onclick=()=>{i=(i+1)%images.length;show()};show();m.classList.add('show')}

// ③ 待确认：左图右文证据对照卡
const RV_ETYPES=['知识点不理解','计算错误','审题错误','步骤不完整','答案表达错误','未作答','其他','待老师确认'];
function renderWsReview(){
 const items=ws.mistakes.filter(m=>m.status==='pending');
 const head=items.length?`<section class="panel"><div class="hw-toolbar"><h2>待确认（${items.length}）</h2>${items.length>3?'<button id="rv-confirm-all">全部确认</button>':''}<button id="rv-merge">合并选中题目</button></div><p class="privacy">对照左侧作业原图核实 AI 的判断；改完题目、知识点或类型后点“保存修改”。</p><div class="rv-list">${items.map(m=>{const imgs=(m.images&&m.images.length?m.images:(m.image?[m.image]:[]));return `<div class="rv-item" data-mid="${m.id}"><div class="rv-img">${imgs.length?`<img src="${imgs[0]}" data-idx="0" alt="作业原图">`:'<span class="rv-noimg">无图</span>'}</div><div class="rv-main"><div class="rv-row1"><label><input type="checkbox" class="rv-cb" value="${m.id}"></label><span class="att-name">${esc(wsStudent(m.studentId)?.name||'?')}</span><span class="muted" title="${esc(m.note||'')}">置信度 ${m.confidence||'?'}</span></div><input class="rv-q" value="${esc(m.question)}"><div class="rv-row2"><input class="rv-kp" value="${esc(m.knowledgePoint)}" placeholder="知识点"><select class="rv-et">${RV_ETYPES.map(t=>`<option ${t===m.errorType?'selected':''}>${t}</option>`).join('')}</select></div>${m.note?`<div class="rv-note">AI 依据：${esc(m.note)}</div>`:''}<div class="rv-actions"><button class="rv-confirm">✓ 确认</button><button class="rv-save">保存修改</button><button class="rv-ignore">忽略</button><button class="rv-nam">不是错题</button></div></div></div>`}).join('')}</div></section>`:`<section class="panel"><h2>待确认</h2><div class="table-empty">没有待确认的分析结果。</div></section>`;
 const opts=ws.students.map(s=>`<option value="${s.id}">${esc(s.name)}</option>`).join('');
 $('#ws-body').innerHTML=head+`<section class="panel"><details class="rv-manual"><summary>手动补充一条错题（本节课）</summary><form id="rv-manual-form"><label>学生<select name="studentId" required>${opts}</select></label><label>题目描述<textarea name="question" placeholder="例如：第3题：3/4 + 1/5"></textarea></label><label>知识点<input name="knowledgePoint" placeholder="例如：分数加减法" required></label><label>错误类型<select name="errorType">${RV_ETYPES.slice(0,7).map(t=>`<option>${t}</option>`).join('')}</select></label><label>备注<textarea name="note" placeholder="可填写老师的补充说明"></textarea></label><button class="primary">保存错题</button></form></details></section>`;
 const act=async(mid,body)=>{try{await api(`/api/mistakes/${mid}/review`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});await reloadWs();toast('已处理')}catch(e){toast(e.message)}};
 $('#ws-body').querySelectorAll('.rv-item').forEach((item,idx)=>{
  const mid=item.dataset.mid,m=items[idx];
  item.querySelector('.rv-confirm').onclick=()=>act(mid,{action:'confirm'});
  item.querySelector('.rv-save').onclick=()=>act(mid,{action:'modify',question:item.querySelector('.rv-q').value,knowledgePoint:item.querySelector('.rv-kp').value,errorType:item.querySelector('.rv-et').value});
  item.querySelector('.rv-ignore').onclick=()=>act(mid,{action:'ignore'});
  item.querySelector('.rv-nam').onclick=()=>act(mid,{action:'not_a_mistake'});
  const img=item.querySelector('.rv-img img');if(img)img.onclick=()=>{const imgs=(m.images&&m.images.length?m.images:(m.image?[m.image]:[]));openImgGallery(imgs,'作业原图')};
 });
 const all=$('#rv-confirm-all');if(all)all.onclick=async()=>{if(!confirm(`把这节课的 ${items.length} 项待确认全部标记为已确认？建议先抽查几条。`))return;try{const r=await api('/api/mistakes/review-all',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lessonId:ws.lesson.id})});await reloadWs();toast(`已确认 ${r.confirmed} 项`)}catch(e){toast(e.message)}};
 const merge=$('#rv-merge');if(merge)merge.onclick=async()=>{const ids=[...document.querySelectorAll('.rv-cb:checked')].map(x=>x.value);if(ids.length<2){toast('先勾选要合并的题目（至少 2 条）');return}try{await api(`/api/mistakes/${ids[0]}/review`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'merge',mergeIds:ids.slice(1)})});await reloadWs();toast('已合并')}catch(e){toast(e.message)}};
 $('#rv-manual-form').addEventListener('submit',async e=>{e.preventDefault();try{const obj=Object.fromEntries(new FormData(e.target));await api('/api/mistakes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...obj,assignmentId:ws.assignment?.id||'',status:'confirmed'})});e.target.reset();await reloadWs();toast('错题已保存')}catch(err){toast(err.message)}});
}

// ④ 班级学情
async function renderWsInsights(){
 $('#ws-body').innerHTML='<section class="panel"><h2>班级学情</h2><div class="table-empty">统计中…</div></section>';
 try{
  const r=await api(`/api/lessons/${ws.lesson.id}/insights`);
  const g=r.groups;
  $('#ws-body').innerHTML=`${r.pendingCount?`<section class="panel ins-warn">还有 <b>${r.pendingCount}</b> 项待确认，当前统计不含未确认结果。<button class="primary" id="ins-go-review">去确认 →</button></section>`:''}<section class="panel"><h2>班级学情</h2><div class="cards ins-cards"><article><span>应到</span><b>${r.attendance.total}</b><i>已到 ${r.attendance.present}｜迟到 ${r.attendance.late}｜请假 ${r.attendance.leave}｜缺勤 ${r.attendance.absent}</i></article><article><span>作业收交</span><b>${r.homework.submitted}/${r.homework.total}</b><i>已分析 ${r.homework.analyzed}</i></article><article><span>待确认</span><b>${r.pendingCount}</b><i>项</i></article></div>
  <h2>各知识点错误人数</h2>${r.knowledgePoints.length?`<div class="bar-chart">${r.knowledgePoints.map(k=>`<div class="bar-row ins-drill" data-kp="${esc(k.knowledgePoint)}" title="点击查看涉及学生"><span>${esc(k.knowledgePoint)}</span><div class="bar"><i style="width:${Math.min(100,k.students*20)}%"></i></div><b>${k.students} 人</b></div>`).join('')}</div>`:'<div class="table-empty">暂无已确认错题</div>'}
  <h2>高频错题</h2>${r.topQuestions.length?`<div class="freq-list">${r.topQuestions.map(q=>`<div class="freq-row ins-drill" data-q="${esc(q.question)}" title="点击查看涉及学生"><span>${esc(q.question)}</span><b>${q.count} 人错</b></div>`).join('')}</div>`:'<div class="table-empty">暂无</div>'}
  <h2>错误类型分布</h2>${r.errorTypes.length?r.errorTypes.map(t=>`<div class="freq-row"><span>${esc(t.errorType)}</span><b>${t.count}</b></div>`).join(''):'<div class="table-empty">暂无</div>'}
  <h2>需要重点关注的学生</h2>${r.focusStudents.length?r.focusStudents.map(f=>`<div class="freq-row"><span>${esc(f.name)}</span><b>${f.mistakes} 道错题</b></div>`).join(''):'<div class="table-empty">暂无</div>'}
  <h2>不计入掌握统计的情况（单独列出）</h2><div class="ins-groups">${[['缺勤/请假',g.absent],['未交作业',g.not_submitted],['分析中',g.analyzing],['无需提交',g.not_required],['已分析·无错题',g.no_mistake]].map(([t,arr])=>`<div class="ins-group"><b>${t}（${arr.length}）</b><span>${arr.map(esc).join('、')||'—'}</span></div>`).join('')}</div></section>`;
  const go=$('#ins-go-review');if(go)go.onclick=()=>{wsTab='review';renderWs()};
  // 证据下钻：知识点 / 高频错题 → 涉及学生与明细
  $('#ws-body').querySelectorAll('.ins-drill').forEach(el=>el.onclick=()=>{
   const kp=el.dataset.kp,q=el.dataset.q;
   const ms=ws.mistakes.filter(m=>m.status==='confirmed'&&(kp?m.knowledgePoint===kp:m.question===q));
   $('#md-modal-name').textContent=kp?`知识点「${kp}」涉及 ${new Set(ms.map(m=>m.studentId)).size} 人`:`高频错题（${ms.length} 条）`;
   $('#md-modal-content').innerHTML=`<div class="row-list">${ms.map(m=>`<div class="item"><div><b>${esc(m.question)}</b><div class="meta">${esc(wsStudent(m.studentId)?.name||'?')} · ${esc(m.knowledgePoint)} · ${esc(m.errorType)}${m.note?'<br>'+esc(m.note):''}</div></div></div>`).join('')||'暂无'}</div>`;
   $('#md-modal-dl').style.display='none';
   $('#md-modal').classList.add('show');
  });
 }catch(e){$('#ws-body').innerHTML=`<section class="panel"><div class="table-empty">加载失败：${esc(e.message)}</div></section>`}
}

// ⑤ 学生反馈
function renderWsFeedback(){
 $('#ws-body').innerHTML=`<section class="panel"><h2>学生反馈</h2><div class="fb-bar"><label>学生<select id="fb-student">${ws.students.map(s=>`<option value="${s.id}">${esc(s.name)}</option>`).join('')}</select></label><button class="primary" id="fb-gen">✦ 生成反馈</button></div><label>老师补充（可选）<textarea id="fb-extra" placeholder="补充这节课该生的表现，Agent 会润色"></textarea></label><div id="fb-result"></div></section>`;
 $('#fb-gen').onclick=async()=>{const b=$('#fb-gen');b.disabled=true;b.textContent='生成中…';try{const r=await api(`/api/lessons/${ws.lesson.id}/students/${$('#fb-student').value}/feedback`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({extra:$('#fb-extra').value.trim()})});
  const isTemplate=!r.file;
  $('#fb-result').innerHTML=`${isTemplate?'<p class="fb-flag">此反馈由系统根据考勤与作业状态自动生成（材料不足，未调用 AI）。</p>':'<p class="fb-flag fb-flag-ai">此反馈由 AI 根据课堂记录与作业图片生成。</p>'}<p><button class="dl-btn" id="fb-copy">复制全文</button> ${r.file?`<a class="dl-btn" href="${encodeURI(r.file.url)}" download="${esc(r.file.name)}">⬇ 下载 ${esc(r.file.name)}</a>`:''} <button class="dl-btn" id="fb-save-edit">另存我的修改</button></p><div class="report-md${isTemplate?' report-template':''}">${renderMd(r.markdown,'/agent输出/')}</div><label>编辑（不影响已保存文件）<textarea id="fb-edit">${esc(r.markdown)}</textarea></label><p id="fb-saved"></p>`;
  $('#fb-copy').onclick=()=>{navigator.clipboard.writeText($('#fb-edit').value).then(()=>toast('已复制'))};
  $('#fb-save-edit').onclick=async()=>{try{const rr=await api(`/api/lessons/${ws.lesson.id}/students/${$('#fb-student').value}/feedback`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({saveEdited:true,markdown:$('#fb-edit').value})});$('#fb-saved').innerHTML=`已另存修订版：<a class="dl-btn" href="${encodeURI(rr.file.url)}" download="${esc(rr.file.name)}">⬇ ${esc(rr.file.name)}</a>`;toast('修订版已保存')}catch(e){toast(e.message)}};
 }catch(e){toast(e.message)}finally{b.disabled=false;b.textContent='✦ 生成反馈'}};
}

$('#home-lesson-form').addEventListener('submit',async e=>{e.preventDefault();const classId=$('#home-class').value;if(!classId){toast('请选择班级');return}const btn=$('#home-create');const body={classId,date:$('#home-date').value,period:$('#home-period').value,title:$('#home-title').value.trim()};btn.disabled=true;try{const r=await api('/api/lessons',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});$('#home-title').value='';toast('课次已创建');openWorkspace(r.lesson.id)}catch(err){if(err.conflict){const x=err.conflict.existing||{};toast(`${x.date||'当天'}第${x.period||'?'}节已有课，同一时间段不能再开课`)}else{toast(err.message)}}finally{btn.disabled=false}});
$('#home-class').addEventListener('change',suggestPeriod);
$('#home-date').addEventListener('change',suggestPeriod);
$('#ws-back').onclick=async()=>{gotoPage('home');$('#page-title').textContent='课次';$('#subtitle').textContent='围绕一节课完成全部教学工作';await loadHome()};
$('#ws-status-set').onchange=async()=>{try{await api(`/api/lessons/${ws.lesson.id}/status`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:$('#ws-status-set').value})});await reloadWs();toast('状态已更新')}catch(e){toast(e.message)}};
$('#ws-tabs').querySelectorAll('button').forEach(b=>b.onclick=()=>{wsTab=b.dataset.tab;$('#ws-tabs').querySelectorAll('button').forEach(x=>x.classList.toggle('active',x===b));renderWs()});
async function init(){if(!token){showAuth();return}try{const me=await api('/api/auth/me');setSession(token,me.user);await refresh()}catch(e){if(token)toast('无法连接本地服务：'+e.message)}}
init();
