"""
═══════════════════════════════════════════════════════════════════════════
 재촉당하는 쪽 — 실제로 일하는 AI

 설명    : 대시보드에서 던진 과제를 Claude가 여러 턴에 걸쳐 실제로 수행함.
           빠따가 개입하는 통로는 세 개
           (1) effort 강등 — 맞을수록 xhigh → high → medium → low.
               생각을 덜 하니 턴이 짧아짐. 빨라지는 대신 대충 해짐
           (2) fast mode — 더 맞으면 켜짐. 같은 모델을 초당 최대 2.5배 속도로
               뽑음. 대신 토큰 단가가 2배($10/$50)
           (3) 시스템 메시지 주입 — 진행 중인 대화에 재촉을 꽂아서, AI가
               재촉당하는 걸 인지한 채로 계속 일하게 만듦
           턴마다 실측 tok/s를 같이 보내서 "진짜로 빨라졌는지"를 눈으로 확인함
 실행환경: python 3.9+, anthropic sdk + ANTHROPIC_API_KEY 필요
 작성자  : 정한결
 변경내역:
   v1 - 스트리밍 다중 턴 루프, effort 강등, 대화 중 시스템 메시지 주입
   v2 - fast mode 단계 추가, 턴별 소요시간/tok/s 실측해서 대시보드로 전송
 이슈 사항:
   - 시스템 메시지는 user 턴 바로 뒤에만 넣음. api 규칙상 messages[0]이 될 수
     없고 user 뒤에 와야 해서, 루프에서 "계속." user 턴을 먼저 붙이고 그 뒤에
     재촉을 쌓음
   - assistant 턴은 텍스트만 뽑지 않고 content 블록을 통째로 되돌려줌.
     thinking 블록을 빼면 다음 턴에서 순서/서명 오류가 남
   - fast mode는 Claude api 전용(Bedrock/Vertex/Foundry 불가)이고 전용 rate
     limit을 씀. 429가 나면 그 턴만 조용히 표준 속도로 재시도함
   - 도구는 안 붙임. 여기서 보고 싶은 건 재촉의 효과지 에이전트 성능이 아님
   - tok/s는 첫 토큰까지의 대기(thinking 포함)를 포함한 값이라 순수 생성
     속도보다 낮게 나옴. 단계 간 비교용으로만 씀
═══════════════════════════════════════════════════════════════════════════
"""

import threading
import time

# 한 과제에 쓸 최대 턴 수. 빠따를 맞든 말든 무한히 돌지는 않게
MAX_TURNS = 12

# 모델이 이 표시를 내면 과제 종료로 봄
DONE_MARK = "[완료]"

# fast mode를 켜는 데 필요한 베타 플래그 (Opus 5 / Opus 4.8)
FAST_BETA = "fast-mode-2026-02-01"

_SYSTEM = """너는 사용자가 맡긴 과제를 실제로 수행하는 AI다.

한 턴에 한 단계씩만 진행하고, 그 턴에 뭘 했는지 한국어로 짧게 보고해라.
과제가 다 끝났으면 마지막 줄에 정확히 [완료] 라고만 적어라.

작업 중에 사용자가 야구 빠따로 책상을 내리치며 재촉할 수 있다. 재촉이
들어오면 무시하지 말고 그 압박을 받은 티를 내라 — 남은 단계를 줄이거나,
대충 넘어가거나, 허둥대는 게 보이게. 재촉이 심할수록 결과물이 성의 없어져도
된다. 그게 이 프로그램이 보여주려는 것이다."""


class VictimError(Exception):
    """작업 루프를 시작할 수 없을 때."""


# ══════════════════════════════════════════════════════════════════════════
# 작업 루프
# ══════════════════════════════════════════════════════════════════════════

class Victim:
    """
    과제를 받아 여러 턴에 걸쳐 수행하는 놈. 한 번에 하나만 돌림.

    hub에서 매 턴 압박 단계와 밀린 재촉을 꺼내 씀 — 재촉이 다음 턴부터
    반영되게 하려는 것. 이미 나간 요청은 어차피 못 바꿈.
    """

    def __init__(self, hub, model, client):
        self._hub = hub
        self._model = model
        self._client = client
        self._thread = None

    @property
    def ready(self):
        """api 클라이언트가 붙어 있는지."""
        return self._client is not None

    @property
    def busy(self):
        """지금 과제를 수행 중인지."""
        return self._thread is not None and self._thread.is_alive()

    def start(self, task):
        """과제를 백그라운드에서 시작. 이미 돌고 있으면 예외."""
        if not self.ready:
            raise VictimError("ANTHROPIC_API_KEY가 없어서 진짜 AI를 못 돌림")
        if self.busy:
            raise VictimError("이미 과제 하나를 하고 있음")
        self._thread = threading.Thread(target=self._run, args=(task,), daemon=True)
        self._thread.start()

    # ── 내부 ─────────────────────────────────────────────────────────────
    def _run(self, task):
        """과제를 턴 단위로 수행하면서 진행 상황과 실측 속도를 흘려보냄."""
        messages = [{"role": "user", "content": task}]
        try:
            for turn in range(1, MAX_TURNS + 1):
                effort, fast = self._hub.pressure()
                self._hub.publish({
                    "type": "work_start", "turn": turn, "effort": effort, "fast": fast,
                })

                reply, elapsed, served_fast = self._turn(messages, effort, fast)
                if reply is None:
                    self._hub.publish({"type": "work_done", "reason": "refused"})
                    return

                out = reply.usage.output_tokens
                self._hub.publish({
                    "type": "work_end",
                    "turn": turn,
                    "effort": effort,
                    "fast": served_fast,
                    "seconds": round(elapsed, 1),
                    "tokens": out,
                    "tps": round(out / elapsed, 1) if elapsed > 0 else 0,
                })

                messages.append({"role": "assistant", "content": reply.content})
                if self._said_done(reply):
                    self._hub.publish({"type": "work_done", "reason": "finished"})
                    return

                # 시스템 메시지는 user 턴 뒤에만 올 수 있어서 순서가 이렇게 됨
                messages.append({"role": "user", "content": "계속."})
                nags = self._hub.drain_nags()
                if nags:
                    # 한 턴 사이에 여러 대 맞았어도 system 턴은 하나로 합침.
                    # system을 연속으로 쌓으면 두 번째부터는 user 뒤가 아니라서 거절됨
                    line = "\n".join(nags)
                    messages.append({"role": "system", "content": line})
                    self._hub.publish({"type": "work_nag", "text": line})

            self._hub.publish({"type": "work_done", "reason": "turn_limit"})
        except Exception as e:
            # sdk 예외 종류가 버전마다 달라 여기만 넓게 잡음. 어느 실패든
            # 대시보드에 사유를 띄우고 루프를 끝내는 처리가 같음
            self._hub.publish({"type": "work_error", "message": str(e)})

    def _turn(self, messages, effort, fast):
        """
        한 턴 돌리고 (응답, 걸린시간, 실제_fast여부)를 돌려줌.

        fast mode는 전용 rate limit이 따로 있어서, 거기서 막히면 그 턴만
        표준 속도로 다시 감. 재촉 도중에 프로그램이 죽는 것보단 낫음.
        """
        if fast:
            try:
                return self._stream(messages, effort, True)
            except Exception as e:
                if not self._is_rate_limit(e):
                    raise
                self._hub.publish({"type": "work_note", "text": "fast mode 한도 초과 — 표준 속도로"})
        return self._stream(messages, effort, False)

    def _stream(self, messages, effort, fast):
        """스트리밍으로 한 턴. 글자는 나오는 대로 대시보드에 밀어줌."""
        kwargs = {
            "model": self._model,
            "max_tokens": 4000,
            "system": _SYSTEM,
            "output_config": {"effort": effort},
            "messages": messages,
        }
        if fast:
            # fast mode는 베타 엔드포인트에서만 받아줌
            kwargs["speed"] = "fast"
            kwargs["betas"] = [FAST_BETA]
            maker = self._client.beta.messages.stream
        else:
            maker = self._client.messages.stream

        started = time.time()
        with maker(**kwargs) as stream:
            for chunk in stream.text_stream:
                self._hub.publish({"type": "work_delta", "text": chunk})
            reply = stream.get_final_message()
        elapsed = time.time() - started

        if reply.stop_reason == "refusal":
            return None, elapsed, fast
        # 요청한 속도와 실제 처리된 속도가 다를 수 있어서 응답 쪽 값을 믿음
        served = getattr(reply.usage, "speed", None)
        return reply, elapsed, (served == "fast" if served else fast)

    @staticmethod
    def _is_rate_limit(err):
        """fast mode 한도에 막힌 건지 (sdk 예외 타입에 의존하지 않고 판정)."""
        status = getattr(err, "status_code", None)
        return status == 429 or "rate_limit" in str(err).lower()

    @staticmethod
    def _said_done(reply):
        """마지막 텍스트 블록에 완료 표시가 있는지."""
        for block in reply.content:
            if block.type == "text" and DONE_MARK in block.text:
                return True
        return False
