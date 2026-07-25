# -*- coding: utf-8 -*-
"""
Google News RSS 수집 모듈.

- API 키가 필요 없고 할당량 제한도 없습니다.
- 표준 라이브러리(urllib, xml.etree)만 사용합니다.

RSS가 돌려주는 <link>는 news.google.com/rss/articles/CBMi... 형태의 인코딩 URL입니다.
사람이 브라우저에서 클릭하면 원문으로 잘 이동하지만, 그 상태로는 원문 도메인을 알 수 없어
resolve_url()로 원문 주소를 복원합니다. 복원은 구글의 비공개 내부 엔드포인트를 쓰기 때문에
언제든 막힐 수 있고, 그럴 때는 원본 구글 링크를 그대로 돌려줍니다.
"""

import re
import json
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

RSS_ENDPOINT = "https://news.google.com/rss/search"
BATCH_ENDPOINT = "https://news.google.com/_/DotsSplashUi/data/batchexecute"

# urllib 기본 UA로는 구글이 막는 경우가 있어 브라우저 UA를 사용합니다.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# 셸 페이지에서 원문 복원에 필요한 서명값을 뽑는 정규식
_SIG_ID = re.compile(r'data-n-a-id="([^"]+)"')
_SIG_TS = re.compile(r'data-n-a-ts="([^"]+)"')
_SIG_SG = re.compile(r'data-n-a-sg="([^"]+)"')

# batchexecute 응답을 정식 파싱하지 못했을 때 쓰는 폴백 정규식.
# 응답 본문에서는 = 가 \\u003d 처럼 백슬래시 두 개로 이스케이프돼 있으므로
# 백슬래시 1~2개를 모두 허용해야 URL 뒷부분이 잘리지 않는다.
_URL_IN_RESPONSE = re.compile(
    r'https?://(?!news\.google\.)(?:[^"\\\s]|\\{1,2}u[0-9a-fA-F]{4})+'
)
_UNICODE_ESCAPE = re.compile(r"\\{1,2}u([0-9a-fA-F]{4})")


def _get(url, data=None, timeout=20):
    headers = {"User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _unescape_unicode(text):
    """\\u003d 같은 이스케이프를 실제 문자로 되돌립니다."""
    return _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)


def _extract_url(resp):
    """
    batchexecute 응답에서 원문 URL을 꺼냅니다.

    응답은 )]}' 프리픽스 뒤에 JSON 배열이 오고, 그 안에 또 JSON 문자열이 들어 있는
    이중 구조입니다. 정규식으로 긁으면 \\u003d 같은 이중 이스케이프 때문에 URL이
    중간에 잘리므로, 두 번 파싱하는 정식 경로를 먼저 시도합니다.
    """
    try:
        body = resp[resp.index("["):]
        for row in json.loads(body):
            if (isinstance(row, list) and len(row) > 2
                    and row[0] == "wrb.fr" and isinstance(row[2], str)):
                for v in json.loads(row[2]):
                    if (isinstance(v, str) and v.startswith("http")
                            and "news.google." not in v):
                        return v
    except Exception:
        pass

    # 응답 구조가 바뀐 경우를 대비한 폴백
    m = _URL_IN_RESPONSE.search(resp)
    return _unescape_unicode(m.group(0)) if m else None


def _strip_press_suffix(title, press):
    """
    Google News는 제목 끝에 항상 ' - 언론사명'을 붙입니다.
    사내 공유 시 그대로 두면 지저분하므로 제거합니다.

    언론사가 자기 제목 끝에 이미 사명을 붙여둔 경우 구글이 하나 더 붙여서
    '...추락사 - 머니투데이 - 머니투데이'가 되므로 반복해서 떼어냅니다.
    """
    if press:
        suffix = " - " + press
        while title.endswith(suffix):
            title = title[: -len(suffix)].strip()
        return title
    # <source>가 비어 있을 때만 쓰는 보수적 폴백.
    # (press를 아는 경우엔 쓰지 않습니다 — 제목 본문의 '-'까지 잘라낼 위험이 있음)
    return re.sub(r"\s+-\s+[^-]{1,20}$", "", title).strip()


def search(query, window=None, timeout=20):
    """
    Google News RSS 검색. 실패해도 예외를 던지지 않고 빈 리스트를 반환합니다.

    query  : 검색어. 'site:' / 'when:7d' 같은 구글 연산자를 그대로 넣어도 됩니다.
    window : 'when:7d' 처럼 뒤에 덧붙일 기간 연산자 (선택)
    """
    q = query if not window else f"{query} {window}"
    url = RSS_ENDPOINT + "?" + urllib.parse.urlencode(
        {"q": q, "hl": "ko", "gl": "KR", "ceid": "KR:ko"}
    )
    try:
        raw = _get(url, timeout=timeout)
        root = ET.fromstring(raw)
    except Exception as e:
        print(f"[ERROR] 구글 뉴스 검색 실패 (q={q}): {e}")
        return []

    results = []
    for item in root.iterfind("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        source_el = item.find("source")
        press = (source_el.text or "").strip() if source_el is not None else ""
        press_url = source_el.get("url", "") if source_el is not None else ""
        results.append({
            "title": _strip_press_suffix(title, press),
            "google_link": link,
            "pubDate": (item.findtext("pubDate") or "").strip(),
            "press": press,
            "press_url": press_url,
        })
    return results


def resolve_url(google_link, timeout=20):
    """
    구글 뉴스 인코딩 링크에서 원문 기사 URL을 복원합니다.
    어떤 단계에서 실패하든 예외를 내지 않고 입력받은 구글 링크를 그대로 반환합니다.
    """
    try:
        shell = _get(google_link, timeout=timeout)
        m_id = _SIG_ID.search(shell)
        m_ts = _SIG_TS.search(shell)
        m_sg = _SIG_SG.search(shell)
        if not (m_id and m_ts and m_sg):
            return google_link

        inner = json.dumps([
            "garturlreq",
            [
                ["X", "X", ["X", "X"], None, None, 1, 1, "US:en",
                 None, 1, None, None, None, None, None, 0, 1],
                "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0,
            ],
            m_id.group(1), int(m_ts.group(1)), m_sg.group(1),
        ], separators=(",", ":"))
        payload = json.dumps(
            [[["Fbv4je", inner, None, "generic"]]], separators=(",", ":")
        )
        body = urllib.parse.urlencode({"f.req": payload}).encode("utf-8")

        resp = _get(BATCH_ENDPOINT, data=body, timeout=timeout)
        return _extract_url(resp) or google_link
    except Exception as e:
        print(f"[WARN] 원문 URL 복원 실패, 구글 링크 사용: {e}")
        return google_link
    finally:
        # 구글에 과도한 요청을 보내지 않기 위한 간격
        time.sleep(0.3)


def domain_of(url):
    """URL에서 호스트명만 뽑습니다 (www. 제거)."""
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


if __name__ == "__main__":
    # 단독 실행 시 동작 확인용
    items = search("탄소중립", window="when:7d")
    print(f"수집 {len(items)}건\n")
    for it in items[:3]:
        print(f"제목    : {it['title']}")
        print(f"언론사  : {it['press']}  ({domain_of(it['press_url'])})")
        print(f"게재    : {it['pubDate']}")
        real = resolve_url(it["google_link"])
        print(f"원문URL : {real}")
        print(f"복원성공: {'news.google.' not in real}")
        print("-" * 60)
