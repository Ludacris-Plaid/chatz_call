// Fallback captcha + auth — ONLY activates if IIFE failed
(function(){
  var iifeOk = (typeof doLogin === "function" && typeof switchTab === "function");
  
  if (iifeOk) {
    // IIFE is fine — just make sure captcha buttons aren't disabled
    setTimeout(function(){
      ["login","register"].forEach(function(pfx) {
        var pb = document.getElementById(pfx + "AcPlay");
        if (pb && pb.disabled) { pb.disabled = false; pb.removeAttribute("disabled"); }
      });
    }, 200);
    console.log("IIFE OK — standalone skipped");
    return;
  }

  console.log("IIFE FAILED — activating standalone fallback");

  // ---- CAPTCHA ----
  window.AC = { CORRECT: [0,1,2,3,4], NOTES: [{id:0,freq:587.3,label:"I",color:"#22c55e"},{id:1,freq:659.3,label:"II",color:"#00d4b4"},{id:2,freq:523.3,label:"III",color:"#60a5fa"},{id:3,freq:261.6,label:"IV",color:"#c084fc"},{id:4,freq:392.0,label:"V",color:"#fbbf24"}], ctx:null,passed:false,answer:[],playing:false,activeTile:null,status:"idle",attempts:0,tiles:null };
  window.acGetCtx=function(){try{if(!AC.ctx)AC.ctx=new(window.AudioContext||window.webkitAudioContext)();if(AC.ctx.state==="suspended")AC.ctx.resume();if(AC.ctx.state==="closed")AC.ctx=new(window.AudioContext||window.webkitAudioContext)();return AC.ctx}catch(e){return null}};
  window.acPlayTone=function(f,s,d){d=d||0.45;var c=acGetCtx();if(!c)return;var o=c.createOscillator(),g=c.createGain();o.type="sine";o.frequency.value=f;o.connect(g);g.connect(c.destination);g.gain.setValueAtTime(0,s);g.gain.linearRampToValueAtTime(0.4,s+0.02);g.gain.setValueAtTime(0.4,s+d-0.05);g.gain.linearRampToValueAtTime(0,s+d);o.start(s);o.stop(s+d)};
  window.acPlay=function(){if(AC.playing||AC.passed)return;var c=acGetCtx();if(!c)return;AC.playing=true;var n=c.currentTime,so=0.05,st=0.6;AC.CORRECT.forEach(function(id,i){acPlayTone(AC.NOTES[id].freq,n+so+i*st)});AC.CORRECT.forEach(function(id,i){setTimeout(function(){acSetActive(id)},(so+i*st)*1000);setTimeout(function(){acSetActive(null)},(so+i*st)*1000+420)});setTimeout(function(){AC.playing=false;acRender()},(so+(AC.CORRECT.length-1)*st+0.45)*1000);acRender()};
  window.acSetActive=function(id){AC.activeTile=id;acRender()};
  window.acTapTile=function(nid){if(AC.status!=="idle"||AC.answer.indexOf(nid)!==-1||AC.passed)return;var c=acGetCtx();if(c)acPlayTone(AC.NOTES[nid].freq,c.currentTime,0.25);AC.answer.push(nid);acRender()};
  window.acRemoveAnswer=function(i){if(AC.status!=="idle"||AC.passed)return;AC.answer.splice(i,1);acRender()};
  window.acVerify=function(){if(AC.answer.length!==AC.CORRECT.length||AC.passed)return;var ok=true;for(var i=0;i<AC.CORRECT.length;i++){if(AC.answer[i]!==AC.CORRECT[i]){ok=false;break}}if(ok){AC.passed=true;AC.status="success";acRender()}else{AC.attempts++;AC.status="fail";acRender();setTimeout(function(){AC.status="idle";AC.answer=[];acShuffleTiles();acRender()},1600)}};
  window.acShuffleTiles=function(){var a=AC.CORRECT.slice();for(var i=a.length-1;i>0;i--){var j=Math.floor(Math.random()*(i+1));var t=a[i];a[i]=a[j];a[j]=t}AC.tiles=a};
  window.acReset=function(){AC.passed=false;AC.answer=[];AC.playing=false;AC.activeTile=null;AC.status="idle";AC.attempts=0;acShuffleTiles();acRender()};
  window.acGetPrefix=function(){var e=document.getElementById("authLogin");return e&&e.classList.contains("active")?"login":"register"};
  window.acRender=function(){var p=acGetPrefix();var inr=document.getElementById(p+"AcInner");if(!inr)return;inr.className="ac-inner"+(AC.status==="success"?" success":"")+(AC.status==="fail"?" fail":"");if(AC.passed){inr.innerHTML='<div class="ac-success-inner"><div class="ac-success-circle">\u2713</div><div class="ac-success-title">ACCESS GRANTED</div><div class="ac-success-sub">Verification complete</div></div>';return}var pb=document.getElementById(p+"AcPlay");if(pb){pb.disabled=AC.playing;if(!AC.playing)pb.removeAttribute("disabled");pb.innerHTML='<span class="ac-play-dot"></span> '+(AC.playing?"PLAYING...":"PLAY SEQUENCE")}var se=document.getElementById(p+"AcStatus");if(se){se.className="ac-status-label"+(AC.status==="fail"?" fail":"");se.textContent=AC.status==="fail"?"\u26A0  INCORRECT \u2014 LISTEN AND RETRY":"AUDIO VERIFICATION"}var ie=document.getElementById(p+"AcInstr");if(ie)ie.textContent=AC.status==="fail"?"Wrong sequence.":"Play the sequence, then tap the tones below in the same order.";var sl=document.getElementById(p+"AcSlots");if(sl){var sh="";for(var i=0;i<AC.CORRECT.length;i++){var nid=AC.answer[i];if(nid!==undefined){var note=AC.NOTES[nid],bh=12,wf="";[0.4,0.75,1,0.75,0.4].forEach(function(r,j){wf+='<rect x="'+(j*4.5)+'" y="'+(bh-bh*r)+'" width="2.5" height="'+(bh*r)+'" rx="1" fill="'+note.color+'"/>'});sh+='<div class="ac-slot-filled" style="background:'+note.color+'14;border:1px solid '+note.color+'60;box-shadow:0 0 12px '+note.color+'18" onclick="acRemoveAnswer('+i+')"><span class="ac-slot-remove">\u2715</span><svg width="24" height="'+bh+'" viewBox="0 0 24 '+bh+'">'+wf+'</svg><span class="ac-slot-label" style="color:'+note.color+'">'+note.label+'</span></div>'}else{sh+='<div class="ac-slot-empty">'+(i+1)+'</div>'}}sl.innerHTML=sh}var te=document.getElementById(p+"AcTiles");if(te){var th="";(AC.tiles||AC.CORRECT).forEach(function(id){var note=AC.NOTES[id],isA=AC.activeTile===id,isU=AC.answer.indexOf(id)!==-1;var bg=isA?note.color+"22":"#1a2535";var border=isA?"1px solid "+note.color:"1px solid rgba(255,255,255,0.13)";var shadow=isA?"0 0 20px "+note.color+"44, inset 0 0 12px "+note.color+"18":"0 4px 12px rgba(0,0,0,0.3)";var bh=isA?20:13,bc=isA?note.color:note.color+"E6",bs=isA?"0 0 6px "+note.color:"none";var lc=isA?note.color:"rgba(255,255,255,0.55)",wf="";[0.35,0.65,1,0.65,0.35].forEach(function(r,j){wf+='<rect x="'+(j*5.5+1)+'" y="'+(bh-bh*r)+'" width="3" height="'+(bh*r)+'" rx="1" fill="'+bc+'" style="box-shadow:'+bs+'"/>'});th+='<div class="ac-tile'+(isU?" ac-used":"")+'" style="background:'+bg+';border:'+border+';box-shadow:'+shadow+'"'+(isU?"":" onclick=\"acTapTile("+id+")\"")+'>'+'<svg class="ac-waveform-svg" width="30" height="'+bh+'" viewBox="0 0 30 '+bh+'">'+wf+'</svg>'+'<span class="ac-tile-label normal" style="color:'+lc+'">'+note.label+'</span></div>'});te.innerHTML=th}var vb=document.getElementById(p+"AcVerify");if(vb){var cv=AC.answer.length===AC.CORRECT.length;vb.className="ac-verify-btn "+(cv?"ready":"not-ready");vb.disabled=!cv;if(cv)vb.removeAttribute("disabled");vb.textContent=cv?"VERIFY SEQUENCE":"SELECT "+(AC.CORRECT.length-AC.answer.length)+" MORE"}var fe=document.getElementById(p+"AcFooter");if(fe)fe.textContent=AC.attempts>0?AC.attempts+" FAILED ATTEMPT"+(AC.attempts>1?"S":""):"TAP A FILLED SLOT TO REMOVE IT"};

  // ---- AUTH ----
  var Q = function(s) { return document.querySelector(s); };
  window.showAuth = function(mode) {
    var l=document.getElementById("authLogin"), r=document.getElementById("authRegister");
    if(l)l.classList.toggle("active",mode==="login");
    if(r)r.classList.toggle("active",mode==="register");
    var app=document.querySelector(".app"); if(app)app.classList.add("app-hidden");
    acReset();
  };
  window.showApp = function() {
    var l=document.getElementById("authLogin"), r=document.getElementById("authRegister");
    if(l)l.classList.remove("active"); if(r)r.classList.remove("active");
    var app=document.querySelector(".app"); if(app)app.classList.remove("app-hidden");
  };
  window.doLogin = function() {
    var u=(Q("#loginUser")||{}).value||"", p=(Q("#loginPass")||{}).value||"", err=Q("#loginError");
    if(!u||!p){if(err)err.textContent="Fill in both fields";return}
    if(!AC.passed){if(err)err.textContent="Complete the audio captcha first";return}
    if(err)err.textContent="";
    fetch("/api/auth/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:u,password:p})})
      .then(function(r){return r.json()}).then(function(d){
        if(d.ok&&d.token){localStorage.setItem("clawcall_token",d.token);localStorage.setItem("clawcall_username",d.user?d.user.username:u);showApp()}
        else{if(err)err.textContent=d.error||"Login failed"}
      }).catch(function(){if(err)err.textContent="Network error"});
  };
  window.doRegister = function() {
    var u=(Q("#registerUser")||{}).value||"", p=(Q("#registerPass")||{}).value||"", err=Q("#registerError");
    if(!u||!p){if(err)err.textContent="Fill in both fields";return}
    if(!AC.passed){if(err)err.textContent="Complete the audio captcha first";return}
    if(p.length<4){if(err)err.textContent="Password too short (min 4)";return}
    if(err)err.textContent="";
    fetch("/api/auth/register",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:u,password:p})})
      .then(function(r){return r.json()}).then(function(d){
        if(d.ok){showAuth("login");var lu=Q("#loginUser");if(lu)lu.value=u}
        else{if(err)err.textContent=d.error||"Registration failed"}
      }).catch(function(){if(err)err.textContent="Network error"});
  };

  // Init captcha
  acShuffleTiles(); acRender();

  // Bind all buttons
  ["login","register"].forEach(function(pfx){
    var pb=document.getElementById(pfx+"AcPlay"), vb=document.getElementById(pfx+"AcVerify");
    if(pb)pb.onclick=acPlay; if(vb)vb.onclick=acVerify;
  });
  var lb=document.getElementById("loginBtn"), rb=document.getElementById("registerBtn");
  var sr=document.getElementById("showRegister"), sl=document.getElementById("showLogin");
  if(lb)lb.onclick=doLogin; if(rb)rb.onclick=doRegister;
  if(sr)sr.onclick=function(e){e.preventDefault();showAuth("register")};
  if(sl)sl.onclick=function(e){e.preventDefault();showAuth("login")};
  var lp=document.getElementById("loginPass"), rp=document.getElementById("registerPass");
  if(lp)lp.onkeydown=function(e){if(e.key==="Enter")doLogin()};
  if(rp)rp.onkeydown=function(e){if(e.key==="Enter")doRegister()};
})();
