"""
═══════════════════════════════════════════════════════════════════════════
 QR 코드 생성 (표준 라이브러리만)

 설명    : 폰으로 빠따 페이지를 열 때 주소를 손으로 치는 게 번거로워서 대시보드에
           QR을 띄움. 배포본이 pip 설치 없이 돌아야 해서 인코더를 직접 구현함
 실행환경: python 3.9+, 표준 라이브러리만
 작성자  : 정한결
 변경내역:
   v1 - 바이트 모드, 오류정정 M, 버전 1~3
 이슈 사항:
   - 버전 1~3만 지원함. 이 범위는 블록이 1개라 인터리빙이 필요 없어서 구현이
     훨씬 단순해짐. 담는 건 LAN 주소(길어야 30자 남짓)뿐이고 3-M이 42바이트라
     넉넉함. 더 긴 걸 넣으려 하면 예외를 냄
   - 숫자/영숫자 모드는 안 넣음. 주소에 :/. 가 섞여 어차피 바이트 모드로 가고,
     모드를 늘리면 코드만 길어짐
═══════════════════════════════════════════════════════════════════════════
"""


class QRError(Exception):
    """담을 수 없는 문자열이 들어왔을 때."""


# ══════════════════════════════════════════════════════════════════════════
# 버전별 제원 (오류정정 M, 블록 1개)
# ══════════════════════════════════════════════════════════════════════════

# version: (데이터 코드워드 수, 오류정정 코드워드 수, 정렬 패턴 중심)
_SPECS = {
    1: (16, 10, None),
    2: (28, 16, 18),
    3: (44, 26, 22),
}

# 마스크별 형식 정보 15비트 (오류정정 M 고정). BCH(15,5) + 마스킹까지 끝난 값
_FORMAT_BITS = {
    0: 0x5412, 1: 0x5125, 2: 0x5E7C, 3: 0x5B4B,
    4: 0x45F9, 5: 0x40CE, 6: 0x4F97, 7: 0x4AA0,
}


# ══════════════════════════════════════════════════════════════════════════
# GF(256) 산술 — 리드솔로몬용
# ══════════════════════════════════════════════════════════════════════════

_EXP = [0] * 512
_LOG = [0] * 256


def _init_tables():
    """QR이 쓰는 원시다항식 0x11D로 지수/로그 표를 만들어 둠."""
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_init_tables()


def _mul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _generator(n):
    """오류정정 코드워드 n개짜리 생성다항식."""
    poly = [1]
    for i in range(n):
        nxt = [0] * (len(poly) + 1)
        for j, c in enumerate(poly):
            nxt[j] ^= c
            nxt[j + 1] ^= _mul(c, _EXP[i])
        poly = nxt
    return poly


def _ec_codewords(data, n):
    """데이터 코드워드에 붙일 오류정정 코드워드 n개를 계산."""
    gen = _generator(n)
    rem = list(data) + [0] * n
    for i in range(len(data)):
        factor = rem[i]
        if factor == 0:
            continue
        for j, g in enumerate(gen):
            rem[i + j] ^= _mul(g, factor)
    return rem[len(data):]


# ══════════════════════════════════════════════════════════════════════════
# 데이터 인코딩
# ══════════════════════════════════════════════════════════════════════════

def _pick_version(nbytes):
    for v in sorted(_SPECS):
        data_cw = _SPECS[v][0]
        if nbytes + 2 <= data_cw:      # 모드 4비트 + 길이 8비트 = 1.5바이트, 넉넉히 2
            return v
    raise QRError(f"{nbytes}바이트는 이 구현(버전 1~3)으로 못 담음")


def _encode(text, version):
    """바이트 모드로 비트열을 만들고 데이터 코드워드까지 채움."""
    raw = text.encode("utf-8")
    data_cw = _SPECS[version][0]
    bits = []

    def put(value, length):
        for i in range(length - 1, -1, -1):
            bits.append((value >> i) & 1)

    put(0b0100, 4)          # 바이트 모드
    put(len(raw), 8)        # 버전 1~9는 길이 필드가 8비트
    for b in raw:
        put(b, 8)

    capacity = data_cw * 8
    if len(bits) > capacity:
        raise QRError("데이터가 버전 용량을 넘음")

    put(0, min(4, capacity - len(bits)))            # 종단자
    while len(bits) % 8:                            # 바이트 경계 맞춤
        bits.append(0)

    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = [0xEC, 0x11]
    while len(words) < data_cw:                     # 남는 자리는 규정된 패딩으로
        words.append(pad[(len(words) - len(bits) // 8) % 2])
    return words


# ══════════════════════════════════════════════════════════════════════════
# 매트릭스 배치
# ══════════════════════════════════════════════════════════════════════════

def _blank(size):
    return [[0] * size for _ in range(size)]


def _place_finder(m, fixed, r0, c0):
    """7x7 파인더 패턴 + 분리자. 여기 칠한 자리는 전부 기능 패턴으로 표시."""
    size = len(m)
    for dr in range(-1, 8):
        for dc in range(-1, 8):
            r, c = r0 + dr, c0 + dc
            if not (0 <= r < size and 0 <= c < size):
                continue
            inside = 0 <= dr <= 6 and 0 <= dc <= 6
            if not inside:
                m[r][c] = 0                          # 분리자(흰 테두리)
            else:
                edge = dr in (0, 6) or dc in (0, 6)
                core = 2 <= dr <= 4 and 2 <= dc <= 4
                m[r][c] = 1 if (edge or core) else 0
            fixed[r][c] = True


def _place_static(m, fixed, version):
    """파인더, 타이밍, 정렬 패턴과 항상 검은 모듈."""
    size = len(m)
    _place_finder(m, fixed, 0, 0)
    _place_finder(m, fixed, 0, size - 7)
    _place_finder(m, fixed, size - 7, 0)

    for i in range(8, size - 8):
        bit = 1 if i % 2 == 0 else 0
        m[6][i] = bit
        fixed[6][i] = True
        m[i][6] = bit
        fixed[i][6] = True

    center = _SPECS[version][2]
    if center is not None:
        for dr in range(-2, 3):
            for dc in range(-2, 3):
                ring = max(abs(dr), abs(dc))
                m[center + dr][center + dc] = 1 if ring != 1 else 0
                fixed[center + dr][center + dc] = True

    m[size - 8][8] = 1
    fixed[size - 8][8] = True


def _reserve_format(fixed):
    """형식 정보 자리를 기능 패턴으로 표시해 데이터가 안 들어가게 함."""
    size = len(fixed)
    for i in range(9):
        fixed[8][i] = True
        fixed[i][8] = True
    for i in range(8):
        fixed[8][size - 1 - i] = True
        fixed[size - 1 - i][8] = True


def _place_data(m, fixed, words):
    """오른쪽 아래에서 지그재그로 올라가며 비트를 채움. 6번 열은 건너뜀."""
    size = len(m)
    bits = []
    for w in words:
        for i in range(7, -1, -1):
            bits.append((w >> i) & 1)

    idx = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (col, col - 1):
                if not fixed[row][c]:
                    m[row][c] = bits[idx] if idx < len(bits) else 0
                    idx += 1
        upward = not upward
        col -= 2


def _mask_bit(mask, r, c):
    if mask == 0:
        return (r + c) % 2 == 0
    if mask == 1:
        return r % 2 == 0
    if mask == 2:
        return c % 3 == 0
    if mask == 3:
        return (r + c) % 3 == 0
    if mask == 4:
        return (r // 2 + c // 3) % 2 == 0
    if mask == 5:
        return (r * c) % 2 + (r * c) % 3 == 0
    if mask == 6:
        return ((r * c) % 2 + (r * c) % 3) % 2 == 0
    return ((r + c) % 2 + (r * c) % 3) % 2 == 0


def _penalty(g):
    """마스크 고르기용 감점. 규격의 네 규칙을 그대로 계산함."""
    size = len(g)
    score = 0

    for line in [list(row) for row in g] + [list(col) for col in zip(*g)]:
        run, prev = 1, line[0]
        for v in line[1:]:
            if v == prev:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run, prev = 1, v
        if run >= 5:
            score += 3 + (run - 5)

    for r in range(size - 1):
        for c in range(size - 1):
            if g[r][c] == g[r][c + 1] == g[r + 1][c] == g[r + 1][c + 1]:
                score += 3

    pat1 = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    pat2 = pat1[::-1]
    for line in [list(row) for row in g] + [list(col) for col in zip(*g)]:
        for i in range(size - 10):
            seg = line[i:i + 11]
            if seg == pat1 or seg == pat2:
                score += 40

    dark = sum(sum(row) for row in g)
    ratio = dark * 100 // (size * size)
    score += 10 * (abs(ratio - 50) // 5)
    return score


def _apply_format(g, mask):
    """
    형식 정보 15비트를 두 벌 배치.

    좌상단 사본은 8번 '열'을 따라 세로로 먼저 내려온 뒤 8번 '행'으로 꺾임.
    행과 열을 바꿔 넣으면 코드가 통째로 안 읽힘 (여기서 한 번 틀렸음)
    """
    size = len(g)
    bits = _FORMAT_BITS[mask]
    for i in range(15):
        bit = (bits >> i) & 1
        if i < 6:
            g[i][8] = bit
        elif i == 6:
            g[7][8] = bit
        elif i == 7:
            g[8][8] = bit
        elif i == 8:
            g[8][7] = bit
        else:
            g[8][14 - i] = bit
        if i < 8:
            g[8][size - 1 - i] = bit
        else:
            g[size - 15 + i][8] = bit


def make(text):
    """
    문자열을 QR 매트릭스(0/1 2차원 리스트)로 만들어 돌려줌.

    마스크 8개를 전부 만들어 보고 규격의 감점 규칙으로 제일 나은 걸 고름 —
    스캔 실패를 줄이는 표준 절차라 생략하지 않음.
    """
    version = _pick_version(len(text.encode("utf-8")))
    ec_cw = _SPECS[version][1]
    data = _encode(text, version)
    words = data + _ec_codewords(data, ec_cw)

    size = 17 + 4 * version
    base = _blank(size)
    fixed = [[False] * size for _ in range(size)]
    _place_static(base, fixed, version)
    _reserve_format(fixed)
    _place_data(base, fixed, words)

    best, best_score = None, None
    for mask in range(8):
        g = [row[:] for row in base]
        for r in range(size):
            for c in range(size):
                if not fixed[r][c] and _mask_bit(mask, r, c):
                    g[r][c] ^= 1
        _apply_format(g, mask)
        s = _penalty(g)
        if best_score is None or s < best_score:
            best, best_score = g, s
    return best


def to_svg(text, quiet=4, module=6):
    """QR을 SVG 문자열로. quiet은 규격이 요구하는 여백(모듈 단위)."""
    g = make(text)
    n = len(g)
    total = (n + quiet * 2) * module
    rects = []
    for r in range(n):
        for c in range(n):
            if g[r][c]:
                x = (c + quiet) * module
                y = (r + quiet) * module
                rects.append(f'<rect x="{x}" y="{y}" width="{module}" height="{module}"/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="{total}" '
        f'viewBox="0 0 {total} {total}" shape-rendering="crispEdges">'
        f'<rect width="{total}" height="{total}" fill="#fff"/>'
        f'<g fill="#000">{"".join(rects)}</g></svg>'
    )
