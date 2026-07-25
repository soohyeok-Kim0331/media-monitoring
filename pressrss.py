# -*- coding: utf-8 -*-
"""
언론사 '전체기사' RSS 수집 모듈 (1차 소스).

구글 뉴스(gnews.py)와 나란히 쓰이며, 신뢰 언론사의 원문 기사를 직접 가져옵니다.
- 원문 URL을 바로 주므로 링크 복원이 필요 없습니다.
- description에 본문 요약이 들어 있어 선별 품질이 올라갑니다.
- 표준 라이브러리(urllib, xml.etree)만 사용합니다.

전체기사 피드는 키워드로 걸러진 게 아니라 그 매체의 최신 기사 전부를 주므로,
호출하는 쪽에서 카테고리 키워드로 필터링해야 합니다.
"""

import re
import html
import urllib.request
import xml.etree.ElementTree as ET

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

TAG_RE = re.compile(r"<[^>]+>")

# 같은 실행 안에서 같은 피드를 여러 카테고리가 요청하므로 결과를 캐시합니다.
_CACHE = {}


def _strip_html(text):
    return html.unescape(TAG_RE.sub("", text or "")).strip()


def _channel_matches(channel_title, expected_press):
    """
    채널 제목이 기대한 매체인지 확인합니다.
    섹션 피드가 다른 매체(예: '파인데일리')로 폴백되는 사고를 막습니다.
    표기가 조금씩 달라서(예: 'IMPACT ON(임팩트온)') 부분 포함으로 봅니다.
    """
    ct = (channel_title or "").replace(" ", "")
    ep = expected_press.replace(" ", "")
    core = ep.replace("신문", "").replace("코리아", "")
    return ep in ct or core in ct


def fetch(feed_url, expected_press, timeout=20):
    """
    RSS 피드 하나를 읽어 기사 dict 리스트를 반환합니다.
    실패하거나 채널명이 기대와 다르면 빈 리스트를 반환합니다(파이프라인 중단 방지).
    """
    if feed_url in _CACHE:
        return _CACHE[feed_url]

    req = urllib.request.Request(feed_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            root = ET.fromstring(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        print(f"[ERROR] RSS 수집 실패 ({expected_press}): {e}")
        _CACHE[feed_url] = []
        return []

    channel = root.find("./channel")
    ch_title = channel.findtext("title") if channel is not None else ""
    if not _channel_matches(ch_title, expected_press):
        print(f"[WARN] RSS 채널명 불일치 ({expected_press} 기대, '{ch_title}' 수신) — 건너뜀")
        _CACHE[feed_url] = []
        return []

    results = []
    for item in root.iterfind("./channel/item"):
        title = _strip_html(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        results.append({
            "title": title,
            "link": link,                              # 원문 URL (복원 불필요)
            "pubDate": (item.findtext("pubDate") or "").strip(),
            "press": expected_press,
            "description": _strip_html(item.findtext("description")),
        })

    _CACHE[feed_url] = results
    return results


if __name__ == "__main__":
    from config import PRESS_RSS
    for name, url in PRESS_RSS.items():
        items = fetch(url, name)
        head = items[0]["title"] if items else "(없음)"
        print(f"{name:12s} {len(items):3d}건  예: {head[:40]}")
