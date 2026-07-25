# -*- coding: utf-8 -*-
"""
Anthropic API 키가 제대로 설정됐는지 확인하는 진단 스크립트.

전체 크롤링을 돌리기 전에 이걸 먼저 실행해서 키 문제를 걸러내세요.

    python check_key.py

키는 아래 순서로 찾습니다.
  1) 같은 폴더의 .env 파일
  2) 환경변수 ANTHROPIC_API_KEY
"""

import os
import json
import urllib.error
import urllib.request

from crawl_and_select import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, ROOT


def mask(key):
    """키를 로그에 그대로 찍지 않도록 가립니다."""
    if len(key) <= 14:
        return "*" * len(key)
    return f"{key[:11]}...{key[-4:]}"


def main():
    print("=" * 58)
    print(" Anthropic API 키 진단")
    print("=" * 58)

    env_path = os.path.join(ROOT, ".env")
    print(f"\n[1] .env 파일        : {'있음' if os.path.exists(env_path) else '없음'}  ({env_path})")

    if not ANTHROPIC_API_KEY:
        print("[2] 키 인식          : 실패 — 키를 찾지 못했습니다.\n")
        print("해결 방법")
        print("  1. https://console.anthropic.com 접속 -> 로그인")
        print("  2. 좌측 'API keys' -> 'Create Key' -> 키 복사")
        print("     (키는 만든 직후 한 번만 보입니다. 반드시 복사해두세요)")
        print("  3. 결제수단 등록: 좌측 'Billing' -> 카드 등록 후 크레딧 충전")
        print("     (무료 크레딧이 없으면 키가 있어도 호출이 거부됩니다)")
        print("  4. 이 폴더에서 아래 실행:")
        print("       Copy-Item .env.example .env")
        print("     그리고 .env 를 메모장으로 열어 키를 붙여넣고 저장")
        print("  5. 다시 python check_key.py 실행")
        return 1

    print(f"[2] 키 인식          : 성공  ({mask(ANTHROPIC_API_KEY)})")

    if not ANTHROPIC_API_KEY.startswith("sk-ant-"):
        print("      ! 경고: 보통 키는 'sk-ant-' 로 시작합니다. 값을 다시 확인해보세요.")
    if "여기에" in ANTHROPIC_API_KEY:
        print("[3] 실제 호출        : 건너뜀 — .env 의 예시 문구를 아직 실제 키로 바꾸지 않았습니다.")
        return 1

    print(f"[3] 실제 호출        : {ANTHROPIC_MODEL} 로 테스트 중...")

    body = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "핑"}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("x-api-key", ANTHROPIC_API_KEY)
    req.add_header("anthropic-version", "2023-06-01")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        usage = data.get("usage", {})
        print("\n성공! 키가 정상 동작합니다.")
        print(f"  응답 모델 : {data.get('model')}")
        print(f"  토큰 사용 : 입력 {usage.get('input_tokens')} / 출력 {usage.get('output_tokens')}")
        print("\n이제 'python crawl_and_select.py' 를 실행하면 AI 선별이 적용됩니다.")
        return 0

    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        print(f"\n실패 — HTTP {e.code}")
        if e.code == 401:
            print("  원인: 키가 잘못됐거나 삭제된 키입니다.")
            print("  해결: 콘솔에서 키를 새로 만들어 .env 에 다시 붙여넣으세요.")
            print("        앞뒤 공백이나 따옴표가 섞이지 않았는지도 확인하세요.")
        elif e.code == 400 and "credit" in detail.lower():
            print("  원인: 크레딧이 부족합니다.")
            print("  해결: 콘솔 -> Billing 에서 결제수단 등록 후 크레딧을 충전하세요.")
        elif e.code == 404:
            print(f"  원인: 모델 이름 '{ANTHROPIC_MODEL}' 을 찾을 수 없습니다.")
            print("  해결: .env 의 ANTHROPIC_MODEL 줄을 지우면 기본값이 쓰입니다.")
        elif e.code == 429:
            print("  원인: 요청 한도 초과입니다. 잠시 뒤 다시 시도하세요.")
        print(f"\n  서버 응답: {detail[:300]}")
        return 1

    except Exception as e:
        print(f"\n실패 — {type(e).__name__}: {e}")
        print("  네트워크나 사내 방화벽이 api.anthropic.com 접속을 막고 있을 수 있습니다.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
