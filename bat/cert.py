"""
═══════════════════════════════════════════════════════════════════════════
 self-signed 인증서 자동 생성

 설명    : 아이폰 사파리에서 DeviceMotion(가속도 센서)을 쓰려면 https가 필수라
           로컬 LAN ip용 self-signed 인증서를 만들어 둠. SAN에 ip랑 localhost를
           같이 박아야 사파리가 "이 사이트로 계속" 버튼을 내줌
 실행환경: macOS + openssl (homebrew, /usr/bin 둘 다 됨), python 3.9+
 작성자  : 정한결
 변경내역:
   v1 - openssl로 cert/key 생성, certs/에 캐시. 이미 있으면 재사용
 이슈 사항:
   - 인증서를 시스템 키체인에 자동 등록하는 건 범위 밖. 관리자 권한이 필요하고
     사용자 기기 신뢰 설정을 코드가 건드리는 건 과함. 사파리 경고 한 번
     넘기면 되는 수준이라 수동 승인으로 둠
   - 인증서 유효기간 갱신 로직 없음. 825일이라 실습용으론 충분
═══════════════════════════════════════════════════════════════════════════
"""

import os
import subprocess


class CertError(Exception):
    """인증서 생성이나 openssl 호출이 실패했을 때."""


# ══════════════════════════════════════════════════════════════════════════
# 인증서 생성
# ══════════════════════════════════════════════════════════════════════════

_CONF_TEMPLATE = """
[req]
distinguished_name = dn
x509_extensions = v3
prompt = no

[dn]
CN = {host}

[v3]
subjectAltName = @san
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth

[san]
IP.1 = {host}
IP.2 = 127.0.0.1
DNS.1 = localhost
"""


def ensure_cert(host, out_dir):
    # type: (str, str) -> tuple
    """
    host(LAN ip)용 self-signed 인증서를 만들어서 (cert경로, key경로)를 돌려줌.

    ip마다 파일명을 나눠서 캐시함 — 와이파이 바뀌어서 ip가 달라져도 예전 인증서를
    잘못 재사용하는 일이 없게 하려고. openssl 한 방으로 key+cert를 같이 뽑음.
    """
    os.makedirs(out_dir, exist_ok=True)
    cert_path = os.path.join(out_dir, f"{host}.crt")
    key_path = os.path.join(out_dir, f"{host}.key")
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return cert_path, key_path

    conf_path = os.path.join(out_dir, f"{host}.cnf")
    with open(conf_path, "w") as f:
        f.write(_CONF_TEMPLATE.format(host=host))

    cmd = [
        "openssl", "req", "-x509", "-nodes",
        "-newkey", "rsa:2048",
        "-days", "825",
        "-keyout", key_path,
        "-out", cert_path,
        "-config", conf_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except FileNotFoundError as e:
        raise CertError("openssl이 없음. `brew install openssl` 하고 다시") from e
    except subprocess.CalledProcessError as e:
        detail = e.stderr.decode("utf-8", "replace").strip()
        raise CertError(f"openssl이 인증서 생성에 실패함: {detail}") from e

    return cert_path, key_path
