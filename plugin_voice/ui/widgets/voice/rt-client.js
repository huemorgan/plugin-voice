/* Luna realtime voice client — plain RTCPeerConnection against OpenAI
 * Realtime, no SDK bundle. One object on window:
 *
 *   LunaRT.start({api, authHeaders, callbacks…}) → Promise<handle>
 *   handle.end()
 *
 * The server does all thinking (persona, tools, gating); this file only
 * moves audio and events:
 *   - GET  {api}/rt/session      → client secret + rt_token (cookie auth)
 *   - SDP  → webrtc_url          → duplex audio (Bearer client_secret)
 *   - data channel "oai-events"  → function calls out, tool results back
 *   - POST {api}/rt/tool         → lane 2/3 execution (rt_token auth)
 *   - WS   {api}/rt/events       → lane-3 task updates → [task update] items
 *   - WS   {api}/live            → imprint tee (16k s16le PCM), verdict chip
 */
(function () {
  "use strict";

  function wsBase() {
    return (location.protocol === "https:" ? "wss://" : "ws://") + location.host;
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

    var session = null, pc = null, dc = null, micStream = null;
    var eventsWs = null, liveWs = null, tapNode = null, tapSrc = null, tapCtx = null;
    var ended = false, endedReason;
    var userSpeaking = false;
    var pendingNudges = [];   // task events held while the user is mid-utterance
    var flushTimer = null;
    var lastVerdict = null, verdictTimer = null;

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
      micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
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
      if (tapNode) { try { tapNode.disconnect(); } catch (e) {} }
      if (tapSrc) { try { tapSrc.disconnect(); } catch (e) {} }
      if (liveWs) { try { liveWs.close(); } catch (e) {} }
      if (eventsWs) { try { eventsWs.close(); } catch (e) {} }
      if (dc) { try { dc.close(); } catch (e) {} }
      if (pc) { try { pc.close(); } catch (e) {} }
      if (micStream) {
        micStream.getTracks().forEach(function (t) { try { t.stop(); } catch (e) {} });
      }
      onWho(null);
      onEnd(endedReason);
    }

    // ----------------------------------------------------------------- webrtc
    setStatus("Connecting…");
    pc = new RTCPeerConnection();
    micStream.getTracks().forEach(function (t) { pc.addTrack(t, micStream); });
    pc.ontrack = function (ev) {
      onRemoteStream(ev.streams && ev.streams[0] ? ev.streams[0] : new MediaStream([ev.track]));
    };
    pc.onconnectionstatechange = function () {
      if (ended) { return; }
      if (pc.connectionState === "failed") { end("Connection lost"); }
      else if (pc.connectionState === "closed" || pc.connectionState === "disconnected") { end(null); }
    };

    dc = pc.createDataChannel("oai-events");
    dc.onmessage = function (ev) {
      var msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      handleEvent(msg);
    };
    dc.onopen = function () {
      onState("listening");
      setStatus("Listening…");
      // The instructions carry the greeting — one nudge makes the talker
      // open the call instead of sitting silent until spoken to.
      send({ type: "response.create" });
      startTaskEvents();
      startImprintTee();
    };

    var offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    var sdpResp = await fetch(
      session.webrtc_url + "?model=" + encodeURIComponent(session.model),
      {
        method: "POST",
        headers: {
          "Authorization": "Bearer " + session.client_secret,
          "Content-Type": "application/sdp",
        },
        body: offer.sdp,
      }
    );
    if (!sdpResp.ok) {
      end("Voice connection refused (" + sdpResp.status + ")");
      throw new Error("OpenAI refused the call (HTTP " + sdpResp.status + ")");
    }
    await pc.setRemoteDescription({ type: "answer", sdp: await sdpResp.text() });

    function send(obj) {
      if (dc && dc.readyState === "open") {
        try { dc.send(JSON.stringify(obj)); } catch (e) {}
      }
    }

    // ---------------------------------------------------- data channel events
    function handleEvent(msg) {
      switch (msg.type) {
        case "input_audio_buffer.speech_started":
          userSpeaking = true;
          onState("listening");
          break;
        case "input_audio_buffer.speech_stopped":
          userSpeaking = false;
          scheduleFlush();
          break;
        case "response.created":
          onState("thinking");
          break;
        case "output_audio_buffer.started":
          onState("speaking");
          break;
        case "output_audio_buffer.stopped":
        case "output_audio_buffer.cleared":
          if (!ended) { onState("listening"); scheduleFlush(); }
          break;
        case "response.output_item.done":
          if (msg.item && msg.item.type === "function_call") {
            relayFunctionCall(msg.item);
          }
          break;
        case "error":
          // Non-fatal server events land here too; only kill the call when
          // the session itself is gone.
          if (msg.error && msg.error.code === "session_expired") {
            end("Voice session expired");
          }
          break;
      }
    }

    async function relayFunctionCall(item) {
      var args = {};
      try { args = JSON.parse(item.arguments || "{}"); } catch (e) {}
      // luna_view_react is a UI-only tool: it never leaves the browser — the
      // reaction goes straight to the Luna View renderer, zero latency.
      if (item.name === "luna_view_react") {
        onReact(args.shape || "");
        send({
          type: "conversation.item.create",
          item: {
            type: "function_call_output",
            call_id: item.call_id,
            output: JSON.stringify({ ok: true }),
          },
        });
        // No response.create here: the reaction is UI-only ("never announce it")
        // and forcing a new response makes the talker take a second, redundant
        // spoken turn for the same beat. The tool output is submitted so the
        // model knows it landed; it keeps speaking its current turn as normal.
        return;
      }
      onState("thinking");
      var out;
      try {
        var r = await fetch(api + "/rt/tool", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: session.rt_token, name: item.name, arguments: args }),
        });
        out = await r.json();
      } catch (e) {
        out = { ok: false, error: "That didn't go through — the connection hiccuped." };
      }
      send({
        type: "conversation.item.create",
        item: {
          type: "function_call_output",
          call_id: item.call_id,
          output: JSON.stringify(out),
        },
      });
      send({ type: "response.create" });
    }

    // -------------------------------------------------------- lane-3 events →
    // Task updates become conversation context. Completions nudge the talker
    // to announce them; a nudge never lands mid-utterance (queued, flushed on
    // speech_stopped + a beat).
    var lastSeq = 0;
    var activeTasks = {};   // task_id → true while a subagent is working

    // Track how many delegated (luna_do) tasks are in flight and surface the
    // count so the Luna View can float a cluster of dots per working subagent.
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
        if (userSpeaking) { return; }  // re-armed by speech_stopped
        var evts = pendingNudges.splice(0);
        var speak = false;
        evts.forEach(function (e2) {
          var line;
          if (e2.type === "task_done") {
            line = "[task update] Finished (" + e2.elapsed_s + "s): " + (e2.spoken_summary || e2.instruction);
            speak = true;
          } else if (e2.type === "task_failed") {
            line = "[task update] Failed: " + (e2.spoken_summary || e2.instruction);
            speak = true;
          } else if (e2.type === "task_started") {
            // Context only — the talker already acknowledged the dispatch.
            line = "[task update] Started working on: " + e2.instruction;
          } else { return; }
          send({
            type: "conversation.item.create",
            item: {
              type: "message", role: "system",
              content: [{ type: "input_text", text: line }],
            },
          });
        });
        if (speak) { send({ type: "response.create" }); }
      }, 600);
    }

    // ------------------------------------------------------------ imprint tee
    // Same mic stream, downsampled to 16k s16le, to the existing /live speaker
    // check. The server verdict gates tools; here it only sets the chip and
    // whispers [voice check] context to the talker.
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
            send({
              type: "conversation.item.create",
              item: {
                type: "message", role: "system",
                content: [{
                  type: "input_text",
                  text: "[voice check: the current speaker is " +
                    (lastVerdict === "owner" ? "your owner]" : "an unrecognized voice, possibly not your owner]"),
                }],
              },
            });
          }, 1500);
        };
        liveWs.onerror = function () { try { liveWs.close(); } catch (e) {} liveWs = null; };

        tapCtx = new (window.AudioContext || window.webkitAudioContext)();
        tapSrc = tapCtx.createMediaStreamSource(micStream);
        tapNode = tapCtx.createScriptProcessor(4096, 1, 1);
        var ratio = tapCtx.sampleRate / 16000, acc = [];
        tapNode.onaudioprocess = function (ev) {
          if (!liveWs || liveWs.readyState !== 1) { return; }
          var inp = ev.inputBuffer.getChannelData(0);
          for (var i = 0; i < inp.length / ratio; i++) {
            var v = inp[Math.floor(i * ratio)];
            acc.push(Math.max(-32768, Math.min(32767, v * 32768)) | 0);
          }
          if (acc.length >= 8000) { // 500ms @16k
            var bytes = new Uint8Array(acc.length * 2);
            for (var j = 0; j < acc.length; j++) {
              bytes[2 * j] = acc[j] & 255; bytes[2 * j + 1] = (acc[j] >> 8) & 255;
            }
            var bin = ""; for (var k = 0; k < bytes.length; k++) { bin += String.fromCharCode(bytes[k]); }
            liveWs.send(JSON.stringify({ pcm_b64: btoa(bin) }));
            acc = [];
          }
        };
        tapSrc.connect(tapNode); tapNode.connect(tapCtx.destination);
      } catch (e) { /* imprint tee is best-effort */ }
    }

    var micMuted = false;
    return {
      session: session,
      micStream: micStream,
      end: function () { end(null); },
      // Mute = disable the mic track (audio keeps flowing as silence, the
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
