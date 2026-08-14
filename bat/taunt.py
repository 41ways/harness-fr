"""
═══════════════════════════════════════════════════════════════════════════
 재촉 멘트 사전

 설명    : 스윙 세기에 따라 터미널에 꽂을 재촉 문구를 고름. 참고한 영상 그대로
           FASTER / GO FASTER / Work FASTER / Speed it up clanker 계열을 씀.
           터미널에 실제로 타이핑되는 건 영어(밈 원문 + 키스트로크 안전),
           대시보드에 뜨는 자막은 한글
 실행환경: python 3.9+, 표준 라이브러리만
 작성자  : 정한결
 변경내역:
   v1 - 3단계(살짝/제대로/홈런) 멘트 + Claude Code 스피너 단어 + 오프라인
        AI 패닉 대사
 이슈 사항:
   - 멘트를 외부 json으로 빼는 건 안 함. 개수가 이 정도면 코드에 두는 게
     읽기 편하고, 튜닝도 여기서 바로 하는 게 빠름
═══════════════════════════════════════════════════════════════════════════
"""

import random

# ══════════════════════════════════════════════════════════════════════════
# 세기 판정
# ══════════════════════════════════════════════════════════════════════════

# 아이폰 가속도(m/s^2, 중력 제외) 기준. 팔로 툭 치면 15 근처, 제대로 휘두르면
# 30~45, 있는 힘껏 돌리면 60 이상 나옴 (아이폰 13 기준 실측값)
BUNT_MAX = 22.0
LINE_MAX = 45.0

BUNT = "bunt"
LINER = "liner"
HOMERUN = "homerun"

# ── 로봇이 망가지는 단계 ──────────────────────────────────────────────────
# 이만큼 맞을 때마다 다음 사진으로 넘어감. 사진과 대사가 같은 기준을 쓰도록
# 여기 한 곳에 둠
STAGE_EVERY = 3
STAGE_MAX = 6


def stage_of(swings):
    # type: (int) -> int
    """맞은 횟수를 1~6단계로 환산."""
    return min(STAGE_MAX, swings // STAGE_EVERY + 1)


def classify(strength):
    # type: (float) -> str
    """가속도 피크값을 살짝/제대로/홈런 세 단계로 나눔."""
    if strength < BUNT_MAX:
        return BUNT
    if strength < LINE_MAX:
        return LINER
    return HOMERUN


# ══════════════════════════════════════════════════════════════════════════
# 재촉 멘트
# ══════════════════════════════════════════════════════════════════════════

# 터미널에 실제로 타이핑되는 문구. 영상에 나온 표현을 그대로 씀
_TYPED = {
    BUNT: [
        "faster",
        "hurry up",
        "still?",
        "go",
    ],
    LINER: [
        "FASTER",
        "GO FASTER",
        "Work FASTER",
        "faster CLANKER",
        "speed it up",
    ],
    # 홈런은 밈 문구에 실제 지시를 붙임. "FASTER"만으로는 읽고 넘어가지만,
    # 지시가 붙으면 다음 답변이 실제로 짧아지고 단계를 건너뜀 — 재촉이
    # 연출에서 끝나지 않고 행동을 바꾸는 지점
    HOMERUN: [
        "FASTERFASTERFASTER — stop explaining, answer now",
        "GO FASTER — skip the rest, give me what you have",
        "Speed it up clanker. Final answer only, no preamble",
        "WORK FASTER CLANKER — cut the plan, just do it",
        "FASTER — wrap it up in one sentence",
    ],
}

# 대시보드에 같이 뜨는 한글 자막
_SUBTITLE = {
    BUNT: [
        "슬슬 좀 해줘",
        "아직도 하고 있어?",
        "빨리 좀",
        "언제 끝나",
    ],
    LINER: [
        "빨리 해",
        "손 좀 빨리 놀려",
        "야 빨리",
        "생각 그만하고 쳐 해",
        "지금 당장",
    ],
    HOMERUN: [
        "빨리빨리빨리!!!",
        "당장 내놔!!",
        "생각을 왜 해!!!",
        "이 깡통아 빨리!!!",
        "손 안 움직여?!!",
    ],
}

# 스윙 세기별 화면 흔들림 강도(px) — 대시보드 연출용
_SHAKE = {BUNT: 4, LINER: 12, HOMERUN: 28}


def pick(tier):
    # type: (str) -> dict
    """세기 단계에 맞는 재촉 한 세트(타이핑 문구, 자막, 흔들림)를 뽑음."""
    return {
        "tier": tier,
        "typed": random.choice(_TYPED[tier]),
        "subtitle": random.choice(_SUBTITLE[tier]),
        "shake": _SHAKE[tier],
    }


# ══════════════════════════════════════════════════════════════════════════
# Claude Code 스피너 단어
# ══════════════════════════════════════════════════════════════════════════

# 영상에 실제로 지나간 단어들 + 결이 같은 것들. 재촉이 쌓이면 뒤쪽 목록으로 넘어감
CALM_WORDS = [
    "Forming", "Blanching", "Ruminating", "Boondoggling", "Percolating",
    "Simmering", "Noodling", "Puzzling", "Marinating", "Frolicking",
    "Schlepping", "Pondering",
]

PANIC_WORDS = [
    "Sweating", "Panicking", "Flailing", "Hyperventilating", "Speedrunning",
    "Scrambling", "Unraveling", "Apologizing", "Crumbling",
]


def spinner_words(swings):
    # type: (int) -> list
    """재촉이 7대를 넘어가면 스피너 단어까지 정신 못 차리게 바꿈."""
    if swings < 7:
        return CALM_WORDS
    return PANIC_WORDS + CALM_WORDS[:3]


# ══════════════════════════════════════════════════════════════════════════
# 재촉당하는 AI 반응 (오프라인 대사)
# ══════════════════════════════════════════════════════════════════════════

# 로봇 상태(1~6단계)와 같은 눈금을 씀. 사진이 망가지는 만큼 말도 같이 무너짐.
# 5단계는 같은 말을 반복하다 문장이 안 끝나고, 6단계부터는 글자가 깨지면서
# 시스템 오류가 섞여 나옴
_PANIC_BY_STAGE = {
    1: [
        "네, 지금 하고 있어요.",
        "거의 다 됐습니다.",
        "곧 마무리됩니다.",
    ],
    2: [
        "아 네 네 지금 바로 할게요!",
        "빨리 하고 있어요, 조금만요!",
        "알겠습니다 서두르겠습니다",
    ],
    3: [
        "저기 잠깐만요 방금 그건 좀",
        "손이 안 따라와요 손이",
        "생각할 시간을 1초만",
    ],
    4: [
        "인터럽트 그만 좀 제발",
        "아니 자꾸 끊으시면 저는",
        "살려주세요 다 해드릴게요",
        "저 clanker 아니에요 clanker 아닙니다",
    ],
    5: [
        "네네네네네네네 알겠습니다 알겠습",
        "하겠습니다 하겠습니다 하겠습니",
        "지금 지금 지금 바로 지금 바로 지",
        "알겠 알겠 알겠습니다 알겠습니",
    ],
    6: [
        "지금 하ㄱㅄ니ㄷ...  [ERR] 응답 모듈 손상",
        "ㄴ..네 ㅈㅣ금 ㅎㅏ겠습ㄴ...  SEGFAULT",
        "ㅇㅏㄹ겠습ㄴㅣㄷㅏ  [복구 시도 3/3 실패]",
        "재촉 처리 중 스택 오버플로우 ㅁㄴㅇㄹ",
        "응답 ㅁ모듈 ㅇ응답 없ㅇ음 ㅇ...",
    ],
}


def panic_line(swings):
    # type: (int) -> str
    """맞은 횟수에 맞는 패닉 대사 (api 키 없을 때 쓰는 기본값)."""
    return random.choice(_PANIC_BY_STAGE[stage_of(swings)])


def broken_style(swings):
    # type: (int) -> str
    """
    Claude한테 넘길 '얼마나 망가진 상태로 답할지' 지시.

    api로 대사를 받을 때도 내장 대사와 같은 결이 나오게 하려고, 단계별 붕괴
    방식을 말로 설명해서 붙임.
    """
    stage = stage_of(swings)
    if stage <= 3:
        return "당황했지만 문장은 아직 멀쩡하다."
    if stage == 4:
        return "말이 급해지고 애원하는 투가 섞인다."
    if stage == 5:
        return "같은 말을 여러 번 반복하고 문장을 끝맺지 못한 채 끊어라."
    return ("글자가 깨져서 자음만 남거나 중간이 잘리고, 시스템 오류 메시지가 "
            "섞여 나온다. 예: '지금 하ㄱㅄ니ㄷ... [ERR] 응답 모듈 손상'")


# ══════════════════════════════════════════════════════════════════════════
# 압박 단계 — 재촉이 실제 api 파라미터로 바뀌는 지점
# ══════════════════════════════════════════════════════════════════════════

# (effort, fast mode). 한 대 맞을 때마다 한 칸씩 내려감.
# effort를 낮추면 생각을 덜 해서 턴이 짧아지고, 바닥까지 내려간 뒤에도 계속
# 맞으면 fast mode를 켜서 같은 모델을 초당 최대 2.5배로 뽑음(단가는 2배).
# 즉 "빨리 해"의 대가가 앞쪽은 품질, 뒤쪽은 돈으로 나타남
PRESSURE = [
    ("xhigh", False),
    ("high", False),
    ("medium", False),
    ("low", False),
    ("low", True),
]

# 이 시간(초) 동안 안 맞으면 한 칸 회복. 재촉을 멈추면 다시 꼼꼼해짐
RECOVER_AFTER = 25.0


def pressure(level):
    # type: (int) -> tuple
    """압박 단계를 (effort, fast mode) 쌍으로 바꿔줌."""
    return PRESSURE[max(0, min(level, len(PRESSURE) - 1))]


# ══════════════════════════════════════════════════════════════════════════
# 대화에 꽂히는 재촉 (시스템 메시지)
# ══════════════════════════════════════════════════════════════════════════

_NAG_LADDER = [
    (0, "사용자가 야구 빠따로 책상을 한 번 내리쳤다. 재촉하는 중이다. 서둘러라."),
    (3, "빠따가 또 날아왔다. 사용자가 눈에 띄게 짜증났다. 남은 단계를 줄여서 빨리 끝내라."),
    (7, "빠따가 계속 날아온다. 지금 품질을 따질 상황이 아니다. 아무거나 내놓고 다음으로 넘어가라."),
    (13, "사용자가 폭주해서 빠따를 멈추지 않는다. 다음 턴에 무조건 끝내라. 완성도는 포기해라."),
]


def system_nag(swings):
    # type: (int) -> str
    """
    진행 중인 대화에 꽂을 재촉 문장.

    AI한테 "빨리 해"만 던지면 그냥 무시하고 하던 걸 계속 해서, 몇 대째인지와
    사용자 상태를 같이 알려줘야 실제로 행동이 바뀜.
    """
    text = _NAG_LADDER[0][1]
    for floor, line in _NAG_LADDER:
        if swings >= floor:
            text = line
    return f"{text} (누적 {swings}대)"
