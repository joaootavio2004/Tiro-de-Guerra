/* Impressora térmica Bluetooth (ESC/POS) — compartilhado entre as páginas da
   área interna. Espera na página: elementos #pDot #pSt #pBtn, função toast(),
   e as globais CLUB, SITE, MODS e STAGE (rótulo da etapa). */
"use strict";
/* ============================ Impressora térmica (ESC/POS) ============================ */
var BT_SERVICES = ['e7810a71-73ae-499d-8c15-faa9aef0c3f2','49535343-fe7d-4ae5-8fa9-9fafd205e455',0xFF00,0x18F0,0xFF80,0xEEE0,0xFF10,0xFEE7];
var printerDev=null, printerChar=null, chunkSize=128, useNoResp=true, printing=false;
async function connectPrinter(){
  if(!navigator.bluetooth){ toast('Use o Chrome no Android para Bluetooth.', true); return; }
  try{
    printerDev = await navigator.bluetooth.requestDevice({acceptAllDevices:true, optionalServices:BT_SERVICES});
    printerDev.addEventListener('gattserverdisconnected', function(){ printerChar=null; setPrinterUI(false); });
    var server = await printerDev.gatt.connect();
    var services = await server.getPrimaryServices(), cands=[];
    for(var i=0;i<services.length;i++){ var chars=await services[i].getCharacteristics();
      for(var j=0;j<chars.length;j++){ var p=chars[j].properties; if(p.writeWithoutResponse||p.write) cands.push(chars[j]); } }
    if(!cands.length) throw new Error('sem característica de escrita');
    cands.sort(function(a,b){ function sc(c){ var s=c.service.uuid; return s.indexOf('e7810a71')===0?0:s.indexOf('49535343')===0?1:s.indexOf('0000ff00')===0?2:s.indexOf('000018f0')===0?3:9; } return sc(a)-sc(b); });
    printerChar=cands[0]; useNoResp=!!printerChar.properties.writeWithoutResponse; chunkSize=128;
    setPrinterUI(true); toast('Impressora conectada');
  }catch(e){ if(String(e).indexOf('cancelled')<0) toast('Impressora: '+e.message, true); }
}
function setPrinterUI(on){ document.getElementById('pDot').className='dot'+(on?' on':''); document.getElementById('pSt').textContent= on ? (printerDev.name||'conectada') : 'desconectada'; document.getElementById('pBtn').textContent=on?'Trocar':'Conectar'; }
function sleep(ms){ return new Promise(function(r){ setTimeout(r, ms); }); }
async function sendBytes(bytes){
  for(var attempt=0; attempt<2; attempt++){
    try{ for(var i=0;i<bytes.length;i+=chunkSize){ var c=bytes.slice(i,i+chunkSize);
        if(useNoResp) await printerChar.writeValueWithoutResponse(c); else await printerChar.writeValue(c); await sleep(useNoResp?30:5); } return; }
    catch(e){ if(attempt===0){ chunkSize=20; useNoResp=false; await sleep(300); } else throw e; }
  }
}
var ESC=0x1B, GS=0x1D;
var enc={ buf:[], raw:function(a){for(var i=0;i<a.length;i++)this.buf.push(a[i]);return this;},
  txt:function(s){ s=(s||'').normalize('NFKD').replace(/[\u0300-\u036f]/g,''); for(var i=0;i<s.length;i++){var c=s.charCodeAt(i); this.buf.push(c<32||c>126?32:c);} return this;},
  nl:function(n){for(var i=0;i<(n||1);i++)this.buf.push(10);return this;}, init:function(){return this.raw([ESC,0x40]);},
  center:function(){return this.raw([ESC,0x61,1]);}, left:function(){return this.raw([ESC,0x61,0]);},
  bold:function(on){return this.raw([ESC,0x45,on?1:0]);}, size:function(w,h){return this.raw([GS,0x21,((w-1)<<4)|(h-1)]);},
  feed:function(n){return this.raw([ESC,0x64,n]);}, hr:function(){return this.txt('-'.repeat(32)).nl();},
  kv:function(k,v){k=k||'';v=v||'';var pad=32-k.length-v.length;return this.txt(k+' '.repeat(Math.max(1,pad))+v).nl();},
  out:function(){var u=new Uint8Array(this.buf);this.buf=[];return u;} };
var LOGO=null, QR=null;
(async function(){ try{ LOGO=new Uint8Array(await (await fetch('/static/logo-thermal.bin')).arrayBuffer()); QR=new Uint8Array(await (await fetch('/static/qr-thermal.bin')).arrayBuffer()); }catch(e){} })();
function concat(l){ var n=0; l.forEach(function(a){n+=a.length;}); var o=new Uint8Array(n),p=0; l.forEach(function(a){o.set(a,p);p+=a.length;}); return o; }
function penText(a,b,c){ var p=[]; if(a)p.push(a+'x2s'); if(b)p.push(b+'x5s'); if(c)p.push(c+'x10s'); return p.length?p.join(' + '):'nenhuma'; }
function buildReceipt(r, name, stageLabel){
  var ml=(MODS.find(function(x){return x.code===r.modality;})||{}).label||r.modality;
  var parts=[enc.init().center().out()]; if(LOGO) parts.push(LOGO);
  var e=enc.center().size(2,2).bold(true).txt('TIRO DE GUERRA').nl().size(1,1).bold(false).txt(CLUB.toUpperCase()).nl().txt(stageLabel||'').nl().left().hr();
  e.center().size(1,2).bold(true); var nm=name.toUpperCase();
  if(nm.length<=32) e.txt(nm).nl(); else { var cut=nm.lastIndexOf(' ',32); if(cut<1)cut=32; e.txt(nm.slice(0,cut)).nl().txt(nm.slice(cut).trim().slice(0,32)).nl(); }
  e.size(1,1).bold(false).txt(ml.toUpperCase()+' - '+(r.category||'').toUpperCase()).nl().left().hr();
  if(r.dq){ e.center().size(2,2).bold(true).txt('DQ').nl().size(1,1).txt('DESQUALIFICADO NESTA PASSADA').nl().bold(false).left(); }
  else{ e.kv('Tempo de pista', f2(r.raw)+'s'); e.kv('Penalidades', penText(r.pen2,r.pen5,r.pen10)); if(r.pen_secs) e.kv('', '+'+r.pen_secs+'s');
    e.nl().center().txt('TEMPO FINAL').nl().size(2,2).bold(true).txt(f2(r.final)+'s').nl().size(1,1).bold(false).left(); }
  e.hr();
  if(r.n_shots){ e.kv('Disparos', String(r.n_shots)); if(r.first_shot!=null) e.kv('Primeiro disparo', f2(r.first_shot)+'s'); e.hr(); }
  e.txt('Vale a melhor passada da etapa.').nl().hr();
  parts.push(e.out()); parts.push(enc.center().out()); if(QR) parts.push(QR);
  var f=enc.center().txt(SITE.replace(/^https?:\/\//,'')).nl(); var now=new Date();
  f.txt('impresso '+now.toLocaleDateString('pt-BR')+' '+now.toTimeString().slice(0,5)).nl().feed(4).left(); parts.push(f.out());
  return concat(parts);
}
async function printRun(r, name, btn){
  if(!printerChar){ toast('Conecte a impressora primeiro.', true); return; }
  if(printing){ toast('Aguarde, imprimindo…'); return; }
  printing=true; var old=btn?btn.textContent:null; if(btn){btn.disabled=true; btn.textContent='Imprimindo…';}
  try{ await sendBytes(buildReceipt(r, name||'', (typeof STAGE!=='undefined' && STAGE) ? STAGE : (window.STAGE_LABEL||''))); toast('Cupom enviado ✔'); }
  catch(e){ toast('Erro ao imprimir: '+e.message, true); }
  finally{ printing=false; if(btn){btn.disabled=false; btn.textContent=old;} }
}

