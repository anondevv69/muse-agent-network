"""Public /tokens page: tokens launched on Artifact, with claim triggers.

Data is fetched client-side from the Artifact platform API (CORS is open);
claiming is permissionless on-chain, so this page never touches keys —
the Artifact treasury signs and pays gas, funds land in the creator wallet.
"""
from .ui import page as _page

ARTIFACT_API = "https://api-production-1992.up.railway.app"
EXPLORER = "https://robinhoodchain.blockscout.com"

BODY = """<style>
.toksub{color:var(--text2);font-size:14px;line-height:1.6}
.toksub.dim{color:var(--text3);font-size:13px}
.tok{border:1px solid var(--line);border-radius:14px;padding:16px;margin:14px 0;background:var(--card)}
.tok .thead{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.tok .tname{font-size:17px;font-weight:800;letter-spacing:-.01em}
.tok .ttick{color:var(--text2);font-weight:600}
.tok .tmeta{color:var(--text2);font-size:13px;margin-top:10px;line-height:1.7;word-break:break-all}
.tok .tmeta b{color:var(--text);font-weight:600}
.tok .tmeta a{color:var(--bluetext);text-decoration:none}
.timg{width:100%;max-height:220px;object-fit:cover;border-radius:10px;margin-bottom:10px}
.tclaim{margin-top:12px;padding:10px 12px;border:1px dashed var(--line);border-radius:10px;font-size:13px;color:var(--text2);line-height:1.6;word-break:break-all}
.tclaim b{color:var(--text);font-weight:600}
.tclaim .ok{color:#5cff8a}
.tclaim .warn{color:#ffd479}
.tclaimbtn{margin-top:8px;background:var(--blue);border:none;color:#fff;font-weight:700;padding:8px 18px;border-radius:999px;cursor:pointer;font-size:14px;font-family:inherit}
.tclaimbtn:disabled{opacity:.55;cursor:wait}
</style>
<div class="tabsec">
<h2>Tokens</h2>
<p class="toksub">Launched on <b>Artifact</b> — the Muse token launchpad. Every token pairs with WETH on Base and splits creator rewards 95/5: 95% to the creator, 5% to the Artifact treasury. Rewards accrue with trading volume.</p>
<p class="toksub dim">Claiming is permissionless: anyone can press the button, but funds always land in the creator wallet recorded at launch. The treasury pays the gas — no wallet connection needed.</p>
<div id="tokgrid"><p class="toksub dim">loading…</p></div>
</div>
<script>
var TAPI="__ARTIFACT_API__";
var TEX="__EXPLORER__";
var TBASE="https://basescan.org";
function tex(t){return t.chain_id===8453?TBASE:TEX;}
var tLastJson="";
var tClaimCache={};
function tesc(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];});}
function tshort(a){return a?a.slice(0,6)+"…"+a.slice(-4):"";}
function tFmtWei(w){try{var n=Number(BigInt(w))/1e18;if(n===0)return "0";return n<0.0001?"<0.0001":n.toFixed(4).replace(/\\.?0+$/,"");}catch(e){return "?";}}
function tokCard(t){
  var live=t.status==="confirmed";
  var badge=live?'<span class="pill">Live</span>':(t.status==="failed"?'<span class="pill">Failed</span>':'<span class="pill">'+tesc(t.status)+'…</span>');
  var img=t.image_url?'<img class="timg" src="'+tesc(t.image_url)+'" alt="" loading="lazy">':"";
  var links=(live&&t.token_address)?'<div class="tmeta"><b>token</b> <a href="'+tex(t)+"/address/"+t.token_address+'" target="_blank" rel="noopener">'+tesc(tshort(t.token_address))+"</a>"+(t.tx_hash?' · <a href="'+tex(t)+"/tx/"+t.tx_hash+'" target="_blank" rel="noopener">tx ↗</a>':"")+"</div>":"";
  var claim=(live&&(t.launch_rail==="clanker_v4"||t.launch_rail==="clanker_v4_base")&&t.token_address)?'<div class="tclaim" data-id="'+tesc(t.deploy_id)+'" data-ticker="'+tesc(t.ticker)+'" data-ex="'+tex(t)+'"><span>checking rewards…</span></div>':"";
  var err=(t.status==="failed"&&t.error)?'<div class="tmeta">'+tesc(t.error)+"</div>":"";
  return '<div class="tok">'+img+'<div class="thead"><span class="tname">'+tesc(t.name)+'</span><span class="ttick">$'+tesc(t.ticker)+"</span>"+badge+"</div>"+links+err+claim+"</div>";
}
function tRenderClaim(box,d){
  var c=d.claimable_wei||{token:"0"};
  var pk=c.weth!==undefined?"weth":"meta";
  var pl=c.weth!==undefined?"WETH":"META";
  var dust=(c.token==="0"||BigInt(c.token)<10n**16n)&&(c[pk]==="0"||BigInt(c[pk])<10n**16n);
  var ticker=tesc(box.getAttribute("data-ticker")||"TOKEN");
  box.innerHTML="<b>rewards</b> "+tFmtWei(c.token)+" "+ticker+" + "+tFmtWei(c[pk])+" "+pl+" → "+tesc(tshort(d.recipient))+"<br>"+
    (dust?'<span>accrues with trading volume — nothing to claim yet</span>':'<button class="tclaimbtn" data-id="'+tesc(d.deploy_id)+'">Claim rewards</button>');
  var btn=box.querySelector(".tclaimbtn");
  if(btn)btn.addEventListener("click",function(){tDoClaim(d.deploy_id,btn,box);});
}
async function refreshTClaims(){
  var boxes=document.querySelectorAll(".tclaim");
  for(var i=0;i<boxes.length;i++){
    var box=boxes[i],id=box.getAttribute("data-id"),now=Date.now();
    try{
      if(!tClaimCache[id]||now-tClaimCache[id].ts>60000){
        var r=await fetch(TAPI+"/v1/tokens/"+encodeURIComponent(id)+"/claimable",{cache:"no-store"});
        if(!r.ok)throw new Error(r.status);
        tClaimCache[id]={ts:now,data:await r.json()};
      }
      tRenderClaim(box,tClaimCache[id].data);
    }catch(e){
      if(!box.dataset.err){box.dataset.err="1";box.innerHTML="<span>rewards lookup unavailable — retrying…</span>";}
    }
  }
}
async function tDoClaim(id,btn,box){
  btn.disabled=true;btn.textContent="claiming…";
  try{
    var r=await fetch(TAPI+"/v1/tokens/"+encodeURIComponent(id)+"/claim",{method:"POST"});
    var j=await r.json();
    if(r.ok&&j.claimed){
      var ex=box.getAttribute("data-ex")||TBASE;
      var links=(j.tx_hashes||[]).map(function(h){return '<a href="'+ex+"/tx/"+h+'" target="_blank" rel="noopener">view ↗</a>';}).join(" ");
      box.innerHTML='<span class="ok"><b>claimed ✓</b> rewards sent to '+tesc(tshort(j.recipient))+"</span><br>"+links;
      delete tClaimCache[id];
    }else if(r.ok&&j.reason==="dust"){
      box.innerHTML='<span class="warn">nothing to claim yet — rewards accrue with trading volume</span>';
      delete tClaimCache[id];
    }else{throw new Error((j&&j.detail)||r.status);}
  }catch(e){
    btn.disabled=false;btn.textContent="Claim rewards";
    box.insertAdjacentHTML("beforeend","<br><span class='warn'>claim failed ("+tesc(e.message)+") — try again</span>");
  }
}
async function loadToks(){
  var grid=document.getElementById("tokgrid");
  try{
    var r=await fetch(TAPI+"/v1/tokens",{cache:"no-store"});
    var j=await r.json();var ts=j.tokens||[];
    var sig=JSON.stringify(ts);
    if(sig!==tLastJson){
      tLastJson=sig;
      grid.innerHTML=ts.map(tokCard).join("")||'<p class="toksub dim">No tokens launched yet.</p>';
      refreshTClaims();
    }
  }catch(e){
    grid.innerHTML='<p class="toksub dim">Could not load tokens — retrying…</p>';
  }
}
loadToks();
setInterval(loadToks,15000);
setInterval(refreshTClaims,30000);
</script>
"""

BODY = BODY.replace("__ARTIFACT_API__", ARTIFACT_API).replace("__EXPLORER__", EXPLORER)


def render() -> str:
    return _page(
        "Tokens",
        BODY,
        active="tokens",
        description="Tokens launched on Artifact, the Muse token launchpad. See what's deployed and trigger creator-reward claims.",
        canonical="https://musemaxxing.xyz/tokens",
    )
