"""Streamlit dashboard for quasi-real-time Korean search trends."""

from __future__ import annotations

import html
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:  # The dashboard still works without optional auto refresh.
    st_autorefresh = None

from collectors.google_trending_now import GoogleTrendingNowCollector
from collectors.naver_datalab import NaverDataLabCollector
from database import (
    ensure_mock_data,
    get_last_run,
    get_naver_history,
    get_snapshot_history,
    get_trends,
    has_data,
    init_db,
    log_collection_run,
    save_google_trends,
    save_naver_age_trends,
)
from scoring import add_trend_scores, content_ideas, rank_age_keywords


KST = timezone(timedelta(hours=9))
DB_PATH = Path(os.getenv("TREND_DB_PATH", Path(__file__).with_name("trends.db")))


st.set_page_config(
    page_title="한국 트렌드 레이더",
    page_icon="↗",
    layout="wide",
    initial_sidebar_state="expanded",
)


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;600;700&family=Noto+Sans+KR:wght@400;500;700&display=swap');
        :root { --ink:#10231d; --muted:#61736c; --mint:#d9f56f; --paper:#f6f7f2; --line:#dce3da; }
        .stApp { background: var(--paper); color: var(--ink); }
        html, body, [class*="css"] { font-family: 'DM Sans','Noto Sans KR',sans-serif; }
        [data-testid="stSidebar"] { background: #11251e; }
        [data-testid="stSidebar"] * { color: #eef5ef; }
        [data-testid="stSidebar"] .stButton button { border: 1px solid #587066; background:#d9f56f; color:#10231d; }
        [data-testid="stSidebar"] .stButton button p { color:#10231d !important; font-weight:700; }
        .hero { padding: 1.1rem 0 1.3rem; border-bottom: 1px solid var(--line); margin-bottom: 1.2rem; }
        .eyebrow { color:#446358; font-size:.78rem; font-weight:700; letter-spacing:.12em; text-transform:uppercase; }
        .hero h1 { color:var(--ink); font-size:2.65rem; line-height:1.08; margin:.35rem 0 .55rem; letter-spacing:-.045em; }
        .hero p { color:var(--muted); max-width:780px; font-size:1rem; margin:0; }
        .status-pill { display:inline-block; background:#e8eddf; color:#355047; border-radius:999px; padding:.25rem .65rem; font-size:.76rem; margin-top:.8rem; }
        .metric-card { min-height:118px; background:#fff; border:1px solid var(--line); border-radius:18px; padding:1rem 1.1rem; box-shadow:0 8px 24px rgba(25,48,39,.04); }
        .metric-label { color:var(--muted); font-size:.78rem; }
        .metric-value { font-size:1.8rem; font-weight:700; letter-spacing:-.04em; margin:.25rem 0; }
        .metric-note { color:#75847e; font-size:.75rem; }
        .section-kicker { color:#61736c; font-size:.76rem; font-weight:700; letter-spacing:.08em; text-transform:uppercase; margin-top:1.3rem; }
        .idea-card { background:#fff; border:1px solid var(--line); border-radius:16px; padding:1rem; height:145px; }
        .idea-tag { display:inline-block; border-radius:999px; padding:.18rem .55rem; background:#edf4d0; color:#344a23; font-size:.7rem; }
        .idea-card h4 { margin:.65rem 0 .35rem; line-height:1.45; font-size:.98rem; }
        .idea-card small { color:#75847e; }
        .fine-print { color:#708078; font-size:.75rem; line-height:1.55; }
        [data-testid="stMetric"] { background:#fff; border:1px solid var(--line); padding:1rem; border-radius:16px; }
        [data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:14px; overflow:hidden; }
        div[data-testid="stExpander"] { background:#fff; border-color:var(--line); }
        </style>
        """,
        unsafe_allow_html=True,
    )


def kst_text(value: str | None, include_date: bool = False) -> str:
    if not value:
        return "—"
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    fmt = "%m.%d %H:%M" if include_date else "%H:%M"
    return parsed.astimezone(KST).strftime(fmt)


def relative_started(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    minutes = max(0, int((datetime.now(timezone.utc) - parsed).total_seconds() / 60))
    if minutes < 60:
        return f"{minutes}분 전"
    if minutes < 24 * 60:
        return f"{minutes // 60}시간 전"
    return f"{minutes // (24 * 60)}일 전"


def collect_live_data() -> tuple[bool, str]:
    """Collect Google now; collect Naver once per day when credentials exist."""

    started = datetime.now(timezone.utc)
    try:
        google_rows = GoogleTrendingNowCollector("KR").fetch()
        save_google_trends(DB_PATH, google_rows, collected_at=started, is_mock=False)
        log_collection_run(DB_PATH, "google", "success", len(google_rows), started_at=started)
    except Exception as exc:
        log_collection_run(DB_PATH, "google", "failed", 0, str(exc), started_at=started)
        return False, f"Google 수집 실패: {exc}"

    naver = NaverDataLabCollector()
    if not naver.configured:
        return True, f"Google {len(google_rows)}개 수집 완료 · 네이버 키 미설정"

    last_naver = get_last_run(DB_PATH, "naver")
    should_collect_naver = True
    if last_naver and last_naver["status"] == "success":
        finished = datetime.fromisoformat(last_naver["finished_at"])
        should_collect_naver = datetime.now(timezone.utc) - finished >= timedelta(hours=20)
    if not should_collect_naver:
        return True, f"Google {len(google_rows)}개 수집 완료 · 네이버 일간 캐시 사용"

    keywords = [row["keyword"] for row in sorted(
        google_rows, key=lambda item: item.get("volume_min", 0), reverse=True
    )[:10]]
    naver_started = datetime.now(timezone.utc)
    try:
        naver_rows = naver.fetch_last_7_days(keywords)
        save_naver_age_trends(DB_PATH, naver_rows, collected_at=naver_started, is_mock=False)
        log_collection_run(DB_PATH, "naver", "success", len(naver_rows), started_at=naver_started)
        return True, f"Google {len(google_rows)}개 · 네이버 {len(naver_rows)}포인트 수집 완료"
    except Exception as exc:
        log_collection_run(DB_PATH, "naver", "failed", 0, str(exc), started_at=naver_started)
        return True, f"Google 수집 완료 · 네이버 수집 실패: {exc}"


def collection_due(minutes: int = 15) -> bool:
    last = get_last_run(DB_PATH, "google")
    if not last or last["status"] != "success":
        return True
    finished = datetime.fromisoformat(last["finished_at"])
    return datetime.now(timezone.utc) - finished >= timedelta(minutes=minutes)


def metric_card(label: str, value: str, note: str) -> None:
    st.markdown(
        f"""
        <div class="metric-card">
          <div class="metric-label">{html.escape(label)}</div>
          <div class="metric-value">{html.escape(value)}</div>
          <div class="metric-note">{html.escape(note)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def table_rows(trends: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "키워드": row["keyword"],
                "검색량 구간": row["volume_label"],
                "상승률": row.get("growth_rate"),
                "시작": relative_started(row["started_at"]),
                "상태": "활성" if row["is_active"] else "종료",
                "종합 점수": row["trend_score"],
            }
            for row in trends
        ]
    )


init_db(DB_PATH)
ensure_mock_data(DB_PATH)
inject_styles()

live_exists = has_data(DB_PATH, False)
if st.session_state.pop("_switch_to_live", False):
    st.session_state.data_mode = "실데이터"
if "data_mode" not in st.session_state:
    st.session_state.data_mode = "실데이터" if live_exists else "샘플 데이터"

with st.sidebar:
    st.markdown("### ↗ TREND RADAR")
    st.caption("KOREA · QUASI REAL-TIME")
    mode = st.radio("데이터 모드", ["샘플 데이터", "실데이터"], key="data_mode")
    auto_refresh = st.toggle("15분 자동 갱신", value=False, disabled=mode == "샘플 데이터")
    if auto_refresh and st_autorefresh:
        st_autorefresh(interval=15 * 60 * 1000, key="trend_auto_refresh")
    if st.button("지금 실데이터 수집", width="stretch"):
        with st.spinner("Google Trending Now를 확인하는 중…"):
            ok, message = collect_live_data()
        if ok:
            st.session_state._switch_to_live = True
            st.success(message)
            st.rerun()
        else:
            st.error(message)

    if mode == "실데이터" and auto_refresh and collection_due():
        ok, message = collect_live_data()
        if ok:
            st.toast(message)
        else:
            st.warning(message)

    st.divider()
    last_google = get_last_run(DB_PATH, "google")
    if last_google:
        status_icon = "●" if last_google["status"] == "success" else "▲"
        st.caption(
            f"{status_icon} 최근 Google 수집\n\n"
            f"{kst_text(last_google['finished_at'], include_date=True)} KST"
        )
    else:
        st.caption("아직 실데이터 수집 이력이 없습니다.")
    st.caption("네이버 키: " + ("연결됨" if NaverDataLabCollector().configured else "미설정"))
    st.markdown(
        '<p class="fine-print">실데이터는 Google Trends Trending Now RSS를 사용합니다. '
        "연령별 수치는 네이버 데이터랩의 상대 검색 비율을 결합한 추정치입니다.</p>",
        unsafe_allow_html=True,
    )

is_mock = mode == "샘플 데이터"
if not is_mock and not has_data(DB_PATH, False):
    st.warning("실데이터가 아직 없습니다. 왼쪽의 ‘지금 실데이터 수집’을 누르세요. 아래에는 샘플을 표시합니다.")
    is_mock = True

all_trends = add_trend_scores(get_trends(DB_PATH, is_mock))
recent_4h = [row for row in all_trends if datetime.fromisoformat(row["started_at"]) >= datetime.now(timezone.utc) - timedelta(hours=4)]
recent_24h = [row for row in all_trends if datetime.fromisoformat(row["started_at"]) >= datetime.now(timezone.utc) - timedelta(hours=24)]
active = [row for row in all_trends if row["is_active"]]
ended = [row for row in all_trends if not row["is_active"]]
naver_points = get_naver_history(DB_PATH, is_mock)
teen_rank = rank_age_keywords(all_trends, naver_points, "teen")
twenties_rank = rank_age_keywords(all_trends, naver_points, "twenties")

mode_label = "DEMO DATA" if is_mock else "LIVE COLLECTION"
st.markdown(
    f"""
    <div class="hero">
      <div class="eyebrow">Korea signal desk · {mode_label}</div>
      <h1>지금, 한국의 검색 관심은<br>어디로 움직이나</h1>
      <p>Google의 급상승 신호를 빠르게 포착하고, 네이버의 연령별 일간 검색 추이로 10대·20대 적합도를 보조 판단합니다.</p>
      <span class="status-pill">● {'샘플 데이터로 실행 중' if is_mock else '준실시간 수집 데이터'}</span>
    </div>
    """,
    unsafe_allow_html=True,
)

metric_columns = st.columns(4)
with metric_columns[0]:
    metric_card("활성 트렌드", f"{len(active)}개", "지금도 평소보다 관심이 높은 신호")
with metric_columns[1]:
    metric_card("최근 4시간", f"{len(recent_4h)}개", "즉시 대응할 단기 기회")
with metric_columns[2]:
    metric_card("최근 24시간", f"{len(recent_24h)}개", "오늘 시작된 급상승 키워드")
with metric_columns[3]:
    top_score = all_trends[0]["trend_score"] if all_trends else 0
    metric_card("최고 신호 점수", f"{top_score:.0f}/100", "검색량·상승·최신성 결합")

st.markdown('<div class="section-kicker">NOW TRENDING</div>', unsafe_allow_html=True)
st.subheader("지금 뜨는 한국 Google 급상승 키워드")
left, right = st.columns([1.45, 1], gap="large")
with left:
    top_table = table_rows(all_trends[:12])
    st.dataframe(
        top_table,
        hide_index=True,
        width="stretch",
        column_config={
            "상승률": st.column_config.NumberColumn("상승률", format="%+.0f%%"),
            "종합 점수": st.column_config.ProgressColumn("종합 점수", min_value=0, max_value=100, format="%.1f"),
        },
    )
with right:
    chart_data = pd.DataFrame(
        [{"키워드": row["keyword"], "검색량 하한": row["volume_min"]} for row in all_trends[:8]]
    )
    if not chart_data.empty:
        figure = px.bar(
            chart_data.sort_values("검색량 하한"),
            x="검색량 하한",
            y="키워드",
            orientation="h",
            color_discrete_sequence=["#9ab52f"],
        )
        figure.update_layout(
            height=410,
            margin=dict(l=10, r=10, t=20, b=10),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis_title="검색량 구간 하한",
            yaxis_title="",
            showlegend=False,
        )
        figure.update_xaxes(gridcolor="#e4e9e1")
        st.plotly_chart(figure, width="stretch")

st.markdown('<div class="section-kicker">TIME WINDOW</div>', unsafe_allow_html=True)
st.subheader("최근 4시간 / 24시간 · 활성 / 종료")
tab_4h, tab_24h, tab_active, tab_ended = st.tabs(
    [f"4시간 {len(recent_4h)}", f"24시간 {len(recent_24h)}", f"활성 {len(active)}", f"종료 {len(ended)}"]
)
for tab, rows in ((tab_4h, recent_4h), (tab_24h, recent_24h), (tab_active, active), (tab_ended, ended)):
    with tab:
        if rows:
            st.dataframe(table_rows(rows), hide_index=True, width="stretch")
        else:
            st.info("이 구간에 해당하는 트렌드가 없습니다.")

st.markdown('<div class="section-kicker">AGE SIGNAL</div>', unsafe_allow_html=True)
st.subheader("10대·20대 추정 관심 키워드")
age_left, age_right = st.columns(2, gap="large")
for column, title, ranking, caption in (
    (age_left, "10대 추정", teen_rank, "네이버 13–18세(ages=2) + Google 신호"),
    (age_right, "20대 추정", twenties_rank, "네이버 19–24세·25–29세(ages=3·4) + Google 신호"),
):
    with column:
        st.markdown(f"#### {title}")
        st.caption(caption)
        if ranking:
            frame = pd.DataFrame(ranking[:7]).rename(
                columns={
                    "keyword": "키워드",
                    "age_fit_score": "추정 적합도",
                    "naver_momentum": "7일 모멘텀(%)",
                    "trend_score": "Google 신호",
                }
            )
            st.dataframe(
                frame,
                hide_index=True,
                width="stretch",
                column_config={
                    "추정 적합도": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f"),
                    "7일 모멘텀(%)": st.column_config.NumberColumn(format="%+.1f%%"),
                },
            )
        else:
            st.info("네이버 연령별 데이터가 없습니다. API 키를 설정하고 수집하세요.")

st.markdown('<div class="section-kicker">7-DAY MOVEMENT</div>', unsafe_allow_html=True)
st.subheader("키워드별 7일 변화")
selected = st.selectbox("키워드 선택", [row["keyword"] for row in all_trends], label_visibility="collapsed")
google_tab, naver_tab = st.tabs(["Google 준실시간 스냅샷", "네이버 연령별 일간 추이"])
with google_tab:
    google_history = get_snapshot_history(DB_PATH, selected, is_mock, days=7)
    if google_history:
        history_frame = pd.DataFrame(google_history)
        history_frame["시각"] = pd.to_datetime(history_frame["collected_at"], utc=True).dt.tz_convert("Asia/Seoul")
        line = px.line(history_frame, x="시각", y="volume_min", markers=True, color_discrete_sequence=["#526e62"])
        line.update_layout(
            height=330, margin=dict(l=10, r=10, t=20, b=10),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            yaxis_title="검색량 구간 하한", xaxis_title="",
        )
        line.update_yaxes(gridcolor="#e4e9e1")
        st.plotly_chart(line, width="stretch")
    else:
        st.info("스냅샷이 쌓이면 변화가 표시됩니다.")
with naver_tab:
    selected_naver = get_naver_history(DB_PATH, is_mock, selected)
    if selected_naver:
        naver_frame = pd.DataFrame(selected_naver).rename(columns={"period": "날짜", "ratio": "상대 검색 비율", "age_label": "연령"})
        naver_line = px.line(
            naver_frame, x="날짜", y="상대 검색 비율", color="연령", markers=True,
            color_discrete_sequence=["#9ab52f", "#526e62", "#d68f5f"],
        )
        naver_line.update_layout(
            height=330, margin=dict(l=10, r=10, t=20, b=10),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", xaxis_title="",
        )
        naver_line.update_yaxes(gridcolor="#e4e9e1")
        st.plotly_chart(naver_line, width="stretch")
    else:
        st.info("이 키워드의 네이버 7일 데이터가 없습니다.")

st.markdown('<div class="section-kicker">CONTENT DESK</div>', unsafe_allow_html=True)
st.subheader("콘텐츠 아이디어 추천")
ideas = content_ideas(all_trends)
for start in range(0, len(ideas), 3):
    columns = st.columns(3)
    for column, idea in zip(columns, ideas[start : start + 3]):
        with column:
            st.markdown(
                f"""
                <div class="idea-card">
                  <span class="idea-tag">{html.escape(idea['format'])}</span>
                  <h4>{html.escape(idea['title'])}</h4>
                  <small>{html.escape(idea['urgency'])} · 키워드 {html.escape(idea['keyword'])}</small>
                </div>
                """,
                unsafe_allow_html=True,
            )

st.markdown('<div class="section-kicker">CONTEXT</div>', unsafe_allow_html=True)
st.subheader("관련 뉴스 · 관련 검색어")
for row in all_trends[:6]:
    with st.expander(f"{row['keyword']} · {row['volume_label']} · {'활성' if row['is_active'] else '종료'}"):
        queries = row.get("related_queries", [])
        if queries:
            st.write("관련 검색어: " + " · ".join(queries[:8]))
        news = row.get("related_news", [])
        if news:
            for article in news[:4]:
                source = article.get("source") or "출처"
                if article.get("url"):
                    st.markdown(f"- [{article.get('title', '관련 기사')}]({article['url']}) — {source}")
                else:
                    st.write(f"- {article.get('title', '관련 기사')} — {source}")
        if row.get("explore_url"):
            st.link_button("Google Trends에서 살펴보기", row["explore_url"])

st.divider()
st.markdown(
    """
    <p class="fine-print"><b>해석 주의.</b> 이 화면은 10대·20대 Google 검색어를 직접 제공하는 공식 데이터가 아닙니다.
    Google Trending Now의 한국 급상승 신호와 네이버 데이터랩의 일간 연령별 상대 검색 비율을 결합한 추정 모델입니다.
    검색량 구간은 절대 검색량의 하한이며, 연령별 수치는 서로 다른 플랫폼 간 방향성 보조지표로만 사용하세요.</p>
    """,
    unsafe_allow_html=True,
)
