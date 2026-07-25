# -*- coding: utf-8 -*-
"""
미디어 모니터링 카테고리 설정
- press: 우선 신뢰 언론사 리스트 (참고용 가중치, 절대 필터는 아님)
- keywords: 검색 키워드 리스트
- extra_context: AI 선별 시 참고할 추가 지침 (시의성 있는 키워드 예시 등)
필요할 때마다 이 파일만 수정하면 검색 대상/키워드를 바꿀 수 있습니다.

뉴스 수집은 신뢰 언론사 RSS(1차) + Google News RSS(2차)를 씁니다. API 키가 필요 없습니다.
"""

CATEGORIES = {
    "ESG 공통": {
        "press": ["ESG경제", "임팩트온"],
        "keywords": [
            "ESG 공시", "ESG 평가", "ESG 내재화", "ESG 투자",
            "ESG 규제", "ESG 보고서", "SBTi", "지속가능성 보고서",
        ],
        "extra_context": "COP, IPCC 등 국제 이니셔티브 관련 기업 영향 기사도 포함 가능",
    },
    "환경(Environment)": {
        "press": ["임팩트온", "비즈니스포스트", "에너지경제신문", "그린포스트코리아"],
        "keywords": [
            "탄소중립", "재생에너지", "탄소배출권", "배출권거래제",
            "전기차", "태양광", "CCUS", "기후에너지환경부",
        ],
        "extra_context": "기업 경영/사업에 미치는 영향 위주. 순수 기후과학·해수면 상승 등 기업 무관 뉴스는 제외",
    },
    "노동/인권(Labor·Human Rights)": {
        "press": ["조선비즈", "머니투데이", "매일노동뉴스"],
        "keywords": [
            "중대재해처벌법", "인권위원회", "최저임금", "기업 인권",
            "인권 실사", "EU 실사", "고용차별", "ILO", "OECD 노동",
            "고용노동부", "공급망 실사", "UNGP", "CSDDD", "GRI",
        ],
        "extra_context": "",
    },
    "반부패/거버넌스(Anti-Corruption)": {
        "press": ["헤럴드경제", "파이낸셜뉴스", "ESG경제"],
        "keywords": [
            "기업 지배구조", "지배구조 개선", "기업 밸류업", "기업 부패",
            "거버넌스", "기업 반부패", "뇌물방지", "윤리경영",
            "공정거래위원회 제재", "기업윤리", "내부통제", "주주총회 의결권",
            # '청렴'/'권익위'만 쓰면 지자체·교육청 청렴 캠페인 기사가 대부분이라
            # 기업 맥락을 붙였습니다.
            "기업 청렴도", "공공기관 청렴도 평가",
        ],
        "extra_context": "지자체·교육청의 청렴 교육/캠페인/서약식이 아니라, 기업 지배구조와 준법·윤리경영 이슈를 선정",
    },
    "성평등/다양성(Gender·Diversity)": {
        "press": ["여성신문", "뉴시스", "여성경제신문"],
        "keywords": [
            "성평등", "젠더격차", "유리천장", "임금격차", "여성임원",
            "여성 관리직", "성인지 감수성", "경력단절여성", "육아휴직",
        ],
        "extra_context": "",
    },
    "지속가능금융(Sustainable Finance)": {
        "press": ["ESG경제", "임팩트온", "연합뉴스", "파이낸셜뉴스"],
        "keywords": [
            "지속가능금융", "녹색금융", "녹색채권", "전환금융",
            "ESG금융", "탄소금융", "기후금융", "지속가능채권",
            # 'SLL'만 쓰면 드라마 제작사 스튜디오룰루랄라(SLL) 기사가 쏟아져서
            # 한글 정식 명칭으로 대체했습니다.
            "지속가능연계대출", "지속가능연계채권",
        ],
        "extra_context": "",
    },
    "정의로운 전환(Just Transition)": {
        "press": ["뉴시스", "연합뉴스", "비즈니스포스트"],
        "keywords": [
            "정의로운 전환", "노동 전환", "산업 전환", "노동시장 전환",
            "녹색전환", "직무 전환", "석탄화력 폐지 고용",
            # 'AI 전환'/'재교육'만 쓰면 일반 AI·교육 뉴스가 대부분이라
            # 고용·일자리 맥락을 붙였습니다.
            "AI 전환 일자리", "자동화 일자리 대체", "직무 재교육",
        ],
        "extra_context": "AI·자동화로 인한 고용 구조 변화와 기업의 인력 전환 대응 관점을 우선",
    },
}

# 언론사명 -> 도메인. 구글 검색 시 site: 힌트를 만드는 데 씁니다.
# (구글의 site: 는 절대 필터가 아니라 가중치라, 지정 밖 매체도 일부 섞여 들어옵니다)
PRESS_DOMAINS = {
    "ESG경제": "esgeconomy.com",
    "임팩트온": "impacton.net",
    "비즈니스포스트": "businesspost.co.kr",
    "에너지경제신문": "ekn.kr",
    "그린포스트코리아": "greenpostkorea.co.kr",
    "조선비즈": "chosun.com",
    "머니투데이": "mt.co.kr",
    "매일노동뉴스": "labortoday.co.kr",
    "헤럴드경제": "heraldcorp.com",
    "파이낸셜뉴스": "fnnews.com",
    "여성신문": "womennews.co.kr",
    "뉴시스": "newsis.com",
    "여성경제신문": "womaneconomy.co.kr",
    "연합뉴스": "yna.co.kr",
}

# 언론사명 -> RSS '전체기사' 피드 URL. 1차 소스입니다.
# 원문 URL을 바로 주고 본문 요약이 있어 선별 품질이 높습니다.
# 여기 없는 매체(조선비즈·머니투데이·헤럴드경제·파이낸셜뉴스·뉴시스)는 구글 뉴스로만 수집됩니다.
# 섹션(S1N*) 피드는 다른 매체 기사가 섞여 나오는 사례가 있어 '전체기사' 피드만 씁니다.
PRESS_RSS = {
    "ESG경제": "https://www.esgeconomy.com/rss/allArticle.xml",
    "임팩트온": "https://www.impacton.net/rss/gns_allArticle.xml",
    "비즈니스포스트": "https://www.businesspost.co.kr/rss/Article.xml",
    "에너지경제신문": "https://m.ekn.kr/rss/economy.xml",
    "그린포스트코리아": "https://www.greenpostkorea.co.kr/rss/gn_rss_allArticle.xml",
    "매일노동뉴스": "https://www.labortoday.co.kr/rss/allArticle.xml",
    "여성신문": "https://www.womennews.co.kr/rss/gns_allArticle.xml",
    "여성경제신문": "https://www.womaneconomy.co.kr/rss/allArticle.xml",
    "연합뉴스": "https://www.yna.co.kr/rss/news.xml",
}

# 구글 뉴스 검색에 붙일 기간 연산자. MAX_ARTICLE_AGE_DAYS와 맞춰 두세요.
SEARCH_WINDOW = "when:7d"

# 제목 앞의 '중립적인' 태그 (예: [마켓인], [단독], [종합]) — 가독성 위해 제거만 합니다.
# 한국 언론은 [] 말고 【】〔〕도 쓰기 때문에 함께 처리합니다.
TITLE_TAG_PATTERN = r"^\s*[\[\【\〔][^\]\】\〕]{1,20}[\]\】\〕]\s*"

# 사설·기고·칼럼·인터뷰성 기사를 나타내는 제목 패턴. 제거가 아니라 '후보에서 제외'합니다.
OPINION_TITLE_PATTERN = (
    r"[\[\【\〔]\s*(사설|기고|특별기고|칼럼|시론|논단|오피니언|기자수첩|데스크칼럼|"
    r"편집국에서|기자의\s*눈|현장에서|인터뷰|대담|좌담|르포|포토|영상|카드뉴스|"
    r"신간|book|리뷰)\s*[\]\】\〕]"
    r"|^\s*[\[\【\〔][^\]\】\〕]{2,20}의\s[^\]\】\〕]{1,20}[\]\】\〕]"
    r"|^\s*(사설|칼럼|기고)\s*[:：]"
)

# 지자체·공공기관 홍보성 기사 패턴. 제외하지 않고 '점수를 깎기만' 합니다.
LOCAL_PR_PATTERN = (
    r"(시청|군청|구청|도청|교육청|교육지원청|지원청|시의회|군의회|주민센터"
    r"|[가-힣]{2,4}(시장|군수|구청장|교육감)"
    r"|발대식|위촉식|서약식|선포식|결의대회|캠페인|챌린지|간담회|워크숍|공모전"
    r"|업무협약|MOU|현판식|기념식|시무식|표창|위문|봉사활동)"
)

# 제외 언론사/발행 주체 (예: UN SDGs협회 제휴 기사)
EXCLUDE_SOURCES = ["UN SDGs협회"]

# 카테고리별 후보 풀 상한 (구글 뉴스는 쿼리당 100건까지 돌려줍니다)
CANDIDATES_PER_KEYWORD = 30

# 최종 선정 기사에 대해서만 원문 URL을 복원합니다. 그 상한 (구글 부하/실행시간 제어)
MAX_RESOLVE_PER_CATEGORY = 10

# 최종 선별 기사 개수 (카테고리당)
SELECT_MIN = 3
SELECT_MAX = 4

# 최신성 기준 (일 단위) - 이 기간을 벗어난 기사는 후보에서 제외
MAX_ARTICLE_AGE_DAYS = 7
PREFERRED_ARTICLE_AGE_DAYS = 3

# 최근 며칠간 선별 이력을 "중복"으로 간주할지
DEDUP_WINDOW_DAYS = 14
