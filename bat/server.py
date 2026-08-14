"""
═══════════════════════════════════════════════════════════════════════════
 빠따 서버 (https + SSE)

 설명    : 아이폰(빠따)과 맥(대시보드)을 이어주는 서버. 아이폰이 스윙을 감지해서
           POST /swing을 쏘면, 서버가 세기를 판정하고 → 터미널에 진짜 재촉을
           타이핑하고 → 대시보드로 SSE 이벤트를 밀어줌
 실행환경: python 3.9+ 표준 라이브러리만 (외부 패키지 없음), macOS
 작성자  : 정한결
 변경내역:
   v1 - ThreadingHTTPServer + ssl + SSE 브로드캐스트
 이슈 사항:
   - 웹소켓 대신 SSE + POST 조합을 씀. 통신이 한 방향씩이면 충분한데
     websocket 핸드셰이크를 직접 구현하면 코드만 150줄 늘고 얻는 게 없음
   - 인증/세션 없음. 같은 와이파이 안에서만 도는 장난감이라 범위 밖.
     대신 진짜 재촉(키스트로크)은 기본 꺼두고 대시보드에서 켜야 동작하게 함
   - 스윙 카운트는 메모리에만 있음. 서버 끄면 초기화됨
═══════════════════════════════════════════════════════════════════════════
"""

import json
import os
import queue
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import nag, taunt, victim

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")

# 콤보 유지 시간(초). 이 안에 다시 치면 연타로 침
COMBO_WINDOW = 2.5


# ══════════════════════════════════════════════════════════════════════════
# 상태 허브
# ══════════════════════════════════════════════════════════════════════════

class Hub:
    """
    스윙 상태를 들고 있으면서 대시보드들한테 이벤트를 뿌리는 중앙.

    구독자마다 Queue를 하나씩 쥐어주는 방식. 대시보드가 느리거나 죽어도
    그 큐만 밀리고 다른 구독자나 스윙 처리에는 영향이 없음.
    """

    def __init__(self, heckler, target_app):
        # type: (nag.Heckler, str) -> None
        self._lock = threading.Lock()
        self._subs = []  # type: list
        self.heckler = heckler
        self.target_app = target_app
        self.armed = False        # 진짜 재촉(키스트로크) 스위치. 기본 꺼짐
        self.swings = 0
        self.combo = 0
        self._last_swing_at = 0.0
        self.victim = None        # 실제로 일하는 AI. run.py에서 붙여줌
        self._level = 0           # 압박 단계 (taunt.PRESSURE 인덱스)
        self._level_at = time.time()
        self._pending = []        # 다음 턴에 꽂을 재촉 문장들

    # ── 구독 관리 ────────────────────────────────────────────────────────
    def subscribe(self):
        # type: () -> queue.Queue
        q = queue.Queue(maxsize=64)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        # type: (queue.Queue) -> None
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, event):
        # type: (dict) -> None
        """모든 대시보드에 이벤트를 밀어줌. 큐가 꽉 찬 구독자는 그냥 건너뜀."""
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass

    # ── 압박 단계 ────────────────────────────────────────────────────────
    def pressure(self):
        # type: () -> tuple
        """
        지금 걸어야 할 (effort, fast mode)를 돌려줌.

        회복은 읽는 시점에 계산함 — 타이머 스레드를 하나 더 돌리는 것보다
        단순하고, 어차피 이 값은 다음 턴 시작할 때만 필요함.
        """
        with self._lock:
            self._decay_locked()
            return taunt.pressure(self._level)

    def _decay_locked(self):
        """재촉이 끊긴 시간만큼 압박 단계를 되돌림. 락을 잡은 채로 호출할 것."""
        idle = time.time() - self._level_at
        steps = int(idle // taunt.RECOVER_AFTER)
        if steps > 0 and self._level > 0:
            self._level = max(0, self._level - steps)
            self._level_at = time.time()

    def drain_nags(self):
        # type: () -> list
        """밀려 있던 재촉 문장을 꺼내가고 비움."""
        with self._lock:
            pending, self._pending = self._pending, []
        return pending

    # ── 스윙 처리 ────────────────────────────────────────────────────────
    def swing(self, strength):
        # type: (float) -> dict
        """
        스윙 한 방을 처리하고 대시보드에 뿌릴 이벤트를 만들어 돌려줌.

        AI 반응은 여기서 기다리지 않고 별도 스레드로 넘김 — api 호출이 1~2초
        걸리는데 그동안 아이폰 응답이 막히면 연타 감이 죽음.
        """
        now = time.time()
        with self._lock:
            self.swings += 1
            if now - self._last_swing_at <= COMBO_WINDOW:
                self.combo += 1
            else:
                self.combo = 1
            self._last_swing_at = now
            swings, combo = self.swings, self.combo

            # 한 대 = 압박 한 칸. 회복분을 먼저 반영하고 올려야 순서가 안 꼬임
            self._decay_locked()
            self._level = min(self._level + 1, len(taunt.PRESSURE) - 1)
            self._level_at = now
            effort, fast = taunt.pressure(self._level)
            # 일하는 중일 때만 대화에 꽂음. 안 돌 때 쌓아두면 나중에 뜬금없이 나감
            if self.victim is not None and self.victim.busy:
                self._pending.append(taunt.system_nag(swings))

        tier = taunt.classify(strength)
        line = taunt.pick(tier)
        event = {
            "type": "swing",
            "at": now,
            "strength": round(strength, 1),
            "swings": swings,
            "combo": combo,
            "words": taunt.spinner_words(swings),
            "effort": effort,
            "fast": fast,
        }
        event.update(line)

        self.publish(event)
        threading.Thread(
            target=self._after_swing,
            args=(swings, str(line["typed"])),
            daemon=True,
        ).start()
        return event

    def _after_swing(self, swings, typed):
        # type: (int, str) -> None
        """터미널 타이핑과 AI 반응을 백그라운드에서 처리."""
        if self.armed:
            try:
                nag.type_into(self.target_app, typed)
            except nag.NagError as e:
                self.armed = False
                self.publish({"type": "nag_error", "message": str(e)})

        self.publish({
            "type": "reaction",
            "swings": swings,
            "text": self.heckler.react(swings, typed),
        })

    def state(self):
        # type: () -> dict
        effort, fast = self.pressure()
        with self._lock:
            return {
                "swings": self.swings,
                "combo": self.combo,
                "armed": self.armed,
                "target": self.target_app,
                "live_ai": self.heckler.live,
                "effort": effort,
                "fast": fast,
                "can_work": self.victim is not None and self.victim.ready,
                "working": self.victim is not None and self.victim.busy,
            }


# ══════════════════════════════════════════════════════════════════════════
# HTTP 핸들러
# ══════════════════════════════════════════════════════════════════════════

class Handler(BaseHTTPRequestHandler):
    """
    라우팅이 여섯 개뿐이라 프레임워크 없이 if 분기로 처리함.

    hub는 서버 인스턴스에 붙여두고 여기서 꺼내 씀 (핸들러는 요청마다 새로 생김).
    """

    server_version = "ClankerBat/1"

    @property
    def hub(self):
        # type: () -> Hub
        return self.server.hub  # type: ignore[attr-defined]

    def log_message(self, fmt, *args):
        """요청 로그는 끔 — 스윙마다 한 줄씩 쌓이면 콘솔이 안 보임."""

    # ── 응답 헬퍼 ────────────────────────────────────────────────────────
    def _send(self, code, body, content_type):
        # type: (int, bytes, str) -> None
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj, code=200):
        # type: (dict, int) -> None
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _send_page(self, name):
        # type: (str) -> None
        path = os.path.join(STATIC_DIR, name)
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        self._send(200, body, "text/html; charset=utf-8")

    def _read_json(self):
        # type: () -> dict
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    # ── 라우팅 ───────────────────────────────────────────────────────────
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self._send_page("bat.html")
        elif path == "/dash":
            self._send_page("dash.html")
        elif path == "/state":
            self._send_json(self.hub.state())
        elif path == "/events":
            self._stream()
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/swing":
            data = self._read_json()
            try:
                strength = float(data.get("strength", 0.0))
            except (TypeError, ValueError):
                self._send_json({"error": "strength가 숫자가 아님"}, code=400)
                return
            self._send_json(self.hub.swing(strength))
        elif path == "/task":
            data = self._read_json()
            task = data.get("task")
            if not isinstance(task, str) or not task.strip():
                self._send_json({"error": "과제 내용이 비어 있음"}, code=400)
                return
            hub = self.hub
            if hub.victim is None:
                self._send_json({"error": "작업용 AI가 준비 안 됨"}, code=400)
                return
            try:
                hub.victim.start(task.strip())
            except victim.VictimError as e:
                self._send_json({"error": str(e)}, code=409)
                return
            hub.publish({"type": "work_task", "task": task.strip()})
            hub.publish({"type": "state", "state": hub.state()})
            self._send_json(hub.state())
        elif path == "/arm":
            data = self._read_json()
            self.hub.armed = bool(data.get("on"))
            target = data.get("target")
            if isinstance(target, str) and target.strip():
                self.hub.target_app = target.strip()
            self.hub.publish({"type": "state", "state": self.hub.state()})
            self._send_json(self.hub.state())
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    # ── SSE ──────────────────────────────────────────────────────────────
    def _stream(self):
        # type: () -> None
        """
        대시보드용 이벤트 스트림.

        15초마다 주석 한 줄(하트비트)을 보내서 중간 장비가 연결을 끊는 걸 막음.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        q = self.hub.subscribe()
        try:
            self._write_event({"type": "state", "state": self.hub.state()})
            while True:
                try:
                    event = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                self._write_event(event)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # 대시보드 탭이 닫힌 것뿐
        finally:
            self.hub.unsubscribe(q)

    def _write_event(self, event):
        # type: (dict) -> None
        payload = json.dumps(event, ensure_ascii=False)
        self.wfile.write((f"data: {payload}\n\n").encode())
        self.wfile.flush()


# ══════════════════════════════════════════════════════════════════════════
# 서버 기동
# ══════════════════════════════════════════════════════════════════════════

def build(host, port, certfile, keyfile, heckler, target_app):
    # type: (str, int, str, str, nag.Heckler, str) -> ThreadingHTTPServer
    """모든 인터페이스에 바인딩한 https 서버를 만들어서 돌려줌."""
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    httpd.daemon_threads = True
    httpd.hub = Hub(heckler, target_app)  # type: ignore[attr-defined]

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile, keyfile)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    return httpd
