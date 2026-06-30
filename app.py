"""Responsive Streamlit dashboard for Korean and Japanese search trends."""

from __future__ import annotations

import html
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import pandas as pd
import plotly.express as px
import streamlit as st

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

from collectors.google_trending_now import GoogleTrendingNowCollector
from collectors.naver_datalab import NaverDataLabCollector
from collectors.translator import translate_many
from database import (
    ensure_mock_data,
    get_last_run,
    get_naver_history,
    get_regional_interest,
    get_snapshot_history,
    get_translations,
    get_trends,
    has_data,
    init_db,
    log_collection_run,
    save_google_trends,
    save_naver_age_trends,
    save_translations,
)
from scoring import add_trend_scores, content_ideas, rank_age_keywords


KST = timezone(timedelta(hours=9))
DB_PATH = Path(os.getenv("TREND_DB_PATH", Path(__file__).with_name("trends.db")))
MARKETS = {"한국": ["KR"], "일본": ["JP"], "한일 비교": ["KR", "JP"]}
COUNTRY_LABEL = {"KR": "한국", "JP": "일본"}
jp_translations: dict[str, str] = {}
translation_mode = "일본어 원문"


st.set_page_config(
    page_title="한일 트렌드 레이더",
    page_icon="↗",
    layout="wide",
    initial_sidebar_state="auto",
)


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;600;700&family=Noto+Sans+KR:wght@400;500;700&family=Noto+Sans+JP:wght@400;500;700&display=swap');
        :root { --ink:#10231d; --muted:#61736c; --mint:#d9f56f; --paper:#f6f7f2; --line:#dce3da; }
        .stApp { background:var(--paper); color:var(--ink); }
        html, body, [class*="css"] { font-family:'DM Sans','Noto Sans KR','Noto Sans JP',sans-serif; }
        [data-testid="stSidebar"] { background:#11251e; }
        [data-testid="stSidebar"] * { color:#eef5ef; }
        [data-testid="stSidebar"] .stButton button { border:1px solid #587066; background:#d9f56f; }
        [data-testid="stSidebar"] .stButton button p { color:#10231d!important; font-weight:700; }
        .hero { padding:1.1rem 0 1.3rem; border-bottom:1px solid var(--line); margin-bottom:1.2rem; }
        .eyebrow { color:#446358; font-size:.78rem; font-weight:700; letter-spacing:.12em; text-transform:uppercase; }
        .hero h1 { color:var(--ink); font-size:2.65rem; line-height:1.08; margin:.35rem 0 .55rem; letter-spacing:-.045em; }
        .hero p { color:var(--muted); max-width:840px; font-size:1rem; margin:0; }
        .status-pill { display:inline-block; background:#e8eddf; color:#355047; border-radius:999px; padding:.25rem .65rem; font-size:.76rem; margin:.8rem .35rem 0 0; }
        .metric-card { min-height:118px; background:#fff; border:1px solid var(--line); border-radius:18px; padding:1rem 1.1rem; box-shadow:0 8px 24px rgba(25,48,39,.04); }
        .metric-label { color:var(--muted); font-size:.78rem; }
        .metric-value { font-size:1.8rem; font-weight:700; letter-spacing:-.04em; margin:.25rem 0; }
        .metric-note { color:#75847e; font-size:.75rem; }
        .section-kicker { color:#61736c; font-size:.76rem; font-weight:700; letter-spacing:.08em; text-transform:uppercase; margin-top:1.3rem; }
        .idea-card { background:#fff; border:1px solid var(--line); border-radius:16px; padding:1rem; height:145px; }
        .idea-tag { display:inline-block; border-radius:999px; padding:.18rem .55rem; background:#edf4d0; color:#344a23; font-size:.7rem; }
        .idea-card h4 { margin:.65rem 0 .35rem; line-height:1.45; font-size:.98rem; }
        .idea-card small, .fine-print { color:#708078; font-size:.75rem; line-height:1.55; }
        .notice-box { background:#fff; border:1px solid var(--line); border-left:4px solid #9ab52f; border-radius:12px; padding:.8rem 1rem; color:#52655d; font-size:.85rem; }
        [data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:14px; overflow:auto; }
        div[data-testid="stExpander"] { background:#fff; border-color:var(--line); }

        @media (max-width: 768px) {
          .block-container { padding:1rem .8rem 4rem!important; }
          .hero { padding:.45rem 0 1rem; }
          .hero h1 { font-size:1.9rem; line-height:1.15; }
          .hero p { font-size:.9rem; }
          [data-testid="stHorizontalBlock"] { flex-wrap:wrap!important; gap:.7rem!important; }
          [data-testid="column"] { flex:1 1 100%!important; width:100%!important; min-width:100%!important; }
          .metric-card { min-height:94px; padding:.8rem 1rem; }
          .metric-value { font-size:1.5rem; }
          .idea-card { height:auto; min-height:118px; }
          h3 { font-size:1.28rem!important; }
          [data-testid="stDataFrame"] { max-width:calc(100vw - 1.6rem); }
          .js-plotly-plot { max-width:100%!important; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def secret(name: str) -> str:
    if os.getenv(name):
        return os.environ[name]
    try:
        return str(st.secrets.get(name, ""))
    except Exception:
        return ""


def naver_collector() -> NaverDataLabCollector:
    return NaverDataLabCollector(secret("NAVER_CLIENT_ID"), secret("NAVER_CLIENT_SECRET"))


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def kst_text(value: str | None, include_date: bool = False) -> str:
    if not value:
        return "—"
    fmt = "%m.%d %H:%M" if include_date else "%H:%M"
    return parse_time(value).astimezone(KST).strftime(fmt)


def relative_started(value: str) -> str:
    minutes = max(0, int((datetime.now(timezone.utc) - parse_time(value)).total_seconds() / 60))
    if minutes < 60:
        return f"{minutes}분 전"
    if minutes < 1440:
        return f"{minutes // 60}시간 전"
    return f"{minutes // 1440}일 전"


def collect_live_data(geos: list[str]) -> tuple[bool, str]:
    messages, google_rows_by_geo = [], {}
    for geo in geos:
        started = datetime.now(timezone.utc)
        try:
            rows = GoogleTrendingNowCollector(geo).fetch()
            save_google_trends(DB_PATH, rows, collected_at=started, is_mock=False)
            log_collection_run(DB_PATH, "google", "success", len(rows), started_at=started, geo=geo)
            google_rows_by_geo[geo] = rows
            messages.append(f"{COUNTRY_LABEL[geo]} Google {len(rows)}개")
        except Exception as exc:
            log_collection_run(DB_PATH, "google", "failed", 0, str(exc), started_at=started, geo=geo)
            messages.append(f"{COUNTRY_LABEL[geo]} 실패")

    naver = naver_collector()
    if "KR" in geos and "KR" in google_rows_by_geo:
        if not naver.configured:
            messages.append("네이버 키 미설정")
        else:
            last_naver = get_last_run(DB_PATH, "naver", "KR")
            due = True
            if last_naver and last_naver["status"] == "success":
                due = datetime.now(timezone.utc) - parse_time(last_naver["finished_at"]) >= timedelta(hours=20)
            if due:
                ranked = sorted(google_rows_by_geo["KR"], key=lambda row: row.get("volume_min", 0), reverse=True)
                keywords = [row["keyword"] for row in ranked[:10]]
                naver_started = datetime.now(timezone.utc)
                try:
                    points = naver.fetch_last_7_days(keywords)
                    save_naver_age_trends(DB_PATH, points, naver_started, is_mock=False)
                    log_collection_run(DB_PATH, "naver", "success", len(points), started_at=naver_started, geo="KR")
                    messages.append(f"네이버 {len(points)}포인트")
                except Exception as exc:
                    log_collection_run(DB_PATH, "naver", "failed", 0, str(exc), started_at=naver_started, geo="KR")
                    messages.append("네이버 실패")
            else:
                messages.append("네이버 일간 캐시")
    return bool(google_rows_by_geo), " · ".join(messages)


def collection_due(geo: str, minutes: int = 15) -> bool:
    last = get_last_run(DB_PATH, "google", geo)
    if not last or last["status"] != "success":
        return True
    return datetime.now(timezone.utc) - parse_time(last["finished_at"]) >= timedelta(minutes=minutes)


def metric_card(label: str, value: str, note: str) -> None:
    st.markdown(
        f'<div class="metric-card"><div class="metric-label">{html.escape(label)}</div>'
        f'<div class="metric-value">{html.escape(value)}</div>'
        f'<div class="metric-note">{html.escape(note)}</div></div>',
        unsafe_allow_html=True,
    )


def table_rows(trends: list[dict[str, Any]], show_country: bool = False) -> pd.DataFrame:
    rows = []
    for row in trends:
        item = {
            "키워드": keyword_display(row), "검색량 구간": row["volume_label"],
            "상승률": row.get("growth_rate"), "시작": relative_started(row["started_at"]),
            "상태": "활성" if row["is_active"] else "종료", "신호 점수": row["trend_score"],
        }
        if show_country:
            item = {"국가": COUNTRY_LABEL[row["geo"]], **item}
        rows.append(item)
    return pd.DataFrame(rows)


def keyword_display(row: dict[str, Any]) -> str:
    keyword = row["keyword"]
    if row.get("geo") != "JP" or translation_mode == "일본어 원문":
        return keyword
    translated = jp_translations.get(keyword)
    if not translated:
        return keyword
    if translation_mode == "한국어 번역":
        return translated
    return f"{keyword} · {translated}"


def show_trend_table(rows: list[dict[str, Any]], show_country: bool = False) -> None:
    st.dataframe(
        table_rows(rows, show_country), hide_index=True, width="stretch",
        column_config={
            "상승률": st.column_config.NumberColumn("상승률", format="%+.0f%%"),
            "신호 점수": st.column_config.ProgressColumn("신호 점수", min_value=0, max_value=100, format="%.1f"),
        },
    )


init_db(DB_PATH)
ensure_mock_data(DB_PATH)
inject_styles()

if st.session_state.pop("_switch_to_live", False):
    st.session_state.data_mode = "실데이터"
if "market" not in st.session_state:
    st.session_state.market = "한국"

with st.sidebar:
    st.markdown("### ↗ TREND RADAR")
    st.caption("KOREA · JAPAN · QUASI REAL-TIME")
    market = st.radio("시장 선택", list(MARKETS), key="market")
    selected_geos = MARKETS[market]
    if "JP" in selected_geos:
        translation_mode = st.selectbox(
            "일본어 표시",
            ["원문 + 한국어", "한국어 번역", "일본어 원문"],
            key="translation_mode",
            help="실시간 새 키워드는 MyMemory 자동 번역을 사용합니다.",
        )
    if "data_mode" not in st.session_state:
        st.session_state.data_mode = "실데이터" if all(has_data(DB_PATH, False, geo) for geo in selected_geos) else "샘플 데이터"
    mode = st.radio("데이터 모드", ["샘플 데이터", "실데이터"], key="data_mode")
    auto_refresh = st.toggle("15분 자동 갱신", value=False, disabled=mode == "샘플 데이터")
    if auto_refresh and st_autorefresh:
        st_autorefresh(interval=15 * 60 * 1000, key="trend_auto_refresh")

    if st.button("선택 국가 실데이터 수집", width="stretch"):
        with st.spinner("Google Trending Now를 확인하는 중…"):
            ok, message = collect_live_data(selected_geos)
        if ok:
            st.session_state._switch_to_live = True
            st.success(message)
            st.rerun()
        else:
            st.error(message)

    if mode == "실데이터" and auto_refresh and any(collection_due(geo) for geo in selected_geos):
        ok, message = collect_live_data(selected_geos)
        st.toast(message) if ok else st.warning(message)

    st.divider()
    for geo in selected_geos:
        last_google = get_last_run(DB_PATH, "google", geo)
        if last_google:
            icon = "●" if last_google["status"] == "success" else "▲"
            st.caption(f"{icon} {COUNTRY_LABEL[geo]} Google · {kst_text(last_google['finished_at'], True)} KST")
        else:
            st.caption(f"○ {COUNTRY_LABEL[geo]} 실데이터 수집 전")

    naver = naver_collector()
    if "KR" in selected_geos:
        naver_run = get_last_run(DB_PATH, "naver", "KR")
        if not naver.configured:
            st.warning("네이버 연령 데이터: API 키 미설정")
        elif naver_run and naver_run["status"] == "failed":
            st.error("네이버 연령 데이터: 최근 수집 실패")
        else:
            st.caption("네이버 연령 데이터: " + ("연결됨" if naver.configured else "미설정"))
    st.markdown(
        '<p class="fine-print">Google에는 공식 연령 필터가 없습니다. 한국 연령대는 네이버 데이터랩의 일간 상대 검색 비율로만 추정합니다.</p>',
        unsafe_allow_html=True,
    )


data_by_geo: dict[str, list[dict[str, Any]]] = {}
source_mode: dict[str, str] = {}
for geo in selected_geos:
    use_mock = mode == "샘플 데이터" or not has_data(DB_PATH, False, geo)
    if mode == "실데이터" and use_mock:
        st.warning(f"{COUNTRY_LABEL[geo]} 실데이터가 없어 샘플 데이터로 대체했습니다. 사이드바에서 수집하세요.")
    source_mode[geo] = "샘플" if use_mock else "실데이터"
    scored = add_trend_scores(get_trends(DB_PATH, use_mock, geo=geo))
    data_by_geo[geo] = [{**row, "country": COUNTRY_LABEL[geo]} for row in scored]

if "JP" in selected_geos and translation_mode != "일본어 원문":
    japanese_keywords = [row["keyword"] for row in data_by_geo["JP"]]
    jp_translations = get_translations(DB_PATH, japanese_keywords)
    missing = [keyword for keyword in japanese_keywords if keyword not in jp_translations]
    if missing:
        with st.spinner("일본어 키워드를 한국어로 번역하는 중…"):
            fetched = translate_many(missing)
        save_translations(DB_PATH, fetched)
        jp_translations.update(fetched)

all_trends = sorted(
    [row for rows in data_by_geo.values() for row in rows],
    key=lambda row: row["trend_score"], reverse=True,
)
recent_4h = [row for row in all_trends if parse_time(row["started_at"]) >= datetime.now(timezone.utc) - timedelta(hours=4)]
recent_24h = [row for row in all_trends if parse_time(row["started_at"]) >= datetime.now(timezone.utc) - timedelta(hours=24)]
active = [row for row in all_trends if row["is_active"]]
ended = [row for row in all_trends if not row["is_active"]]
is_compare = len(selected_geos) == 2

hero_title = {
    "한국": "지금, 한국의 검색 관심은<br>어디로 움직이나",
    "일본": "今、日本の検索関心は<br>どこへ動いているか",
    "한일 비교": "한국과 일본, 지금 무엇이<br>동시에 떠오르는가",
}[market]
badges = "".join(
    f'<span class="status-pill">{COUNTRY_LABEL[geo]} · {source_mode[geo]}</span>' for geo in selected_geos
)
st.markdown(
    f"""
    <div class="hero">
      <div class="eyebrow">ASIA SIGNAL DESK · {market}</div>
      <h1>{hero_title}</h1>
      <p>Google 급상승 신호를 국가별로 포착하고, 한국은 네이버 연령별 일간 추이를 보조지표로 결합합니다.</p>
      {badges}
    </div>
    """,
    unsafe_allow_html=True,
)
if "JP" in selected_geos and translation_mode != "일본어 원문":
    translated_count = len(jp_translations)
    total_japanese = len(data_by_geo["JP"])
    st.caption(
        f"일본어 키워드 번역: MyMemory 자동 번역 · {translated_count}/{total_japanese}개 번역됨 · "
        "번역 실패 항목은 일본어 원문으로 표시"
    )

metrics = st.columns(4)
with metrics[0]: metric_card("활성 트렌드", f"{len(active)}개", "현재 관심이 높은 신호")
with metrics[1]: metric_card("최근 4시간", f"{len(recent_4h)}개", "즉시 대응할 단기 기회")
with metrics[2]: metric_card("최근 24시간", f"{len(recent_24h)}개", "오늘 시작된 급상승")
with metrics[3]: metric_card("최고 신호 점수", f"{all_trends[0]['trend_score']:.0f}/100" if all_trends else "—", "검색량·상승·최신성")

st.markdown('<div class="section-kicker">NOW TRENDING</div>', unsafe_allow_html=True)
st.subheader(f"지금 뜨는 {market} Google 급상승 키워드")
if is_compare:
    kr_col, jp_col = st.columns(2, gap="large")
    for column, geo in ((kr_col, "KR"), (jp_col, "JP")):
        with column:
            st.markdown(f"#### {COUNTRY_LABEL[geo]}")
            show_trend_table(data_by_geo[geo][:10])
    compare_frame = pd.DataFrame([
        {"국가": COUNTRY_LABEL[row["geo"]], "키워드": keyword_display(row), "상대 신호": row["trend_score"]}
        for geo in selected_geos for row in data_by_geo[geo][:6]
    ])
    comparison_chart = px.bar(
        compare_frame, x="상대 신호", y="키워드", color="국가", orientation="h", facet_col="국가",
        color_discrete_map={"한국": "#9ab52f", "일본": "#526e62"},
    )
    comparison_chart.update_layout(height=380, margin=dict(l=10, r=10, t=35, b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    comparison_chart.update_yaxes(matches=None, showticklabels=True)
    st.plotly_chart(comparison_chart, width="stretch")

    common = sorted(set(row["keyword"].casefold() for row in data_by_geo["KR"]) & set(row["keyword"].casefold() for row in data_by_geo["JP"]))
    if common:
        st.caption("양국 동시 급상승: " + " · ".join(common))
    else:
        st.caption("현재 양국 목록에 정확히 일치하는 공통 키워드는 없습니다. 언어가 달라 같은 이슈도 별도 키워드로 집계될 수 있습니다.")
else:
    left, right = st.columns([1.45, 1], gap="large")
    with left: show_trend_table(all_trends[:12])
    with right:
        chart_data = pd.DataFrame([{"키워드": keyword_display(row), "검색량 하한": row["volume_min"]} for row in all_trends[:8]])
        chart = px.bar(chart_data.sort_values("검색량 하한"), x="검색량 하한", y="키워드", orientation="h", color_discrete_sequence=["#9ab52f"])
        chart.update_layout(height=410, margin=dict(l=10, r=10, t=20, b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", showlegend=False)
        st.plotly_chart(chart, width="stretch")

st.markdown('<div class="section-kicker">TIME WINDOW</div>', unsafe_allow_html=True)
st.subheader("최근 4시간 / 24시간 · 활성 / 종료")
time_tabs = st.tabs([f"4시간 {len(recent_4h)}", f"24시간 {len(recent_24h)}", f"활성 {len(active)}", f"종료 {len(ended)}"])
for tab, rows in zip(time_tabs, (recent_4h, recent_24h, active, ended)):
    with tab:
        show_trend_table(rows, is_compare) if rows else st.info("이 구간에 해당하는 트렌드가 없습니다.")

if "KR" in selected_geos:
    st.markdown('<div class="section-kicker">AGE SIGNAL · KOREA ONLY</div>', unsafe_allow_html=True)
    st.subheader("한국 10대·20대 추정 관심 키워드")
    kr_is_mock = source_mode["KR"] == "샘플"
    live_naver = get_naver_history(DB_PATH, False, geo="KR")
    age_demo = kr_is_mock or not live_naver
    age_points = get_naver_history(DB_PATH, True if age_demo else False, geo="KR")
    age_trends = data_by_geo["KR"] if not age_demo else add_trend_scores(get_trends(DB_PATH, True, geo="KR"))
    teen_rank = rank_age_keywords(age_trends, age_points, "teen")
    twenties_rank = rank_age_keywords(age_trends, age_points, "twenties")

    if age_demo and mode == "실데이터":
        st.warning("네이버 실데이터가 아직 없어 이 영역만 DEMO 예시를 표시합니다. API 키 설정 후 ‘선택 국가 실데이터 수집’을 누르세요.")
    age_left, age_right = st.columns(2, gap="large")
    for column, title, ranking, caption in (
        (age_left, "10대 추정", teen_rank, "네이버 13–18세(ages=2) + Google 신호"),
        (age_right, "20대 추정", twenties_rank, "네이버 19–24세·25–29세(ages=3·4) + Google 신호"),
    ):
        with column:
            st.markdown(f"#### {title} {'· DEMO' if age_demo else ''}")
            st.caption(caption)
            frame = pd.DataFrame(ranking[:7]).rename(columns={"keyword":"키워드", "age_fit_score":"추정 적합도", "naver_momentum":"7일 모멘텀(%)", "trend_score":"Google 신호"})
            st.dataframe(frame, hide_index=True, width="stretch", column_config={"추정 적합도":st.column_config.ProgressColumn(min_value=0,max_value=100,format="%.1f"), "7일 모멘텀(%)":st.column_config.NumberColumn(format="%+.1f%%")})

    if not naver_collector().configured:
        with st.expander("네이버 데이터가 없는 이유와 해결 방법"):
            st.write("네이버 데이터랩은 무료지만 Client ID와 Client Secret이 반드시 필요합니다. Streamlit Cloud의 App settings → Secrets에 아래 형식으로 저장한 뒤 다시 수집하세요.")
            st.code('NAVER_CLIENT_ID = "발급받은_ID"\nNAVER_CLIENT_SECRET = "발급받은_SECRET"', language="toml")

st.markdown('<div class="section-kicker">7-DAY MOVEMENT</div>', unsafe_allow_html=True)
st.subheader("키워드별 7일 변화")
trend_options = {(f"[{COUNTRY_LABEL[row['geo']]}] {keyword_display(row)}"): row for row in all_trends}
selected_label = st.selectbox("키워드 선택", list(trend_options), label_visibility="collapsed")
selected_row = trend_options[selected_label]
history = get_snapshot_history(DB_PATH, selected_row["keyword"], bool(selected_row["is_mock"]), days=7, geo=selected_row["geo"])
history_tabs = st.tabs(["Google 준실시간 스냅샷", "연령별 일간 추이"] if selected_row["geo"] == "KR" else ["Google 준실시간 스냅샷"])
with history_tabs[0]:
    frame = pd.DataFrame(history)
    if not frame.empty:
        frame["시각"] = pd.to_datetime(frame["collected_at"], utc=True).dt.tz_convert("Asia/Seoul")
        line = px.line(frame, x="시각", y="volume_min", markers=True, color_discrete_sequence=["#526e62"])
        line.update_layout(height=330, margin=dict(l=10,r=10,t=20,b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", yaxis_title="검색량 구간 하한", xaxis_title="")
        st.plotly_chart(line, width="stretch")
    else:
        st.info("스냅샷이 쌓이면 변화가 표시됩니다.")
if selected_row["geo"] == "KR":
    with history_tabs[1]:
        selected_naver = get_naver_history(DB_PATH, bool(selected_row["is_mock"]), selected_row["keyword"], "KR")
        if selected_naver:
            frame = pd.DataFrame(selected_naver).rename(columns={"period":"날짜", "ratio":"상대 검색 비율", "age_label":"연령"})
            line = px.line(frame, x="날짜", y="상대 검색 비율", color="연령", markers=True, color_discrete_sequence=["#9ab52f", "#526e62", "#d68f5f"])
            line.update_layout(height=330, margin=dict(l=10,r=10,t=20,b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", xaxis_title="")
            st.plotly_chart(line, width="stretch")
        else:
            st.info("이 키워드의 네이버 연령별 데이터가 없습니다.")

if "KR" in selected_geos:
    st.markdown('<div class="section-kicker">REGIONAL SIGNAL · KOREA</div>', unsafe_allow_html=True)
    st.subheader("국내 지역별 관심 비교")
    st.markdown('<div class="notice-box"><b>Google은 연령별 검색 데이터를 제공하지 않습니다.</b> 대신 공식 Trends는 지역·하위지역 관심도를 제공합니다. 실시간 RSS에는 지역 수치가 없어, 샘플 모드에서는 예시 차트를 보여주고 실데이터에서는 공식 Explore 지역 화면으로 연결합니다.</div>', unsafe_allow_html=True)
    kr_rows = data_by_geo["KR"]
    region_keyword = st.selectbox("지역 비교 키워드", [row["keyword"] for row in kr_rows], key="region_keyword")
    kr_is_mock = source_mode["KR"] == "샘플"
    region_rows = get_regional_interest(DB_PATH, kr_is_mock, region_keyword, "KR")
    if region_rows:
        region_frame = pd.DataFrame(region_rows).rename(columns={"region_name":"지역", "ratio":"상대 관심도"})
        region_chart = px.bar(region_frame.sort_values("상대 관심도"), x="상대 관심도", y="지역", orientation="h", color_discrete_sequence=["#9ab52f"])
        region_chart.update_layout(height=350, margin=dict(l=10,r=10,t=20,b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", showlegend=False)
        st.plotly_chart(region_chart, width="stretch")
        st.caption("DEMO 지역 분포 · 실제 Google 지역 수치가 아닙니다.")
    explore_url = f"https://trends.google.com/trends/explore?geo=KR&q={quote_plus(region_keyword)}"
    st.link_button("Google Trends 공식 지역 관심도 열기", explore_url, width="stretch")

st.markdown('<div class="section-kicker">CONTENT DESK</div>', unsafe_allow_html=True)
st.subheader("콘텐츠 아이디어 추천")
ideas = content_ideas(all_trends)
for start in range(0, len(ideas), 3):
    columns = st.columns(3)
    for column, idea in zip(columns, ideas[start:start+3]):
        country = COUNTRY_LABEL.get(idea.get("geo", "KR"), "")
        display_name = keyword_display({"keyword": idea["keyword"], "geo": idea.get("geo", "KR")})
        display_title = idea["title"].replace(idea["keyword"], display_name, 1)
        with column:
            st.markdown(f'<div class="idea-card"><span class="idea-tag">{html.escape(country)} · {html.escape(idea["format"])}</span><h4>{html.escape(display_title)}</h4><small>{html.escape(idea["urgency"])}</small></div>', unsafe_allow_html=True)

st.markdown('<div class="section-kicker">CONTEXT</div>', unsafe_allow_html=True)
st.subheader("관련 뉴스 · 관련 검색어")
for row in all_trends[:8]:
    with st.expander(f"[{COUNTRY_LABEL[row['geo']]}] {keyword_display(row)} · {row['volume_label']} · {'활성' if row['is_active'] else '종료'}"):
        if row.get("related_queries"):
            st.write("관련 검색어: " + " · ".join(row["related_queries"][:8]))
        for article in row.get("related_news", [])[:4]:
            if article.get("url"):
                st.markdown(f"- [{article.get('title','관련 기사')}]({article['url']}) — {article.get('source','출처')}")
        if row.get("explore_url"):
            st.link_button("Google Trends에서 살펴보기", row["explore_url"])

st.divider()
st.markdown(
    '<p class="fine-print"><b>해석 주의.</b> Google Trends는 공식 연령별 검색어를 제공하지 않습니다. 한국 연령 점수는 Google 급상승 신호와 네이버 연령별 일간 상대 검색 비율을 결합한 추정치입니다. 일본에는 네이버 연령 지표를 적용하지 않습니다. 국가 간 점수는 각 시장 안에서의 상대 신호이며 절대 검색량 비교가 아닙니다.</p>',
    unsafe_allow_html=True,
)
