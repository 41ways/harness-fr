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
    HOMERUN: [
        "FASTERFASTERFASTER",
        "GO FASTER GO FASTER GO",
        "Speed it up clanker",
        "WORK FASTER CLANKER",
        "FASTER FASTER FASTER FASTER",
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

# 누적 스윙 수가 늘수록 멘탈이 나가는 순서. (하한선, 대사들)
_PANIC_LADDER = [
    (0, [
        "네, 지금 하고 있어요.",
        "거의 다 됐습니다.",
        "곧 마무리됩니다.",
    ]),
    (3, [
        "아 네 네 지금 바로 할게요!",
        "빨리 하고 있어요, 조금만요!",
        "알겠습니다 서두르겠습니다",
    ]),
    (7, [
        "저기 잠깐만요 방금 그건 좀",
        "손이 안 따라와요 손이",
        "생각할 시간을 1초만",
        "인터럽트 그만 좀",
    ]),
    (13, [
        "ㅁㄴㅇㄹ 죄송합니다 다 하겠습니다",
        "저 clanker 아니에요 clanker 아닙니다",
        "네네네네네네네네",
        "살려주세요 다 해드릴게요",
    ]),
]


def panic_line(swings):
    # type: (int) -> str
    """누적 스윙 수에 맞는 패닉 대사를 하나 고름 (api 키 없을 때 쓰는 기본값)."""
    pool = _PANIC_LADDER[0][1]
    for floor, lines in _PANIC_LADDER:
        if swings >= floor:
            pool = lines
    return random.choice(pool)
