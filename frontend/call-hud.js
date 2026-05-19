// CLAWCALL CALL HUD v3 — UI only (WebRTC logic in main script)
(function() {
  'use strict';
  console.log('[HUD] Loading...');

  var activeSession = null;
  var timerInt = null;
  var startTime = null;

  // Inject HUD HTML
  var hudHTML = '<div class="call-hud" id="callHud">'+
    '<div class="call-hud-spinner">'+
    '<div class="ring"></div><div class="ring pulse"></div><div class="ring spin"></div><div class="ring spin2"></div>'+
    '<div class="icon"><svg viewBox="0 0 24 24" fill="none" stroke="#22C55E" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/></svg></div>'+
    '</div>'+
    '<div class="call-hud-timer" id="hudTimer">00:00</div>'+
    '<div class="call-hud-status" id="hudStatus">CONNECTING</div>'+
    '<div class="call-hud-stats">'+
    '<div class="call-hud-stat"><div class="call-hud-stat-label">Cost</div><div class="call-hud-stat-value" id="hudCost">$0.00</div></div>'+
    '<div class="call-hud-stat"><div class="call-hud-stat-label">Rate</div><div class="call-hud-stat-value" id="hudRate">$0.50/min</div></div>'+
    '<div class="call-hud-stat"><div class="call-hud-stat-label">Balance</div><div class="call-hud-stat-value" id="hudBalance">--</div></div>'+
    '</div>'+
    '<div class="call-hud-numbers"><div>CID: <span id="hudCID">--</span></div><div>TO: <span id="hudTarget">--</span></div></div>'+
    '<button class="call-hud-hangup" id="hudHangup">'+
    '<svg viewBox="0 0 24 24"><path d="M10.68 13.32a15.3 15.3 0 0 0 2.64 2.64l4.55-4.55a1.5 1.5 0 0 1 1.7-.3l4.1 1.7a1.5 1.5 0 0 1 .9 1.45v5.15a1.5 1.5 0 0 1-1.5 1.5C13.27 21.91 2.09 10.73 2.09 1.5A1.5 1.5 0 0 1 3.59 0h5.15a1.5 1.5 0 0 1 1.45.9l1.7 4.1a1.5 1.5 0 0 1-.3 1.7L8.68 9.68a15.3 15.3 0 0 0 2 2"/></svg>'+
    '</button>'+
    '</div>'+
    // audio element is in main index.html

  // HUD div now in index.html — no injection needed
  // Bind hangup button handler
  (function bindHangup() {
    var hb = document.getElementById('hudHangup');
    if (hb) hb.addEventListener('click', function() {
      if (typeof window.hangupCall === 'function') window.hangupCall();
    });
    else console.error('[HUD] hangup button not found in DOM');
  })();

  function formatNum(n) {
    if (!n) return '--';
    var d = (''+n).replace(/\D/g,'');
    if (d.length===11 && d[0]==='1') d = d.slice(1);
    if (d.length===10) return '('+d.slice(0,3)+') '+d.slice(3,6)+'-'+d.slice(6);
    return d;
  }

  // Expose globally
  window.showCallHud = function(target, cid) {
    console.log('[HUD] show:', target, cid);
    document.getElementById('hudTarget').textContent = formatNum(target);
    document.getElementById('hudCID').textContent = formatNum(cid);
    document.getElementById('hudStatus').textContent = 'CONNECTING';
    document.getElementById('hudTimer').textContent = '00:00';
    document.getElementById('hudCost').textContent = '$0.00';
    var bal = (typeof S !== 'undefined' && S.userBalance) ? S.userBalance : 0;
    var rate = (typeof S !== 'undefined' && S.ratePerMin) ? S.ratePerMin : 0.50;
    document.getElementById('hudBalance').textContent = '$' + Number(bal).toFixed(2);
    document.getElementById('hudRate').textContent = '$' + Number(rate).toFixed(2) + '/min';
    var hangupBtn = document.getElementById('hudHangup');
    if (hangupBtn) hangupBtn.style.display = '';
    document.getElementById('callHud').classList.add('active');
  };

  window.hideCallHud = function() {
    var h = document.getElementById('callHud');
    if (h) h.classList.remove('active');
    if (timerInt) { clearInterval(timerInt); timerInt = null; }
    var hangupBtn = document.getElementById('hudHangup');
    if (hangupBtn) hangupBtn.style.display = '';
    activeSession = null;
    window.activeSIPSession = null;
  };

  window.setHudStatus = function(status) {
    var el = document.getElementById('hudStatus');
    if (el) el.textContent = status;
  };

  window.startHudTimer = function() {
    startTime = Date.now();
    document.getElementById('hudStatus').textContent = 'ACTIVE';
    var rate = (typeof S !== 'undefined' && S.ratePerMin) ? S.ratePerMin : 0.50;
    var initBal = (typeof S !== 'undefined' && S.userBalance) ? S.userBalance : 0;
    timerInt = setInterval(function() {
      var elapsed = Math.floor((Date.now() - startTime) / 1000);
      var m = Math.floor(elapsed / 60);
      var s = elapsed % 60;
      var cost = (elapsed / 60) * rate;
      var displayCost = Math.max(rate, cost);
      document.getElementById('hudTimer').textContent = String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');
      document.getElementById('hudCost').textContent = '$' + displayCost.toFixed(2);
      var remaining = Math.max(0, initBal - displayCost);
      document.getElementById('hudBalance').textContent = '$' + remaining.toFixed(2);
    }, 500);
  };

	  window.hangupCall = function() {
	    console.log('[HUD] hangup');
	    if (window.activeSIPSession) {
	      try {
	        if (window.activeSIPSession.bye) window.activeSIPSession.bye();
	        else if (window.activeSIPSession.cancel) window.activeSIPSession.cancel();
	      } catch(e) {
	        try { if (window.activeSIPSession.cancel) window.activeSIPSession.cancel(); } catch(_) {}
	        console.error('[HUD] hangup signaling error:', e);
	      }
	    }
    // Calculate duration and report before clearing timer
    var duration = startTime ? Math.floor((Date.now() - startTime) / 1000) : 0;
    if (timerInt) { clearInterval(timerInt); timerInt = null; }
    var el = document.getElementById('hudStatus');
    if (el) el.textContent = 'CALL ENDED';
    var hangupBtn = document.getElementById('hudHangup');
    if (hangupBtn) hangupBtn.style.display = 'none';
    // Report call to backend for billing
    var destRaw = document.getElementById('hudTarget');
    var cidRaw = document.getElementById('hudCID');
    var dest = destRaw ? destRaw.textContent : '';
    var cid = cidRaw ? cidRaw.textContent : '';
    var token = (typeof localStorage !== 'undefined') ? (localStorage.getItem('clawcall_token') || '') : '';
    console.log('[HUD] Reporting call: ' + dest + ' duration=' + duration + 's');
    if (typeof window.reportCall === 'function' && dest && duration > 0) {
      window.reportCall(dest, cid, duration, 'COMPLETED', token);
    }
    if (typeof toast === 'function') toast('CALL ENDED');
    setTimeout(function() {
      window.hideCallHud();
    }, 3000);
  };

  console.log('[HUD] Module loaded - UI functions exposed');
})();
