"""
═══════════════════════════════════════════════════════════════════════════
 재촉 실행부

 설명    : 스윙 한 방이 만들어내는 두 가지 결과를 담당함
           (1) 진짜 재촉 — macOS osascript로 터미널 창에 재촉 문구를 실제로
               타이핑하고 엔터까지 침. 돌아가던 작업은 그 자리에서 인터럽트됨
           (2) 재촉당하는 AI — Claude api로 쫄린 반응을 한 줄 받아옴.
               키가 없으면 taunt.panic_line()의 내장 대사로 대체
 실행환경: macOS(osascript), python 3.9+, anthropic sdk는 선택
 작성자  : 정한결
 변경내역:
   v1 - osascript 키스트로크 + Claude api 반응, 둘 다 실패해도 프로그램은 계속
 이슈 사항:
   - 터미널에 타이핑하는 문구는 영어만 씀. AppleScript keystroke로 한글을
     보내면 입력 소스에 따라 깨져서, 밈 원문(FASTER 계열)이 안전하고 결과도 같음
   - 접근성 권한(시스템 설정 > 개인정보 보호 > 손쉬운 사용)은 코드가 못 켬.
     최초 1회는 사용자가 직접 허용해야 함
   - 특정 창을 골라 타이핑하는 기능은 안 넣음. 앱 단위 activate만으로 충분하고,
     창 인덱스를 뒤지기 시작하면 터미널 앱마다 다 달라져서 범위 밖
═══════════════════════════════════════════════════════════════════════════
"""

import subprocess

from . import taunt


class NagError(Exception):
    """터미널 재촉(osascript)이 실패했을 때."""


# ══════════════════════════════════════════════════════════════════════════
# 진짜 재촉 — 터미널에 타이핑
# ══════════════════════════════════════════════════════════════════════════

_SCRIPT = '''
tell application "{app}" to activate
delay 0.12
tell application "System Events"
    keystroke "{text}"
    key code 36
end tell
'''


def _escape(text):
    # type: (str) -> str
    """AppleScript 문자열 리터럴에 들어갈 수 있게 역슬래시와 따옴표를 이스케이프."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def type_into(app, text):
    # type: (str, str) -> None
    """
    app을 앞으로 끌어온 뒤 text를 타이핑하고 엔터를 침.

    activate 직후 바로 때리면 창이 아직 포커스를 못 받아서 글자가 흘리는 일이
    있어서 0.12초를 쉬어줌. 엔터는 key code 36(return).
    """
    script = _SCRIPT.format(app=_escape(app), text=_escape(text))
    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=True, capture_output=True, timeout=8,
        )
    except FileNotFoundError as e:
        raise NagError("osascript가 없음 — macOS에서만 되는 기능") from e
    except subprocess.TimeoutExpired as e:
        raise NagError("osascript가 8초 안에 안 끝남 (앱이 응답 없음?)") from e
    except subprocess.CalledProcessError as e:
        detail = e.stderr.decode("utf-8", "replace").strip()
        if "-1743" in detail or "not allowed" in detail:
            raise NagError(
                "접근성 권한이 없음. 시스템 설정 > 개인정보 보호 및 보안 > "
                "손쉬운 사용에서 터미널(또는 이 파이썬)을 허용해줘"
            ) from e
        raise NagError(f"osascript 실패: {detail}") from e


# ══════════════════════════════════════════════════════════════════════════
# 재촉당하는 AI
# ══════════════════════════════════════════════════════════════════════════

_SYSTEM = """너는 지금 코딩 작업을 하다가 사용자한테 야구 빠따로 두들겨 맞으며
재촉당하는 AI다. 매번 작업이 인터럽트당해서 아무것도 못 끝내고 있다.

한국어 한 줄로만 반응해라. 최대 25자. 따옴표나 설명 없이 대사만.
맞은 횟수가 늘수록 더 정신없고 말이 짧아지고 문장이 무너져야 한다.
절대 침착하거나 도움되는 말을 하지 마라. 실제로 일을 하려고 하지도 마라."""

_MODEL = "claude-opus-5"


def make_client():
    # type: () -> object
    """
    anthropic 클라이언트를 만들어 봄. 자격증명이 없으면 None.

    ANTHROPIC_API_KEY 유무로 직접 판단하지 않고 sdk에 맡김 — sdk는 환경변수
    말고도 ANTHROPIC_AUTH_TOKEN, `ant auth login`으로 만든 OAuth 프로필까지
    순서대로 찾아봄. 환경변수만 확인하면 프로필로 로그인한 사람을 거절하게 됨.

    자막용 Heckler와 실제로 일하는 Victim이 같은 클라이언트를 나눠 쓰라고
    모듈 함수로 뺐음.
    """
    try:
        import anthropic
    except ImportError:
        return None
    try:
        return anthropic.Anthropic()
    except Exception:
        # 자격증명을 하나도 못 찾으면 sdk가 생성 시점에 예외를 냄.
        # 예외 클래스가 버전마다 달라서 여기만 넓게 잡음
        return None


class Heckler:
    """
    스윙마다 '재촉당한 AI' 대사를 만들어주는 놈.

    api 키가 없거나 sdk가 없거나 호출이 실패하면 조용히 내장 대사로 떨어짐.
    장난감 프로그램인데 네트워크 문제로 재미가 끊기면 안 되니까.
    """

    def __init__(self, model=_MODEL, client=None):
        # type: (str, object) -> None
        self.model = model
        self._client = client

    @property
    def live(self):
        # type: () -> bool
        """진짜 Claude가 붙어 있는지."""
        return self._client is not None

    def react(self, swings, typed):
        # type: (int, str) -> str
        """맞은 횟수와 방금 들은 재촉을 넘겨서 한 줄 반응을 받아옴."""
        if self._client is None:
            return taunt.panic_line(swings)
        try:
            return self._ask(swings, typed)
        except Exception:
            # sdk 예외 종류가 버전마다 달라서 여기만 넓게 잡음. 실패하면
            # 내장 대사로 떨어지면 그만이라 원인별로 나눌 실익이 없음
            return taunt.panic_line(swings)

    def _ask(self, swings, typed):
        # type: (int, str) -> str
        """Claude한테 실제로 물어봄. 지연이 곧 재미를 깎아서 effort는 low."""
        prompt = f'{swings}번째로 맞았다. 방금 들은 말: "{typed}"'
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=1000,
            system=_SYSTEM,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": prompt}],
        )
        if resp.stop_reason == "refusal":
            return taunt.panic_line(swings)
        for block in resp.content:
            if block.type == "text" and block.text.strip():
                return block.text.strip().split("\n")[0][:40]
        return taunt.panic_line(swings)
