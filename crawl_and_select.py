# -*- coding: utf-8 -*-
"""
미디어 모니터링 메인 스크립트.

[일반 모드]  REFINE_PAYLOAD 환경변수가 없으면 실행.
  1) 신뢰 언론사 RSS(1차) + Google News(2차)로 카테고리별 후보 수집
  2) 최신성 필터 + 사설/기고 제외 + 중복(이전 발행 이력) + 영구 제외 목록 필터
  3) Claude에게 카테고리별 3~4개 최종 선별 요청
  4) 선정 기사만 원문 URL 복원
  5) data/latest.json 저장, history.json 갱신, docs/index.html 생성

[재선별 모드] REFINE_PAYLOAD 환경변수가 있으면 실행 (GitHub Actions 수동 실행 입력).
  - 사이트에서 체크한 기사는 그대로 유지
  - 체크 안 한 기사는 영구 제외 목록(data/rejected.json)에 넣고, 빈 자리만 새 기사로 채움
"""

import os
import re
import json
import html
import time
import shutil
import datetime
import subprocess
import urllib.request
import urllib.parse

import gnews
import pressrss
import config
from config import (
    CATEGORIES, TITLE_TAG_PATTERN, OPINION_TITLE_PATTERN, EXCLUDE_SOURCES,
    LOCAL_PR_PATTERN, PRESS_DOMAINS, PRESS_RSS, SEARCH_WINDOW, CANDIDATES_PER_KEYWORD,
    MAX_RESOLVE_PER_CATEGORY, SELECT_MIN, SELECT_MAX,
    MAX_ARTICLE_AGE_DAYS, PREFERRED_ARTICLE_AGE_DAYS, DEDUP_WINDOW_DAYS,
)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

# 사이트 '재검색 실행' 버튼 설정 (없으면 빈 문자열 → 버튼 숨김). 구버전 config 대비 getattr.
WORKER_URL = getattr(config, "WORKER_URL", "")
TRIGGER_SECRET = getattr(config, "TRIGGER_SECRET", "")

# API 키가 없을 때, 로컬에 로그인된 Claude Code CLI로 대신 호출할지 여부.
# 키 발급 전에 AI 선별 품질을 미리 확인해보는 용도입니다.
# GitHub Actions에는 CLI가 없으므로 자동으로 꺼집니다.
USE_CLI = bool(shutil.which("claude")) and not ANTHROPIC_API_KEY

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
DOCS_DIR = os.path.join(ROOT, "docs")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
LATEST_PATH = os.path.join(DATA_DIR, "latest.json")
REJECTED_PATH = os.path.join(DATA_DIR, "rejected.json")
CRITERIA_PATH = os.path.join(DATA_DIR, "criteria.json")

KST = datetime.timezone(datetime.timedelta(hours=9))
OPINION_RE = re.compile(OPINION_TITLE_PATTERN)
LOCAL_PR_RE = re.compile(LOCAL_PR_PATTERN)
NORMALIZE_RE = re.compile(r"[\s\W_]+", re.UNICODE)


# ----------------------------------------------------------------------------
# 텍스트 유틸
# ----------------------------------------------------------------------------
def clean_title(title):
    """중립적인 머리 태그([마켓인] 등)만 제거합니다."""
    return re.sub(TITLE_TAG_PATTERN, "", title or "").strip()


def normalize_title(title):
    """같은 기사가 여러 매체에 실렸을 때를 잡기 위한 비교용 정규화."""
    return NORMALIZE_RE.sub("", title or "").lower()


def yymmdd(date_str):
    """'2026-05-06' -> '260506'. 파싱 실패 시 원본 반환."""
    try:
        return datetime.datetime.strptime(date_str, "%Y-%m-%d").strftime("%y%m%d")
    except Exception:
        return date_str


def parse_pubdate(pubdate_str):
    """
    두 가지 pubDate 포맷을 처리합니다.
    - 'Wed, 22 Jul 2026 07:17:38 GMT' / '+0900' (tz 포함)
    - '2026-07-24 16:48:57' (tz 없음) — 이 naive 포맷을 놓치면 해당 매체 기사가 전량 폐기됨
    tz가 없는 값은 한국 시간(KST)으로 간주합니다.
    """
    s = (pubdate_str or "").strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                tz = datetime.timezone.utc if fmt.endswith("%Z") else KST
                dt = dt.replace(tzinfo=tz)
            return dt
        except Exception:
            continue
    return None


# ----------------------------------------------------------------------------
# Claude 호출
# ----------------------------------------------------------------------------
def ask_claude_api(prompt):
    """Anthropic API 호출. 응답 텍스트를 반환합니다."""
    body = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("x-api-key", ANTHROPIC_API_KEY)
    req.add_header("anthropic-version", "2023-06-01")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    return "\n".join(blocks).strip()


def ask_claude_cli(prompt):
    """로컬 Claude Code CLI로 호출 (구독 인증, API 키 불필요). 로컬 확인용."""
    proc = subprocess.run(
        ["claude", "-p", prompt, "--model", ANTHROPIC_MODEL],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI 실패 (exit {proc.returncode}): {proc.stderr[:200]}")
    return proc.stdout.strip()


def extract_json_array(text):
    """응답에서 첫 번째 JSON 배열만 꺼냅니다(코드블록/후행 설명 대응)."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    start = text.find("[")
    if start == -1:
        raise ValueError(f"응답에 JSON 배열이 없습니다: {text[:120]!r}")
    depth = 0
    in_string = False
    escaped = False
    for i, ch in enumerate(text[start:], start):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError(f"JSON 배열이 닫히지 않았습니다: {text[start:start + 120]!r}")


# ----------------------------------------------------------------------------
# 영구 제외 목록 (재선별 시 탈락한 기사)
# ----------------------------------------------------------------------------
_REJECTED = None  # (links set, norms set) 캐시


def load_rejected():
    global _REJECTED
    if _REJECTED is None:
        entries = load_json(REJECTED_PATH, [])
        links = {e.get("link") for e in entries if e.get("link")}
        norms = {e.get("norm") for e in entries if e.get("norm")}
        _REJECTED = (links, norms)
    return _REJECTED


def add_rejected(articles):
    """기사들을 영구 제외 목록에 추가하고 캐시를 갱신합니다."""
    global _REJECTED
    entries = load_json(REJECTED_PATH, [])
    known = {e.get("link") for e in entries}
    for a in articles:
        if a.get("link") in known:
            continue
        entries.append({
            "link": a.get("link"),
            "title": a.get("title"),
            "norm": normalize_title(a.get("title", "")),
            "rejected_at": datetime.date.today().isoformat(),
        })
    with open(REJECTED_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    _REJECTED = None  # 다음 load_rejected에서 새로 읽도록


# ----------------------------------------------------------------------------
# 학습되는 제외 기준 (사용자가 '재검색' 하며 남긴 사유)
# ----------------------------------------------------------------------------
_CRITERIA = None


def load_criteria():
    """{카테고리명: [제외 기준 문장, ...]} 반환."""
    global _CRITERIA
    if _CRITERIA is None:
        _CRITERIA = load_json(CRITERIA_PATH, {})
    return _CRITERIA


def add_criteria(cat_name, reasons):
    """카테고리에 새 제외 기준을 누적(중복·과길이 제거). 이후 모든 선별 프롬프트에 반영됨."""
    global _CRITERIA
    data = load_json(CRITERIA_PATH, {})
    lst = data.get(cat_name, [])
    seen = set(lst)
    for r in reasons:
        r = (r or "").strip()
        if r and r not in seen and len(r) <= 200:
            lst.append(r)
            seen.add(r)
    data[cat_name] = lst[-30:]  # 카테고리당 최근 30개까지만 유지
    with open(CRITERIA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _CRITERIA = None


# ----------------------------------------------------------------------------
# 후보 수집
# ----------------------------------------------------------------------------
def score_candidate(c, cfg):
    """관련성 점수. 상위 60건만 Claude에 넘어가므로 순위가 결과를 좌우합니다."""
    score = 0
    tokens = [t for t in c["matched_keyword"].split() if t]
    hits = sum(1 for t in tokens if t in c["title"])
    if tokens and hits == len(tokens):
        score += 3
    elif hits:
        score += 1
    if c["press"] in cfg["press"]:
        score += 2
    if c["age_days"] <= PREFERRED_ARTICLE_AGE_DAYS:
        score += 1
    if LOCAL_PR_RE.search(c["title"]):
        score -= 4          # 지자체·공공기관 홍보성 기사는 뒤로
    if c.get("description"):
        score += 1          # 본문 요약이 있는 RSS 기사
    if len(c["title"]) < 12:
        score -= 3          # '고용노동부' 같은 한 단어 제목 = 사진/브리핑 스텁
    return score


def build_query(keyword, cfg):
    """키워드에 기간 연산자와 신뢰 언론사 site: 힌트를 붙여 구글 검색어를 만듭니다."""
    parts = [keyword, SEARCH_WINDOW]
    domains = [PRESS_DOMAINS[p] for p in cfg["press"] if p in PRESS_DOMAINS]
    if domains:
        parts.append("(" + " OR ".join(f"site:{d}" for d in domains) + ")")
    return " ".join(parts)


def keyword_hit(text, keywords):
    """제목/요약에 카테고리 키워드가 들어있으면 그 키워드를 반환(없으면 None)."""
    for kw in keywords:
        if all(tok in text for tok in kw.split()):
            return kw
    return None


def collect_candidates(category_name, cfg):
    now = datetime.datetime.now(KST)
    rej_links, rej_norms = load_rejected()
    seen_links = set()
    seen_titles = set()
    candidates = []
    stats = {"opinion": 0}

    def consider(rec):
        title = clean_title(rec["title"])
        if not title or rec["link"] in seen_links or rec["link"] in rej_links:
            return
        if OPINION_RE.search(rec["title"]):
            stats["opinion"] += 1
            return
        if any(ex in rec["press"] for ex in EXCLUDE_SOURCES):
            return
        norm = normalize_title(title)
        if norm in seen_titles or norm in rej_norms:
            return
        pub = parse_pubdate(rec["pubDate"])
        if pub is None:
            return
        age_days = (now - pub).days
        if age_days > MAX_ARTICLE_AGE_DAYS:
            return
        seen_links.add(rec["link"])
        seen_titles.add(norm)
        candidates.append({
            "title": title,
            "link": rec["link"],
            "pubDate": rec["pubDate"],
            "date": pub.astimezone(KST).strftime("%Y-%m-%d"),
            "age_days": age_days,
            "matched_keyword": rec["matched_keyword"],
            "press": rec["press"],
            "domain": rec["domain"],
            "description": rec.get("description", ""),
        })

    # --- 1차: 신뢰 언론사 RSS (원문 URL + 본문 요약) ---
    for press in cfg["press"]:
        feed = PRESS_RSS.get(press)
        if not feed:
            continue
        for it in pressrss.fetch(feed, press):
            kw = keyword_hit(it["title"] + " " + it["description"], cfg["keywords"])
            if not kw:
                continue
            consider({
                "title": it["title"], "link": it["link"], "pubDate": it["pubDate"],
                "matched_keyword": kw, "press": it["press"],
                "domain": gnews.domain_of(it["link"]), "description": it["description"],
            })

    # --- 2차: 구글 뉴스 (RSS 없는 매체 + 시의성 보완) ---
    for kw in cfg["keywords"]:
        items = gnews.search(build_query(kw, cfg))
        items += gnews.search(kw, window=SEARCH_WINDOW)
        for it in items[:CANDIDATES_PER_KEYWORD * 2]:
            consider({
                "title": it["title"], "link": it["google_link"], "pubDate": it["pubDate"],
                "matched_keyword": kw, "press": it["press"],
                "domain": gnews.domain_of(it["press_url"]), "description": "",
            })

    if stats["opinion"]:
        print(f"  사설/기고/영상성 제목 {stats['opinion']}건 제외")
    candidates.sort(key=lambda c: (-score_candidate(c, cfg), c["age_days"]))
    return candidates


# ----------------------------------------------------------------------------
# 저장 유틸 & 이력
# ----------------------------------------------------------------------------
def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def recent_history_links(history, window_days=DEDUP_WINDOW_DAYS):
    cutoff = datetime.date.today() - datetime.timedelta(days=window_days)
    links, titles = set(), set()
    for entry in history:
        try:
            d = datetime.date.fromisoformat(entry["date"])
        except Exception:
            continue
        if d >= cutoff:
            for cat_articles in entry.get("categories", {}).values():
                for art in cat_articles:
                    links.add(art.get("link"))
                    titles.add(art.get("title"))
    return links, titles


# ----------------------------------------------------------------------------
# Claude 선별
# ----------------------------------------------------------------------------
def call_claude_select(category_name, cfg, candidates, exclude_titles, want_max=SELECT_MAX):
    """Claude에게 카테고리별 최종 선별을 요청."""
    if not candidates:
        return []
    if not (ANTHROPIC_API_KEY or USE_CLI):
        print("[WARN] 인증 수단이 없어 규칙 기반으로만 선별합니다.")
        return rule_based_fallback(candidates, exclude_titles, want_max)

    candidate_lines = []
    for i, c in enumerate(candidates[:60]):
        line = (f"{i+1}. 제목: {c['title']} | 언론사: {c['press']} | "
                f"게재: {c['date']} ({c['age_days']}일 전)")
        if c.get("description"):
            line += f" | 요약: {c['description'][:150]}"
        candidate_lines.append(line)
    candidates_text = "\n".join(candidate_lines)

    crit = load_criteria().get(category_name, [])
    crit_block = ""
    if crit:
        crit_block = ("\n[사용자가 지정한 추가 제외 기준 — 아래에 해당하는 기사는 반드시 제외]\n"
                      + "\n".join(f"- {c}" for c in crit))

    system_prompt = f"""당신은 ESG/지속가능경영 전문 미디어 모니터링 편집자입니다.
아래 '{category_name}' 카테고리 후보 기사 중에서 {SELECT_MIN}~{want_max}개를 최종 선별하세요.

[선별 기준]
- 최근 2~3일 이내 기사를 우선하고, 최대 7일 이내까지만 허용
- 아래 '최근 선정 이력'과 제목이 유사하거나 동일 이슈를 다루는 기사는 제외 (중복 방지)
- 신뢰도 있는 언론사 우선. 특정 언론사가 한 카테고리에 지나치게 반복되면 다른 매체의 같은 주제 기사로 교체
- 제목이 지나치게 자극적/낚시성이면 같은 내용을 다루는 더 담백한 제목의 기사로 대체
- 내용이 너무 지엽적이거나 기업 경영과 무관한 기사는 제외 (예: 환경 카테고리에 순수 해수면 상승 뉴스 등)
- '기업 경영에 미치는 영향' 또는 '기업이 대응해야 할 이슈' 관점의 기사를 우선
- 키워드에 없어도 시의적절한 국제 이니셔티브(COP, IPCC 등) 관련 기업 영향 기사는 포함 가능
- 사설/기고/광고성 기사 주의: 제목은 그럴듯한데 실제로는 신간 홍보, 행사·포럼 홍보, 수상 소식인 경우 제외
- 뉴스 영상 스크립트 형태나 인터뷰 기사는 제외
- 특정 지역에 한정된 지자체 행사·캠페인·업무협약 기사는 지엽적이므로 제외
- 판단 재료가 제목/언론사/요약뿐이므로, 위 기준 위반이 의심되면 넣지 말고 넘어가세요
- 후보가 기준에 부합하지 않으면 {SELECT_MIN}개 미만으로 반환해도 됩니다. 절대 억지로 채우지 마세요.

[카테고리 참고 언론사] {", ".join(cfg["press"])}
[추가 지침] {cfg.get("extra_context", "")}
{crit_block}
[최근 선정 이력 - 아래와 겹치는 주제/제목은 제외]
{chr(10).join(list(exclude_titles)[:80]) if exclude_titles else "(없음)"}

[후보 기사 목록]
{candidates_text}

반드시 아래 JSON 형식만 출력하세요. 다른 설명, 코드블록 마크다운 없이 순수 JSON 배열만 출력합니다:
[{{"index": 후보번호(정수), "reason": "선정 이유 한 줄"}}, ...]
"""

    try:
        raw = ask_claude_cli(system_prompt) if USE_CLI else ask_claude_api(system_prompt)
        selections = extract_json_array(raw)
    except Exception as e:
        print(f"[ERROR] Claude 선별 실패 ({category_name}): {e}")
        return rule_based_fallback(candidates, exclude_titles, want_max)

    results = []
    for sel in selections:
        idx = sel.get("index")
        if not idx or idx < 1 or idx > len(candidates):
            continue
        results.append(_to_article(candidates[idx - 1], sel.get("reason", "")))
    return results[:want_max]


def _to_article(c, reason):
    return {
        "title": c["title"], "link": c["link"], "date": c["date"],
        "press": c["press"], "domain": c["domain"], "reason": reason,
    }


def rule_based_fallback(candidates, exclude_titles, want_max=SELECT_MAX):
    """API 키가 없을 때를 대비한 단순 규칙 기반 선별 (품질은 낮음)."""
    results = []
    used_press = {}
    for c in candidates:
        if c["title"] in exclude_titles:
            continue
        if c["age_days"] > PREFERRED_ARTICLE_AGE_DAYS and len(results) >= SELECT_MIN:
            continue
        if used_press.get(c["press"], 0) >= 2:
            continue
        results.append(_to_article(c, "규칙 기반 자동 선정 (AI 미사용)"))
        used_press[c["press"]] = used_press.get(c["press"], 0) + 1
        if len(results) >= want_max:
            break
    return results


def resolve_links(articles):
    """선정된 기사에 한해 구글 뉴스 링크를 원문 URL로 복원합니다."""
    for a in articles[:MAX_RESOLVE_PER_CATEGORY]:
        if "news.google." in a["link"]:
            a["link"] = gnews.resolve_url(a["link"])
    return articles


def select_for_category(cat_name, cfg, exclude_links, exclude_titles, want):
    """한 카테고리에서 want개를 새로 선별합니다(제외 목록 반영 + 원문 URL 복원)."""
    if want <= 0:
        return []
    candidates = collect_candidates(cat_name, cfg)
    candidates = [c for c in candidates
                  if c["link"] not in exclude_links
                  and normalize_title(c["title"]) not in exclude_titles]
    selected = call_claude_select(cat_name, cfg, candidates, exclude_titles, want_max=want)
    return resolve_links(selected)[:want]


# ----------------------------------------------------------------------------
# 사이트 생성 (기사별 재검색 버튼 + 학습 사유 + 복사 기능)
# ----------------------------------------------------------------------------
SITE_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>미디어 모니터링 · __TODAY__</title>
<style>
  :root {
    --bg:#f5f7f4; --card:#fff; --text:#17201b; --muted:#586258; --faint:#8a938b;
    --accent:#1e5b48; --accent-soft:#e7efe9; --border:#dce3dd; --brass:#8c7333;
    --danger:#a23b2d; --danger-soft:#f6e7e3;
  }
  @media (prefers-color-scheme:dark){
    :root{ --bg:#101512; --card:#171e19; --text:#e7eee8; --muted:#9ba79e; --faint:#6c776e;
      --accent:#74c4a2; --accent-soft:#1a2a22; --border:#25302a; --brass:#cbae77;
      --danger:#e08a7a; --danger-soft:#2c1c19; }
  }
  *{box-sizing:border-box;}
  body{margin:0; padding:36px 18px 100px; background:var(--bg); color:var(--text);
    font-family:"Pretendard","Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif; line-height:1.55;}
  .wrap{max-width:900px; margin:0 auto;}
  h1{font-size:1.6rem; margin:0 0 4px;}
  .updated{color:var(--muted); font-size:.9rem; margin-bottom:14px;}
  .howto{background:var(--accent-soft); color:var(--text); border:1px solid var(--border);
    border-radius:10px; padding:12px 16px; font-size:.86rem; margin-bottom:26px; line-height:1.6;}
  .howto b{color:var(--accent);}
  .category{background:var(--card); border:1px solid var(--border); border-radius:12px;
    padding:18px 20px; margin-bottom:16px;}
  .category h2{font-size:1.05rem; margin:0 0 8px; color:var(--accent);}
  ul{list-style:none; margin:0; padding:0;}
  .article{display:flex; gap:11px; padding:11px 0; border-top:1px solid var(--border); align-items:flex-start;}
  .article:first-of-type{border-top:none;}
  .research{flex:none; width:28px; height:28px; border:1px solid var(--border); background:var(--bg);
    color:var(--muted); border-radius:8px; cursor:pointer; font-size:.95rem; line-height:1;
    display:flex; align-items:center; justify-content:center; transition:all .12s;}
  .research:hover{border-color:var(--danger); color:var(--danger);}
  .article.flagged .research{background:var(--danger); border-color:var(--danger); color:#fff;}
  .body{flex:1; min-width:0;}
  .line a{color:var(--text); text-decoration:none; font-weight:600; line-height:1.45;}
  .line a:hover{color:var(--accent); text-decoration:underline;}
  .cite{color:var(--muted); font-size:.86rem; font-variant-numeric:tabular-nums; margin-left:4px; white-space:nowrap;}
  .article.flagged .line a{text-decoration:line-through; color:var(--faint);}
  .flagbox{margin-top:8px;}
  .flagbox input{width:100%; font-size:.82rem; border:1px solid var(--danger); border-radius:7px;
    padding:7px 10px; background:var(--danger-soft); color:var(--text);}
  .flagbox input::placeholder{color:var(--faint);}
  .empty{color:var(--faint); font-size:.9rem; padding:8px 0;}
  .bar{position:fixed; left:0; right:0; bottom:0; background:var(--card);
    border-top:1px solid var(--border); padding:11px 18px; display:flex; gap:10px;
    align-items:center; justify-content:center; flex-wrap:wrap;}
  .bar .status{font-size:.85rem; color:var(--muted); margin-right:auto;}
  .btn{background:var(--accent); color:#fff; border:none; border-radius:8px;
    padding:10px 16px; font-size:.88rem; font-weight:600; cursor:pointer;}
  .btn.ghost{background:transparent; color:var(--accent); border:1px solid var(--accent);}
  .btn.danger{background:var(--danger);}
  dialog{border:1px solid var(--border); border-radius:12px; padding:20px; max-width:600px;
    width:92vw; background:var(--card); color:var(--text);}
  dialog::backdrop{background:rgba(0,0,0,.45);}
  dialog h3{margin:0 0 8px; font-size:1.05rem;}
  dialog p{font-size:.86rem; color:var(--muted); margin:0 0 12px; line-height:1.6;}
  dialog textarea{width:100%; height:180px; font-family:ui-monospace,Menlo,Consolas,monospace;
    font-size:.78rem; border:1px solid var(--border); border-radius:8px; padding:10px;
    background:var(--bg); color:var(--text); resize:vertical;}
  dialog .row{display:flex; gap:10px; margin-top:12px; justify-content:flex-end;}
  .spinner{width:36px; height:36px; margin:6px auto 2px; border:3px solid var(--border);
    border-top-color:var(--accent); border-radius:50%; animation:spin 0.9s linear infinite;}
  @keyframes spin{to{transform:rotate(360deg);}}
  @media (prefers-reduced-motion:reduce){ .spinner{animation:none;} }
</style>
</head>
<body>
<div class="wrap">
  <h1>📰 미디어 모니터링</h1>
  <div class="updated">마지막 업데이트: __TODAY__ (매일 자동 갱신)</div>
  <div class="howto">
    기사 왼쪽 <b>↻</b> 버튼을 누르면 그 기사를 <b>다른 기사로 교체</b>합니다. 이때 <b>부적절한 이유</b>를
    적어두면, 앞으로도 비슷한 기사를 자동으로 걸러냅니다.
    다 표시했으면 하단 <b>재검색 실행</b>을 누르면 됩니다(약 3~4분 뒤 자동 갱신).
    <b>리스트 복사</b>는 공유용 텍스트입니다.
  </div>
  __CONTENT__
</div>

<div class="bar">
  <span class="status" id="status">재검색 표시 0건</span>
  <button class="btn ghost" id="btnList">리스트 복사</button>
  <button class="btn ghost" id="btnRefine">재검색 목록 복사</button>
  <button class="btn danger" id="btnRun" style="display:none">재검색 실행</button>
</div>

<dialog id="dlg">
  <h3 id="dlgTitle"></h3>
  <p id="dlgHelp"></p>
  <textarea id="payload" readonly></textarea>
  <div class="row">
    <button class="btn ghost" id="close">닫기</button>
    <button class="btn" id="copy">복사</button>
  </div>
</dialog>

<dialog id="prog">
  <h3 id="progTitle">재검색 중…</h3>
  <div class="spinner"></div>
  <p id="progMsg" style="text-align:center">새 기사를 찾고 있어요. 약 3~4분 걸리고, 끝나면 이 페이지가 자동으로 갱신됩니다.</p>
  <div class="row"><button class="btn ghost" id="progClose">닫기</button></div>
</dialog>

<script>
const TODAY = "__TODAY__";
const CONFIG = __CONFIG__;
const arts = () => Array.from(document.querySelectorAll(".article"));
const flagged = () => arts().filter(a => a.classList.contains("flagged"));
function updateStatus(){ document.getElementById("status").textContent = "재검색 표시 " + flagged().length + "건"; }

document.addEventListener("click", e => {
  const btn = e.target.closest(".research");
  if (!btn) return;
  const li = btn.closest(".article");
  if (li.classList.contains("flagged")) {
    li.classList.remove("flagged");
    const fb = li.querySelector(".flagbox"); if (fb) fb.remove();
  } else {
    li.classList.add("flagged");
    const fb = document.createElement("div");
    fb.className = "flagbox";
    const inp = document.createElement("input");
    inp.className = "reason";
    inp.placeholder = "이 기사가 부적절한 이유 (예: 지자체 행사, 특정기업 홍보) — 비슷한 기사도 앞으로 걸러집니다";
    fb.appendChild(inp);
    li.querySelector(".body").appendChild(fb);
    inp.focus();
  }
  updateStatus();
});

function buildRefine(){
  const reject = {};
  flagged().forEach(li => {
    const cat = li.dataset.cat;
    const inp = li.querySelector(".reason");
    (reject[cat] = reject[cat] || []).push({
      link: li.dataset.link, title: li.dataset.title, reason: (inp ? inp.value : "").trim()
    });
  });
  return JSON.stringify({ date: TODAY, reject }, null, 0);
}
function buildList(){
  const out = [];
  document.querySelectorAll(".category").forEach(sec => {
    out.push("[" + sec.querySelector("h2").textContent + "]");
    sec.querySelectorAll(".article").forEach(li => {
      out.push(li.dataset.title + " (" + li.dataset.press + ", " + li.dataset.day + ")");
      out.push(li.dataset.link);
    });
    out.push("");
  });
  return out.join("\\n").trim();
}
const dlg = document.getElementById("dlg");
function openDlg(title, help, body){
  document.getElementById("dlgTitle").textContent = title;
  document.getElementById("dlgHelp").textContent = help;
  document.getElementById("payload").value = body;
  dlg.showModal();
}
document.getElementById("btnRefine").addEventListener("click", () => {
  if (flagged().length === 0) { alert("먼저 교체할 기사의 ↻ 버튼을 누르고, 가능하면 이유를 적어주세요."); return; }
  openDlg("재검색 목록", "GitHub → Actions → Daily Media Monitoring → Run workflow 의 refine 입력란에 붙여넣고 실행하세요.", buildRefine());
});
document.getElementById("btnList").addEventListener("click", () => {
  openDlg("리스트 (공유용)", "복사해서 메일·메신저에 붙여넣으세요.", buildList());
});
document.getElementById("close").addEventListener("click", () => dlg.close());
document.getElementById("copy").addEventListener("click", async () => {
  const ta = document.getElementById("payload"); ta.select();
  try { await navigator.clipboard.writeText(ta.value); } catch { document.execCommand("copy"); }
  const b = document.getElementById("copy"); b.textContent = "복사됨 ✓";
  setTimeout(() => b.textContent = "복사", 1500);
});

// ---- '재검색 실행' 자동 트리거 (Worker 설정 시에만 표시) ----
const prog = document.getElementById("prog");
let pollTimer = null;
function pollUntilDone(){
  const start = Date.now();
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    if (Date.now() - start > 6 * 60 * 1000) {   // 6분 타임아웃
      clearInterval(pollTimer);
      document.getElementById("progMsg").textContent = "시간이 조금 더 걸리네요. 잠시 후 새로고침(F5) 해보세요.";
      return;
    }
    try {
      const r = await fetch("status.json?t=" + Date.now(), { cache: "no-store" });
      const j = await r.json();
      if (j.generated_at && j.generated_at > CONFIG.genAt) {
        clearInterval(pollTimer);
        location.reload();
      }
    } catch (e) { /* Pages 반영 지연 중 — 계속 폴링 */ }
  }, 10000);
}
async function triggerRun(){
  if (flagged().length === 0) { alert("먼저 교체할 기사의 ↻ 버튼을 누르고, 가능하면 이유를 적어주세요."); return; }
  const payload = buildRefine();
  document.getElementById("progTitle").textContent = "재검색 중…";
  document.getElementById("progMsg").textContent = "새 기사를 찾고 있어요. 약 3~4분 걸리고, 끝나면 이 페이지가 자동으로 갱신됩니다.";
  prog.showModal();
  try {
    const res = await fetch(CONFIG.workerUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Trigger-Secret": CONFIG.secret || "" },
      body: payload,
    });
    if (!res.ok) throw new Error("서버 응답 " + res.status);
  } catch (e) {
    prog.close();
    alert("자동 실행 요청에 실패했어요 (" + e.message + ").\\n대신 '재검색 목록 복사'로 수동 실행하세요.");
    return;
  }
  pollUntilDone();
}
document.getElementById("btnRun").addEventListener("click", triggerRun);
document.getElementById("progClose").addEventListener("click", () => prog.close());
if (CONFIG.workerUrl) {   // Worker가 설정돼 있으면 자동 버튼을 앞세우고, 복사 버튼은 보조로
  document.getElementById("btnRun").style.display = "";
  document.getElementById("btnRefine").classList.remove("danger");
}
updateStatus();
</script>
</body>
</html>"""


def build_site(today_str, categories_result, generated_at=0):
    os.makedirs(DOCS_DIR, exist_ok=True)
    # Pages는 docs/ 만 서빙하므로, 자동 새로고침 감지용 상태 파일을 docs/ 안에 둔다.
    with open(os.path.join(DOCS_DIR, "status.json"), "w", encoding="utf-8") as f:
        json.dump({"generated_at": generated_at}, f)
    cat_blocks = []
    for cat_name, articles in categories_result.items():
        cat_esc = html.escape(cat_name)
        cat_attr = html.escape(cat_name, quote=True)
        if not articles:
            items_html = '<li class="empty">오늘은 선정된 기사가 없습니다.</li>'
        else:
            items_html = ""
            for a in articles:
                link = html.escape(a["link"], quote=True)
                title_txt = html.escape(a["title"])
                title_attr = html.escape(a["title"], quote=True)
                press = html.escape(a.get("press") or a.get("domain") or "")
                press_attr = html.escape(a.get("press") or a.get("domain") or "", quote=True)
                yy = html.escape(yymmdd(a.get("date", "")))
                # 형식: 제목(하이퍼링크) (신문사, 260506)
                items_html += (
                    f'<li class="article" data-cat="{cat_attr}" data-link="{link}" '
                    f'data-title="{title_attr}" data-press="{press_attr}" data-day="{yy}">'
                    f'<button class="research" type="button" title="이 기사 교체(재검색)">↻</button>'
                    f'<div class="body"><div class="line">'
                    f'<a href="{link}" target="_blank" rel="noopener noreferrer">{title_txt}</a>'
                    f'<span class="cite">({press}, {yy})</span>'
                    f'</div></div></li>'
                )
        cat_blocks.append(
            f'<section class="category"><h2>{cat_esc}</h2><ul>{items_html}</ul></section>'
        )

    cfg_js = json.dumps({
        "workerUrl": WORKER_URL, "secret": TRIGGER_SECRET, "genAt": generated_at,
    })
    html_out = (SITE_TEMPLATE
                .replace("__TODAY__", html.escape(today_str))
                .replace("__CONFIG__", cfg_js)
                .replace("__CONTENT__", "".join(cat_blocks)))
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html_out)


# ----------------------------------------------------------------------------
# 실행 모드
# ----------------------------------------------------------------------------
def print_mode():
    if ANTHROPIC_API_KEY:
        print(f"[선별 방식] Claude API ({ANTHROPIC_MODEL})")
    elif USE_CLI:
        print(f"[선별 방식] 로컬 Claude Code CLI ({ANTHROPIC_MODEL}) — 로컬 확인용")
    else:
        print("[선별 방식] 규칙 기반 (AI 미사용) — 품질이 많이 떨어집니다.")


def save_result(today, categories_result):
    history = load_json(HISTORY_PATH, [])
    history = [h for h in history if h.get("date") != today]
    history.append({"date": today, "categories": categories_result})
    cutoff = datetime.date.today() - datetime.timedelta(days=60)
    history = [h for h in history if datetime.date.fromisoformat(h["date"]) >= cutoff]
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    generated_at = int(time.time())  # 자동 새로고침 감지용 (실행할 때마다 증가)
    with open(LATEST_PATH, "w", encoding="utf-8") as f:
        json.dump({"date": today, "generated_at": generated_at,
                   "categories": categories_result}, f, ensure_ascii=False, indent=2)
    build_site(today, categories_result, generated_at)


def main():
    print_mode()
    os.makedirs(DATA_DIR, exist_ok=True)
    today = datetime.date.today().isoformat()
    history = load_json(HISTORY_PATH, [])
    exclude_links, exclude_titles = recent_history_links(history)

    categories_result = {}
    for cat_name, cfg in CATEGORIES.items():
        print(f"=== {cat_name} 수집 중 ===")
        selected = select_for_category(cat_name, cfg, exclude_links, exclude_titles, SELECT_MAX)
        print(f"  선정 {len(selected)}건")
        categories_result[cat_name] = selected
        time.sleep(1)

    save_result(today, categories_result)
    print("완료.")


def refine(payload_str):
    """
    재선별 모드: 표시하지 않은 기사는 유지, '재검색' 표시된 기사는 교체합니다.
    입력 형식 (사이트 '재검색 목록 복사'):
      {"date":..., "reject": {"카테고리": [{"link","title","reason"}, ...]}}
    교체된 기사는 영구 제외, 사유는 카테고리별 제외 기준으로 학습됩니다.
    (구버전 {"keep": {...}} 형식도 하위호환 처리)
    """
    print_mode()
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        payload = json.loads(payload_str)
    except Exception as e:
        print(f"[ERROR] 재선별 입력(JSON) 파싱 실패: {e}")
        return
    reject_map = payload.get("reject")
    keep_map = payload.get("keep")

    latest = load_json(LATEST_PATH, {"date": "", "categories": {}})
    today = datetime.date.today().isoformat()
    history = load_json(HISTORY_PATH, [])
    hist_links, hist_titles = recent_history_links(history)

    categories_result = {}
    for cat_name, cfg in CATEGORIES.items():
        current = latest.get("categories", {}).get(cat_name, [])

        if reject_map is not None:
            entries = reject_map.get(cat_name, [])
            reject_links = {e.get("link") for e in entries}
            reasons = [e.get("reason") for e in entries if (e.get("reason") or "").strip()]
        else:  # 구버전 keep 형식
            keep_links = set((keep_map or {}).get(cat_name, []))
            reject_links = {a.get("link") for a in current if a.get("link") not in keep_links}
            reasons = []

        kept = [a for a in current if a.get("link") not in reject_links]
        rejected = [a for a in current if a.get("link") in reject_links]

        if not rejected:
            categories_result[cat_name] = kept
            print(f"=== {cat_name}: 유지 {len(kept)}건 (교체 없음)")
            continue

        add_rejected(rejected)          # 이 기사들은 다시 안 나오게 (영구 제외)
        if reasons:
            add_criteria(cat_name, reasons)  # 사유를 향후 선별 기준으로 학습
            print(f"=== {cat_name}: 유지 {len(kept)}, 교체 {len(rejected)}건, 학습된 기준 {len(reasons)}개")
        else:
            print(f"=== {cat_name}: 유지 {len(kept)}, 교체 {len(rejected)}건 (사유 없음)")

        need = len(current) - len(kept)
        exclude_links = set(hist_links) | {a.get("link") for a in kept}
        exclude_titles = set(hist_titles) | {a.get("title") for a in kept}
        fresh = select_for_category(cat_name, cfg, exclude_links, exclude_titles, need)
        print(f"    새로 {len(fresh)}건 확보")
        categories_result[cat_name] = kept + fresh
        time.sleep(1)

    save_result(today, categories_result)
    print("재선별 완료.")


if __name__ == "__main__":
    _payload = os.environ.get("REFINE_PAYLOAD", "").strip()
    if _payload:
        refine(_payload)
    else:
        main()
