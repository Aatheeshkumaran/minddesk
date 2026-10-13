"""Patches app/static/index.html:
  1. removes the OLD pet (HTML + JS + old 3D script) that conflicted with the new pet.js
  2. makes chat use streaming (/api/chat/stream) with a timeout, so input ALWAYS unlocks
  3. connects the new Kiko pet (thinking face while the model replies)
Run from the project folder:   python patch_index.py
A backup is saved as index.html.bak. Nothing is changed if any marker is missing."""
import re, sys, shutil
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else "app/static/index.html")
if not path.exists():
    sys.exit(f"Can't find {path}. Run this from the folder that contains the 'app' folder.")
with open(path, encoding="utf-8", newline="") as fh:
    src = fh.read()
if "askModel(" in src:
    sys.exit("Already patched - nothing to do.")
t = src

def need(cond, msg):
    if not cond:
        sys.exit("NOT PATCHED (file left unchanged): " + msg)

# 1a. old pet + old pet-chat HTML
a = t.find('<div class="pet" id="pet"'); b = t.find('<div class="auth-screen" id="authScreen">')
need(a != -1 and b != -1 and a < b, "old pet HTML not found")
t = t[:a] + t[b:]

# 1b. old 3D pet module script
t, n = re.subn(r'<script type="module">\s*import \* as THREE from .*?</script>\s*', '', t, count=1, flags=re.S)
need(n == 1, "old 3D pet script not found")

# 1c. old pet JS -> small safe shim that talks to the new pet
a = t.find("function petSay("); m = t.find("window.addEventListener('resize',()=>movePetTo(")
need(a != -1 and m != -1 and a < m, "old pet JS not found")
b = t.find("\n", m)
shim = r'''/* Kiko (pet.js) hooks - safe even if the pet hasn't loaded yet */
function petSay(message){try{window.MindDeskPet&&window.MindDeskPet.say(message)}catch(e){}}
function petThinking(on){try{window.MindDeskPet&&window.MindDeskPet.setThinking(on)}catch(e){}}'''
t = t[:a] + shim + t[b:]

# 2. streaming chat with timeout + guaranteed unlock
a = t.find("petSay('Thinking…','sending');")
fin = "finally{input.disabled=false;$('sendBtn').disabled=false;input.focus();$('content').scrollTop=$('content').scrollHeight;}"
f = t.find(fin, a)
need(a != -1 and f != -1, "sendMessage block not found")
new_send = r'''petThinking(true);
 const msgBody=typing.querySelector('.message-body');
 try{
  const reply=await askModel(txt=>{msgBody.textContent=txt;$('content').scrollTop=$('content').scrollHeight;});
  msgBody.textContent=reply;chatMessages.push({role:'assistant',content:reply});syncActiveChat();setStatus(true,'Ollama connected');petThinking(false);
 }catch(e){
  msgBody.textContent='I couldn’t get a response: '+e.message+'\n\nCheck that Ollama is running and that you have a model installed.';
  setStatus(false,'Connection issue');petThinking(false);petSay('Hmm, something went wrong. Check the connection.');
 }
 finally{input.disabled=false;$('sendBtn').disabled=false;input.focus();$('content').scrollTop=$('content').scrollHeight;}'''
t = t[:a] + new_send + t[f + len(fin):]

ask = r'''async function askModel(onDelta){
 const ctrl=new AbortController();const timer=setTimeout(()=>ctrl.abort(),180000);
 try{
  const r=await fetch('/api/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({messages:chatMessages,model:$('modelSelect').value||null}),signal:ctrl.signal});
  if(!r.ok){let d='The request failed.';try{d=(await r.json()).detail||d}catch(e){}throw new Error(d);}
  const reader=r.body.getReader(),dec=new TextDecoder();let buf='',full='';
  for(;;){
   const {done,value}=await reader.read();if(done)break;
   buf+=dec.decode(value,{stream:true});let i;
   while((i=buf.indexOf('\n'))>=0){
    const line=buf.slice(0,i).trim();buf=buf.slice(i+1);if(!line)continue;
    let m;try{m=JSON.parse(line)}catch(e){continue}
    if(m.error)throw new Error(m.error);
    if(m.delta){full+=m.delta;onDelta(full);}
   }
  }
  if(!full.trim())throw new Error('The model returned an empty reply. Try again.');
  return full;
 }catch(e){
  if(e.name==='AbortError')throw new Error('The model took too long to respond.');
  throw e;
 }finally{clearTimeout(timer);}
}
'''
a = t.find("async function sendMessage(text){")
need(a != -1, "sendMessage function not found")
t = t[:a] + ask + t[a:]

shutil.copyfile(path, str(path) + ".bak")
with open(path, "w", encoding="utf-8", newline="") as fh:
    fh.write(t)
print("Patched OK. Backup: " + str(path) + ".bak  -> now press Ctrl+F5 in the browser.")
