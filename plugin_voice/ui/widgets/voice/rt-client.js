/* Luna realtime voice client — plain WebSocket against Gemini Live, no SDK
 * bundle. One object on window:
 *
 *   LunaRT.start({api, authHeaders, callbacks…}) → Promise<handle>
 *   handle.end()
 *
 * The server does all thinking (persona, tools, gating); this file only
 * moves audio and events:
 *   - GET  {api}/rt/session   → {ws_url, access_token, setup, rt_token…}
 *   - WS   ws_url?access_token= → setup sent VERBATIM (locked token
 *     constraint), then 16k s16le mic up / 24k PCM audio down
 *   - toolCall frames → POST {api}/rt/tool → toolResponse (rt_token auth)
 *   - WS   {api}/rt/events    → lane-3 task updates → [task update] turns
 *   - WS   {api}/live         → imprint tee (16k s16le PCM), verdict chip
 */
(function () {
  "use strict";

  function wsBase() {
    return (location.protocol === "https:" ? "wss://" : "ws://") + location.host;
  }

  var IN_RATE = 16000;          // mic → Gemini
  var OUT_RATE = 24000;         // Gemini → speaker
  var SEND_SAMPLES = 1600;      // ~100 ms @16k per realtimeInput chunk
  var TEE_SAMPLES = 8000;       // 500 ms @16k per imprint-tee chunk
  var SPEECH_RMS = 0.02;        // local energy gate: mic is "hot" above this
  var SPEECH_HANG_MS = 700;     // …and stays hot this long after the last peak

  function b64FromInt16(int16) {
    var bytes = new Uint8Array(int16.length * 2);
    for (var i = 0; i < int16.length; i++) {
      bytes[2 * i] = int16[i] & 255;
      bytes[2 * i + 1] = (int16[i] >> 8) & 255;
    }
    var bin = "";
    for (var j = 0; j < bytes.length; j += 8192) {
      bin += String.fromCharCode.apply(null, bytes.subarray(j, j + 8192));
    }
    return btoa(bin);
  }

  async function start(opts) {
    var api = opts.api;
    var authHeaders = opts.authHeaders || function () { return {}; };
    var onState = opts.onState || function () {};   // connecting|listening|thinking|speaking
    var onWho = opts.onWho || function () {};       // owner|other|unknown|null
    var onRemoteStream = opts.onRemoteStream || function () {};
    var onEnd = opts.onEnd || function () {};       // (reason: string|null) — null = clean
    var onReact = opts.onReact || function () {};   // Luna View reaction shape
    var onTask = opts.onTask || function () {};     // (activeCount, event) — subagent loader
    var setStatus = opts.onStatus || function () {};

    var session = null, ws = null, micStream = null;
    var capCtx = null, capNodes = [];
    var playCtx = null, playDest = null, playhead = 0, drainTimer = null;
    var activeSources = [];
    var eventsWs = null, liveWs = null;
    var ended = false, endedReason;
    var setupDone = false, everStarted = false, speaking = false;
    var userSpeaking = false, speechTimer = null;
    var pendingNudges = [];   // task events held while the user is mid-utterance
    var flushTimer = null;
    var lastVerdict = null, verdictTimer = null;
    var resumeHandle = null, reconnects = 0;
    var cancelledCalls = {};  // toolCallCancellation ids → drop the late relay

    // ---------------------------------------------------------------- session
    setStatus("Connecting…");
    var resp = await fetch(api + "/rt/session", {
      method: "GET", credentials: "include", headers: authHeaders(),
    });
    if (!resp.ok) {
      var detail = "Setup needed — see Settings → Voice";
      try {
        var d = (await resp.json()).detail;
        if (d && typeof d !== "string") { d = JSON.stringify(d); }
        detail = d || detail;
      } catch (e) {}
      if (resp.status === 401) { detail = "Session expired — reload the page"; }
      throw new Error(detail);
    }
    session = await resp.json();

    // ------------------------------------------------------------- microphone
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error("This browser can't capture microphone audio");
    }
    setStatus("Requesting microphone…");
    try {
      // The browser-side half of the noise story: echo cancellation keeps
      // Luna's own voice out of the mic, noise suppression + AGC tame fans,
      // typing and distance. Gemini's LOW-sensitivity VAD does the rest.
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (err) {
      if (err && (err.name === "NotAllowedError" || err.name === "SecurityError")) {
        throw new Error("Microphone blocked — click the 🎤/lock icon in the address bar, allow the mic, then try again");
      }
      throw new Error("Microphone unavailable: " + (err && err.name || err));
    }

    function end(reason) {
      if (ended) { return; }
      ended = true; endedReason = reason || null;
      if (flushTimer) { clearTimeout(flushTimer); }
      if (verdictTimer) { clearTimeout(verdictTimer); }
      if (speechTimer) { clearTimeout(speechTimer); }
      if (drainTimer) { clearTimeout(drainTimer); }
      capNodes.forEach(function (n) { try { n.disconnect(); } catch (e) {} });
      if (liveWs) { try { liveWs.close(); } catch (e) {} }
      if (eventsWs) { try { eventsWs.close(); } catch (e) {} }
      if (ws) { try { ws.close(); } catch (e) {} }
      if (capCtx) { try { capCtx.close(); } catch (e) {} }
      if (playCtx) { try { playCtx.close(); } catch (e) {} }
      if (micStream) {
        micStream.getTracks().forEach(function (t) { try { t.stop(); } catch (e) {} });
      }
      onWho(null);
      onEnd(endedReason);
    }

    // ---------------------------------------------------------------- playback
    // 24 kHz PCM chunks → scheduled buffer sources → a MediaStream destination,
    // so the widget keeps receiving a plain MediaStream like the WebRTC days.
    playCtx = new (window.AudioContext || window.webkitAudioContext)();
    playDest = playCtx.createMediaStreamDestination();
    onRemoteStream(playDest.stream);

    function queueAudio(b64) {
      var bin = atob(b64);
      var n = bin.length >> 1;
      if (!n) { return; }
      var buf = playCtx.createBuffer(1, n, OUT_RATE);
      var ch = buf.getChannelData(0);
      for (var i = 0; i < n; i++) {
        var v = (bin.charCodeAt(2 * i + 1) << 8) | bin.charCodeAt(2 * i);
        if (v >= 32768) { v -= 65536; }
        ch[i] = v / 32768;
      }
      var src = playCtx.createBufferSource();
      src.buffer = buf;
      src.connect(playDest);
      var t = Math.max(playCtx.currentTime, playhead);
      src.start(t);
      playhead = t + buf.duration;
      activeSources.push(src);
      src.onended = function () {
        var ix = activeSources.indexOf(src);
        if (ix >= 0) { activeSources.splice(ix, 1); }
      };
      if (!speaking) { speaking = true; onState("speaking"); }
      armDrainCheck();
    }

    function armDrainCheck() {
      if (drainTimer) { clearTimeout(drainTimer); }
      var ms = Math.max((playhead - playCtx.currentTime) * 1000 + 150, 150);
      drainTimer = setTimeout(function () {
        drainTimer = null;
        if (ended || !speaking) { return; }
        if (playCtx.currentTime >= playhead - 0.05) {
          speaking = false;
          onState("listening");
          scheduleFlush();
        } else { armDrainCheck(); }
      }, ms);
    }

    // Real barge-in: the model was interrupted — whatever is still queued is
    // stale speech. Silence it NOW.
    function flushPlayback() {
      activeSources.splice(0).forEach(function (s) { try { s.stop(); } catch (e) {} });
      playhead = 0;
      speaking = false;
    }

    // --------------------------------------------------------------- live WS
    function sendJson(obj) {
      if (ws && ws.readyState === 1 && setupDone) {
        try { ws.send(JSON.stringify(obj)); } catch (e) {}
      }
    }

    function connect() {
      ws = new WebSocket(session.ws_url + "?access_token=" + encodeURIComponent(session.access_token));
      ws.binaryType = "arraybuffer";
      ws.onopen = function () {
        // The setup must match the token's locked constraint — send it
        // verbatim. A resumption handle may ride along (probe-verified: the
        // constrained endpoint accepts it), restoring context on reconnect.
        var setup = JSON.parse(JSON.stringify(session.setup));
        if (resumeHandle) { setup.setup.sessionResumption = { handle: resumeHandle }; }
        try { ws.send(JSON.stringify(setup)); } catch (e) {}
      };
      ws.onmessage = function (ev) {
        var txt = typeof ev.data === "string" ? ev.data : new TextDecoder().decode(ev.data);
        var msg;
        try { msg = JSON.parse(txt); } catch (e) { return; }
        handleServer(msg);
      };
      ws.onerror = function () { try { ws.close(); } catch (e) {} };
      ws.onclose = function () {
        if (ended) { return; }
        setupDone = false;
        if (speaking) { flushPlayback(); }
        if (reconnects < 2) {
          reconnects++;
          setStatus("Reconnecting…");
          setTimeout(function () { if (!ended) { connect(); } }, 300 * reconnects);
        } else {
          end("Connection lost");
        }
      };
    }

    function handleServer(msg) {
      if (msg.setupComplete) {
        setupDone = true;
        reconnects = 0;
        onState("listening");
        setStatus("Listening…");
        if (!everStarted) {
          everStarted = true;
          // proactiveAudio won't necessarily speak into silence — one nudge
          // makes the talker open the call instead of waiting to be spoken to.
          contextTurn("[call started — greet your owner briefly, in character]", true);
          startTaskEvents();
          startImprintTee();
        }
        return;
      }
      if (msg.sessionResumptionUpdate) {
        var u = msg.sessionResumptionUpdate;
        if (u.resumable && u.newHandle) { resumeHandle = u.newHandle; }
        return;
      }
      if (msg.goAway) {
        // Server is about to drop us — reconnect now; onclose does the rest.
        try { ws.close(); } catch (e) {}
        return;
      }
      if (msg.toolCallCancellation) {
        (msg.toolCallCancellation.ids || []).forEach(function (id) {
          cancelledCalls[id] = true;
        });
        return;
      }
      if (msg.toolCall) {
        (msg.toolCall.functionCalls || []).forEach(relayCall);
        return;
      }
      var sc = msg.serverContent;
      if (!sc) { return; }
      if (sc.interrupted) {
        flushPlayback();
        if (!ended) { onState("listening"); }
        return;
      }
      var parts = (sc.modelTurn && sc.modelTurn.parts) || [];
      parts.forEach(function (p) {
        if (p.inlineData && p.inlineData.data) { queueAudio(p.inlineData.data); }
      });
      if (sc.turnComplete && !speaking && !ended) {
        onState("listening");
        scheduleFlush();
      }
    }

    // ----------------------------------------------------------------- tools
    async function relayCall(call) {
      var args = call.args || {};
      // luna_view_react is a UI-only tool: it never leaves the browser — the
      // reaction goes straight to the Luna View renderer, zero latency. The
      // response is submitted so the model knows it landed; no extra spoken
      // turn ("never announce it").
      if (call.name === "luna_view_react") {
        onReact(args.shape || "");
        sendJson({ toolResponse: { functionResponses: [
          { id: call.id, name: call.name, response: { ok: true } },
        ] } });
        return;
      }
      onState("thinking");
      var out;
      try {
        var r = await fetch(api + "/rt/tool", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: session.rt_token, name: call.name, arguments: args }),
        });
        out = await r.json();
      } catch (e) {
        out = { ok: false, error: "That didn't go through — the connection hiccuped." };
      }
      if (ended) { return; }
      if (cancelledCalls[call.id]) { delete cancelledCalls[call.id]; return; }
      sendJson({ toolResponse: { functionResponses: [
        { id: call.id, name: call.name, response: out },
      ] } });
    }

    // ----------------------------------------------------------- nudges/turns
    // Context lines ride in as clientContent turns. turnComplete only when a
    // spoken announcement is wanted — otherwise the line just lands as
    // context for the next real exchange.
    function contextTurn(text, speakNow) {
      sendJson({ clientContent: {
        turns: [{ role: "user", parts: [{ text: text }] }],
        turnComplete: !!speakNow,
      } });
    }

    // -------------------------------------------------------- lane-3 events →
    // Task updates become conversation context. Completions nudge the talker
    // to announce them; a nudge never lands mid-utterance (queued, flushed
    // when the local energy gate says the mic has gone quiet).
    var lastSeq = 0;
    var activeTasks = {};   // task_id → true while a subagent is working

    function noteTask(e2) {
      if (e2.type === "task_started") { activeTasks[e2.task_id] = true; }
      else if (e2.type === "task_done" || e2.type === "task_failed") { delete activeTasks[e2.task_id]; }
      else { return; }
      var n = 0;
      for (var k in activeTasks) { if (activeTasks.hasOwnProperty(k)) { n++; } }
      onTask(n, e2);
    }

    function startTaskEvents() {
      try {
        eventsWs = new WebSocket(
          wsBase() + api + "/rt/events?token=" + encodeURIComponent(session.rt_token) +
          "&since=" + lastSeq
        );
      } catch (e) { return; }
      eventsWs.onmessage = function (ev) {
        var e2;
        try { e2 = JSON.parse(ev.data); } catch (err) { return; }
        if (typeof e2.seq === "number") { lastSeq = e2.seq; }
        noteTask(e2);
        pendingNudges.push(e2);
        scheduleFlush();
      };
      eventsWs.onclose = function () {
        if (ended) { return; }
        // Task updates matter — reconnect quietly, resuming from lastSeq.
        setTimeout(function () { if (!ended) { startTaskEvents(); } }, 2000);
      };
      eventsWs.onerror = function () { try { eventsWs.close(); } catch (e) {} };
    }

    function scheduleFlush() {
      if (flushTimer || !pendingNudges.length) { return; }
      flushTimer = setTimeout(function () {
        flushTimer = null;
        if (ended) { return; }
        if (userSpeaking || speaking) { return; }  // re-armed on quiet/drain
        var evts = pendingNudges.splice(0);
        var speak = false;
        var lines = [];
        evts.forEach(function (e2) {
          if (e2.type === "task_done") {
            lines.push("[task update] Finished (" + e2.elapsed_s + "s): " + (e2.spoken_summary || e2.instruction));
            speak = true;
          } else if (e2.type === "task_failed") {
            lines.push("[task update] Failed: " + (e2.spoken_summary || e2.instruction));
            speak = true;
          } else if (e2.type === "task_started") {
            // Context only — the talker already acknowledged the dispatch.
            lines.push("[task update] Started working on: " + e2.instruction);
          }
        });
        if (lines.length) { contextTurn(lines.join("\n"), speak); }
      }, 600);
    }

    // ------------------------------------------------------------ mic capture
    // ONE capture path: AudioWorklet (ScriptProcessor fallback) → 16 kHz s16le
    // frames → both the Gemini realtimeInput uplink and the /live imprint tee.
    var sendAcc = [], teeAcc = [];

    function onFrames(f32, srcRate) {
      // local energy gate (userSpeaking) — nudge timing only, VAD is Gemini's
      var s = 0, cnt = 0;
      for (var e = 0; e < f32.length; e += 8) { s += f32[e] * f32[e]; cnt++; }
      if (cnt && Math.sqrt(s / cnt) > SPEECH_RMS) {
        userSpeaking = true;
        if (speechTimer) { clearTimeout(speechTimer); }
        speechTimer = setTimeout(function () {
          userSpeaking = false;
          scheduleFlush();
        }, SPEECH_HANG_MS);
      }
      var ratio = srcRate / IN_RATE;
      for (var i = 0; i < f32.length; i += ratio) {
        var v = f32[Math.floor(i)];
        var s16 = Math.max(-32768, Math.min(32767, v * 32768)) | 0;
        sendAcc.push(s16);
        teeAcc.push(s16);
      }
      while (sendAcc.length >= SEND_SAMPLES) {
        var chunk = sendAcc.splice(0, SEND_SAMPLES);
        sendJson({ realtimeInput: { audio: {
          data: b64FromInt16(chunk),
          mimeType: "audio/pcm;rate=" + IN_RATE,
        } } });
      }
      if (teeAcc.length >= TEE_SAMPLES && liveWs && liveWs.readyState === 1) {
        liveWs.send(JSON.stringify({ pcm_b64: b64FromInt16(teeAcc.splice(0)) }));
      } else if (teeAcc.length >= TEE_SAMPLES) {
        teeAcc.length = 0;  // tee off/behind — don't hoard audio
      }
    }

    async function startCapture() {
      capCtx = new (window.AudioContext || window.webkitAudioContext)();
      var capSrc = capCtx.createMediaStreamSource(micStream);
      var mute = capCtx.createGain();
      mute.gain.value = 0;   // keep the graph pulled without local echo
      mute.connect(capCtx.destination);
      var rate = capCtx.sampleRate;
      try {
        var workletCode =
          "class LunaCap extends AudioWorkletProcessor{" +
          "process(inputs){var c=inputs[0][0];if(c){this.port.postMessage(c.slice(0));}return true;}}" +
          "registerProcessor('luna-cap',LunaCap);";
        await capCtx.audioWorklet.addModule(
          URL.createObjectURL(new Blob([workletCode], { type: "application/javascript" }))
        );
        var wnode = new AudioWorkletNode(capCtx, "luna-cap");
        wnode.port.onmessage = function (ev) {
          if (!ended) { onFrames(ev.data, rate); }
        };
        capSrc.connect(wnode);
        wnode.connect(mute);
        capNodes.push(capSrc, wnode, mute);
      } catch (e) {
        // Old browsers: ScriptProcessor still works, just on the main thread.
        var snode = capCtx.createScriptProcessor(4096, 1, 1);
        snode.onaudioprocess = function (ev) {
          if (!ended) { onFrames(ev.inputBuffer.getChannelData(0), rate); }
        };
        capSrc.connect(snode);
        snode.connect(mute);
        capNodes.push(capSrc, snode, mute);
      }
    }

    // ------------------------------------------------------------ imprint tee
    // Same 16k capture frames, to the existing /live speaker check. The server
    // verdict gates tools; here it only sets the chip and whispers
    // [voice check] context to the talker.
    function startImprintTee() {
      if (!session.has_imprint || !session.live_token) { return; }
      try {
        liveWs = new WebSocket(
          wsBase() + api + "/live?token=" + encodeURIComponent(session.live_token)
        );
        liveWs.onmessage = function (e) {
          var verdict;
          try { verdict = JSON.parse(e.data).speaker; } catch (err) { return; }
          onWho(verdict);
          if (verdict === lastVerdict) { return; }
          lastVerdict = verdict;
          if (verdictTimer) { clearTimeout(verdictTimer); }
          verdictTimer = setTimeout(function () {
            if (ended || !lastVerdict || lastVerdict === "unknown") { return; }
            contextTurn(
              "[voice check: the current speaker is " +
                (lastVerdict === "owner" ? "your owner]" : "an unrecognized voice, possibly not your owner]"),
              false
            );
          }, 1500);
        };
        liveWs.onerror = function () { try { liveWs.close(); } catch (e) {} liveWs = null; };
      } catch (e) { /* imprint tee is best-effort */ }
    }

    // ------------------------------------------------------------------- go
    setStatus("Connecting…");
    await startCapture();
    connect();

    var micMuted = false;
    return {
      session: session,
      micStream: micStream,
      end: function () { end(null); },
      // Mute = disable the mic track (the worklet then captures silence, the
      // connection stays up). The imprint tee hears silence too — harmless.
      setMuted: function (on) {
        micMuted = !!on;
        micStream.getAudioTracks().forEach(function (t) { t.enabled = !micMuted; });
      },
      muted: function () { return micMuted; },
    };
  }

  window.LunaRT = { start: start };
})();
