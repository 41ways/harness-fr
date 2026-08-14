/*
 * ══════════════════════════════════════════════════════════════════════════
 *  clanker-bat 크롬 확장 — 브라우저에서 열어둔 AI를 재촉함
 *
 *  설명    : 로컬 서버(SSE)에 붙어 있다가 스윙이 오면 지금 보고 있는 AI
 *            웹 UI를 직접 조작함. (1) 중지 버튼을 눌러 생성을 끊고
 *            (2) 입력창에 재촉을 타이핑해서 전송. 영상에서 터미널에 하던 짓을
 *            그대로 웹에서 하는 것
 *  실행환경: 크롬 MV3 content script. 서버가 http://127.0.0.1:8444에 떠 있어야 함
 *  작성자  : 정한결
 *  변경내역:
 *    v1 - SSE 연결, 중지+타이핑+전송, 사이트별 셀렉터 + 범용 폴백, 상태 배지
 *  이슈 사항:
 *    - 셀렉터는 사이트가 UI를 바꾸면 언제든 깨짐. 그래서 사이트별 셀렉터가
 *      빗나가면 aria-label/텍스트로 버튼을 훑는 범용 폴백으로 넘어감.
 *      그래도 안 되면 콘솔에 뭘 못 찾았는지 찍음
 *    - 입력은 execCommand("insertText")로 넣음. 폐기 예정 api지만 React나
 *      ProseMirror가 알아듣는 input 이벤트를 제대로 발생시키는 건 아직 이게
 *      제일 확실함. value에 직접 대입하면 프레임워크가 무시함
 *    - 서버가 https(8443)가 아니라 평문 http(8444)에 붙는 이유: 자체서명
 *      인증서라 TLS 검증에 막힘. 반대로 http://127.0.0.1은 크롬이 신뢰 가능한
 *      출처로 쳐서 https 페이지에서도 mixed content 차단을 안 함
 *    - 자동 로그인이나 계정 조작은 일절 안 함. 이미 로그인해서 열어둔 탭의
 *      입력창에 글을 넣고 보내는 것까지만
 * ══════════════════════════════════════════════════════════════════════════
 */

(function () {
  "use strict";

  if (window.top !== window) { return; }   // iframe 안에서는 안 돎

  var SERVER = "http://127.0.0.1:8444";
  var PING_EVERY = 20000;                   // 서버의 연결 판정 시간(45초)보다 짧게

  // ══════════════════════════════════════════════════════════════════════
  // 사이트별 셀렉터 — UI가 바뀌면 여기만 고치면 됨
  // ══════════════════════════════════════════════════════════════════════
  var SITES = [
    {
      match: /claude\.ai/,
      name: "claude.ai",
      composer: ['div[contenteditable="true"].ProseMirror', 'div[contenteditable="true"]'],
      send: ['button[aria-label*="Send"]', 'button[type="submit"]'],
      stop: ['button[aria-label*="Stop"]']
    },
    {
      match: /chatgpt\.com|chat\.openai\.com/,
      name: "chatgpt",
      composer: ["#prompt-textarea", 'div[contenteditable="true"]', "textarea"],
      send: ['button[data-testid="send-button"]', 'button[aria-label*="Send"]'],
      stop: ['button[data-testid="stop-button"]', 'button[aria-label*="Stop"]']
    },
    {
      match: /gemini\.google\.com|aistudio\.google\.com/,
      name: "gemini",
      composer: ['div[contenteditable="true"]', "textarea"],
      send: ['button[aria-label*="Send"]', 'button[aria-label*="Run"]'],
      stop: ['button[aria-label*="Stop"]']
    }
  ];

  var site = null;
  for (var i = 0; i < SITES.length; i++) {
    if (SITES[i].match.test(location.host)) { site = SITES[i]; break; }
  }
  if (!site) { return; }

  // ══════════════════════════════════════════════════════════════════════
  // DOM 찾기
  // ══════════════════════════════════════════════════════════════════════
  function visible(el) {
    if (!el) { return false; }
    var r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
  }

  function bySelectors(list) {
    for (var i = 0; i < list.length; i++) {
      var found = document.querySelectorAll(list[i]);
      for (var j = 0; j < found.length; j++) {
        if (visible(found[j]) && !found[j].disabled) { return found[j]; }
      }
    }
    return null;
  }

  // 셀렉터가 빗나갔을 때 버튼을 라벨/텍스트로 훑는 폴백
  function byLabel(re) {
    var buttons = document.querySelectorAll("button, [role=button]");
    for (var i = 0; i < buttons.length; i++) {
      var b = buttons[i];
      if (!visible(b) || b.disabled) { continue; }
      var label = (b.getAttribute("aria-label") || "") + " " +
                  (b.getAttribute("title") || "") + " " + (b.textContent || "");
      if (re.test(label)) { return b; }
    }
    return null;
  }

  function findComposer() {
    var el = bySelectors(site.composer);
    if (el) { return el; }
    // 폴백: 화면에서 제일 큰 편집 가능 영역이 대개 입력창
    var cands = document.querySelectorAll('div[contenteditable="true"], textarea');
    var best = null, bestArea = 0;
    for (var i = 0; i < cands.length; i++) {
      if (!visible(cands[i])) { continue; }
      var r = cands[i].getBoundingClientRect();
      if (r.width * r.height > bestArea) { best = cands[i]; bestArea = r.width * r.height; }
    }
    return best;
  }

  var STOP_RE = /stop|중지|멈춤|정지/i;
  var SEND_RE = /send|submit|보내기|전송/i;

  // ══════════════════════════════════════════════════════════════════════
  // 입력하고 보내기
  // ══════════════════════════════════════════════════════════════════════
  function typeInto(el, text) {
    el.focus();
    if (el.tagName === "TEXTAREA" || el.tagName === "INPUT") {
      // React는 value에 직접 대입한 걸 무시해서, 네이티브 setter로 넣고
      // input 이벤트를 손으로 쏴줘야 상태가 갱신됨
      var proto = el.tagName === "TEXTAREA"
        ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
      var setter = Object.getOwnPropertyDescriptor(proto, "value").set;
      setter.call(el, el.value ? el.value + " " + text : text);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      return;
    }
    // contenteditable — 커서를 끝으로 옮기고 execCommand로 삽입
    var sel = window.getSelection();
    var range = document.createRange();
    range.selectNodeContents(el);
    range.collapse(false);
    sel.removeAllRanges();
    sel.addRange(range);
    document.execCommand("insertText", false, text);
  }

  function submit(el) {
    var btn = bySelectors(site.send) || byLabel(SEND_RE);
    if (btn) { btn.click(); return true; }
    // 전송 버튼을 못 찾으면 엔터로
    ["keydown", "keypress", "keyup"].forEach(function (type) {
      el.dispatchEvent(new KeyboardEvent(type, {
        key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true
      }));
    });
    return false;
  }

  function interrupt() {
    var btn = bySelectors(site.stop) || byLabel(STOP_RE);
    if (btn) { btn.click(); return true; }
    return false;
  }

  // ══════════════════════════════════════════════════════════════════════
  // 스윙 처리
  // ══════════════════════════════════════════════════════════════════════
  function onSwing(ev) {
    var stopped = interrupt();          // 생성 중이었으면 여기서 끊김
    var composer = findComposer();
    if (!composer) {
      console.warn("[clanker-bat] 입력창을 못 찾음. content.js의 SITES 셀렉터를 확인해줘");
      flash("입력창 못 찾음", true);
      return;
    }
    // 중지 직후 UI가 정리될 틈을 조금 줌 — 바로 치면 입력이 씹히는 경우가 있음
    setTimeout(function () {
      typeInto(composer, ev.typed);
      setTimeout(function () {
        submit(composer);
        flash((stopped ? "인터럽트 + " : "") + ev.typed, false);
      }, 60);
    }, stopped ? 180 : 0);
  }

  // ══════════════════════════════════════════════════════════════════════
  // 상태 배지 — 조용히 죽으면 원인 찾기가 지옥이라 눈에 보이게 함
  // ══════════════════════════════════════════════════════════════════════
  var badge = document.createElement("div");
  badge.style.cssText = [
    "position:fixed", "right:14px", "bottom:14px", "z-index:2147483647",
    "background:#1b1917", "color:#d97757", "border:1px solid #d97757",
    "border-radius:999px", "padding:6px 13px", "pointer-events:none",
    "font:12px ui-monospace,Menlo,monospace", "opacity:.92",
    "transition:background .12s,color .12s", "max-width:60vw",
    "overflow:hidden", "text-overflow:ellipsis", "white-space:nowrap"
  ].join(";");
  badge.textContent = "clanker-bat 연결 중…";
  function mount() {
    if (document.body && !badge.isConnected) { document.body.appendChild(badge); }
  }
  mount();
  document.addEventListener("DOMContentLoaded", mount);

  function flash(text, bad) {
    badge.textContent = text;
    badge.style.background = bad ? "#3a1b14" : "#d97757";
    badge.style.color = bad ? "#ffb4a0" : "#1b1917";
    setTimeout(function () {
      badge.style.background = "#1b1917";
      badge.style.color = "#d97757";
      badge.textContent = "clanker-bat 대기 중";
    }, 1400);
  }

  // ══════════════════════════════════════════════════════════════════════
  // 서버 연결
  // ══════════════════════════════════════════════════════════════════════
  function ping() {
    // Content-Type을 text/plain으로 두면 CORS 프리플라이트가 안 뜸
    fetch(SERVER + "/hooked", {
      method: "POST",
      headers: { "Content-Type": "text/plain" },
      body: JSON.stringify({ site: site.name })
    }).catch(function () {});
  }

  function connect() {
    var es = new EventSource(SERVER + "/events");
    es.onopen = function () {
      badge.textContent = "clanker-bat 대기 중";
      ping();
    };
    es.onmessage = function (e) {
      var ev = JSON.parse(e.data);
      if (ev.type === "swing") { onSwing(ev); }
    };
    es.onerror = function () {
      badge.textContent = "clanker-bat 서버 끊김";
      // EventSource가 알아서 재연결하지만, 서버가 아예 죽었으면 계속 실패함
    };
  }

  connect();
  setInterval(ping, PING_EVERY);
  console.log("[clanker-bat] " + site.name + "에 붙음. 서버: " + SERVER);
})();
