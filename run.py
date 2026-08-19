#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════
 clanker-bat — 야구 빠따로 AI를 재촉하는 프로그램

 설명    : 아이폰을 빠따처럼 쥐고 휘두르면 그 스윙이 터미널에서 돌아가는 AI를
           실제로 인터럽트하고, 대시보드에서는 재촉당한 AI가 허둥대는 걸 볼 수
           있음. 이 파일은 진입점 — ip 찾고, 인증서 만들고, 서버 띄움
 실행환경: macOS, python 3.9+ (표준 라이브러리만), openssl
 작성자  : 정한결
 변경내역:
   v1 - 인자 파싱, LAN ip 자동 탐지, 인증서 준비, 서버 기동
 이슈 사항:
   - 종료(sys.exit)는 여기서만 함. 아래 모듈들은 예외만 올림
   - ip 자동 탐지가 틀리면 --host로 직접 넣으면 됨 (vpn 켜져 있으면 종종 틀림)
═══════════════════════════════════════════════════════════════════════════
"""

import argparse
import os
import socket
import sys
import threading

from bat import cert, nag, server, victim

CERT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs")


# ══════════════════════════════════════════════════════════════════════════
# 준비
# ══════════════════════════════════════════════════════════════════════════

def lan_ip():
    # type: () -> str
    """
    아이폰이 접속할 이 맥의 LAN ip를 찾음.

    외부로 UDP 소켓을 '연결'만 해보면 커널이 알아서 나가는 인터페이스를 골라줌.
    실제로 패킷을 보내지는 않아서 인터넷이 없어도 됨.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def parse_args():
    p = argparse.ArgumentParser(description="야구 빠따로 AI 재촉하기")
    p.add_argument("--host", default=None, help="이 맥의 LAN ip (기본: 자동 탐지)")
    p.add_argument("--port", type=int, default=8443, help="포트 (기본: 8443)")
    p.add_argument("--target", default="Terminal",
                   help="재촉을 타이핑할 앱 이름 (기본: Terminal, 예: iTerm2, Ghostty)")
    p.add_argument("--model", default="claude-opus-5",
                   help="AI 반응을 만들 모델 (기본: claude-opus-5)")
    return p.parse_args()


# ══════════════════════════════════════════════════════════════════════════
# 진입점
# ══════════════════════════════════════════════════════════════════════════

def main():
    args = parse_args()
    host = args.host or lan_ip()

    certfile, keyfile = cert.ensure_cert(host, CERT_DIR)
    client = nag.make_client()
    heckler = nag.Heckler(model=args.model, client=client)

    hub = server.Hub(heckler, args.target)
    hub.victim = victim.Victim(hub, args.model, client)
    hub.phone_url = f"https://{host}:{args.port}"

    threading.Thread(target=hub.watch_grab, daemon=True).start()

    https_srv = server.build_https(args.port, hub, certfile, keyfile)
    ext_port = args.port + 1
    plain_srv = server.build_plain(ext_port, hub)
    threading.Thread(target=plain_srv.serve_forever, daemon=True).start()

    base = f"https://{host}:{args.port}"
    ai_mode = f"Claude {args.model}" if heckler.live else "내장 대사 (api 키 없음)"
    print("")
    print("  빠따 준비 완료")
    print("  ─────────────────────────────────────────────")
    print(f"  아이폰(빠따) : {base}   (대시보드 QR을 찍어도 됨)")
    print(f"  맥(대시보드) : {base}/dash")
    print(f"  크롬 확장    : http://127.0.0.1:{ext_port}  (확장이 알아서 붙음)")
    print(f"  재촉 대상 앱 : {args.target}   (대시보드에서 켜야 실제로 타이핑됨)")
    print(f"  AI 반응      : {ai_mode}")
    print("")
    print("  아이폰 사파리에서 인증서 경고가 뜨면 '자세히 보기 > 웹사이트 방문'")
    print("  Ctrl+C 로 종료")
    print("")

    try:
        https_srv.serve_forever()
    except KeyboardInterrupt:
        print(f"\n  경기 종료. 총 {hub.swings}대 쳤음.")
    finally:
        https_srv.server_close()
        plain_srv.server_close()


if __name__ == "__main__":
    try:
        main()
    except cert.CertError as e:
        print(f"인증서 준비 실패: {e}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"서버를 못 띄움: {e}", file=sys.stderr)
        sys.exit(1)
