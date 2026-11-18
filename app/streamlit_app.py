"""B 점주용 화면 (Streamlit).  실행: streamlit run app/streamlit_app.py

도민이 쉽게 쓰도록: 고민 → 가게 정보(눌러서 고르기) → 주문 기록(선택) → 결과 한눈에 보기 → 자세히 보기
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from coach import session as S
from coach.config import MOCK_DIR, USE_MOCK

st.set_page_config(page_title="제주 가게 운영실험 코치", layout="centered")

CODES = {k: v for k, v in json.loads((MOCK_DIR / "dong_codes.json").read_text(encoding="utf-8")).items()
         if not k.startswith("_")}
BUDGETS = {"없어요": 0, "5만 원": 50000, "10만 원": 100000, "20만 원": 200000, "30만 원": 300000, "50만 원": 500000}
STAFF = [1, 2, 3, 4]
CHANGEABLE = {"menu": "메뉴", "hours": "영업시간", "price": "가격", "channel": "배달·포장·SNS"}
DIAGNOSIS = {"co_decline": "동네 전체가 함께 줄었어요",
             "only_me": "동네는 괜찮은데 우리 가게만 줄었어요",
             "season_weather": "계절·날씨 영향이 커 보여요",
             "undetermined": "아직 판단하기 어려워요"}
# 실데이터 모드는 지역 데이터 기간(~8/31)에 맞춘 시연 파일을 쓴다
DEMO_FILE = "synthetic_stores.json" if USE_MOCK else "synthetic_stores_asof_20260831.json"
DEMO = {c["store_input"]["dong_name"]: c["store_input"]
        for c in json.loads((MOCK_DIR / DEMO_FILE).read_text(encoding="utf-8"))}

def recent_weeks(n=8) -> pd.DataFrame:
    monday = date.today() - timedelta(days=date.today().weekday())
    return pd.DataFrame({"week_start": [monday - timedelta(weeks=i) for i in range(n, 0, -1)],
                         "orders": None, "open_days": 6})


def key_of(options: dict, value):
    return next((k for k, v in options.items() if v == value), None)


def pick_or_type(container, label, options: list, default, unit: str, step: int, fmt=str):
    """자주 쓰는 값은 버튼으로 고르고, 없으면 '직접 입력'을 눌러 숫자를 적는다."""
    labels = [fmt(o) for o in options] + ["직접 입력"]
    preset = fmt(default) if default in options else ("직접 입력" if default is not None else None)
    choice = container.pills(label, labels, default=preset)
    if choice == "직접 입력":
        return container.number_input(f"{label} ({unit})", min_value=0, step=step,
                                      value=default if default not in options else None,
                                      placeholder=f"숫자만 적어 주세요 ({unit})")
    return options[labels.index(choice)] if choice else None


# ───────────────────────── 시연용 (사이드바)
with st.sidebar:
    st.caption("시연용")
    demo = st.selectbox("합성 점주 데이터", ["사용 안 함"] + list(DEMO), label_visibility="collapsed")
base = DEMO.get(demo, {})

# ───────────────────────── 머리말
st.title("제주 가게 운영실험 코치", anchor=False)
st.write("우리 동네 사람 흐름과 내 가게 주문을 비교해서, **내 형편에 맞는 2주짜리 실험** 하나를 알려 드려요.")
st.caption("지금은 **카페**만 지원해요. 시범 지역: 애월읍 · 노형동 · 한림읍 · 연동")

visit = st.segmented_control("방문", ["처음이에요", "2주 전에 받은 코드가 있어요"], default="처음이에요",
                             label_visibility="collapsed")
code, prev = None, None
if visit == "2주 전에 받은 코드가 있어요":
    code = st.text_input("받은 코드 6자리", max_chars=6, placeholder="예: K7RM3X").strip().upper()
    if code:
        try:
            prev = S.load_previous(code)
            st.info(f"지난번 실험: **{prev.get('title', prev.get('catalog_id'))}** ({prev.get('started_at')} 시작)" if prev
                    else "이 코드로 저장된 실험이 없어요. 코드를 다시 확인해 주세요.")
        except ValueError:
            st.warning("코드는 영문과 숫자 6자리예요.")

# ───────────────────────── 입력
st.subheader(anchor=False, body="1. 어떤 점이 고민이세요?")
question = st.text_area("고민", label_visibility="collapsed", height=90,
                        placeholder="편하게 적어 주세요. 예) 애월에서 카페해요. 요즘 손님이 줄었는데 뭘 해 보면 좋을까요?")

st.subheader(anchor=False, body="2. 가게 정보")
st.caption("눌러서 골라 주세요. 모르면 비워 두셔도 고민 내용에서 찾아보고, 없으면 다시 여쭤볼게요.")
dong = st.pills("가게 위치", list(CODES), default=base.get("dong_name"))
budget = pick_or_type(st, "이번 달 실험에 쓸 수 있는 돈", list(BUDGETS.values()), base.get("budget_krw"),
                      "원", 10000, fmt=lambda v: key_of(BUDGETS, v))
c1, c2 = st.columns(2)
staff_am = pick_or_type(c1, "오전에 일하는 사람", STAFF, base.get("staff_am"), "명", 1, fmt=lambda n: f"{n}명")
staff_pm = pick_or_type(c2, "오후에 일하는 사람", STAFF, base.get("staff_pm"), "명", 1, fmt=lambda n: f"{n}명")
changeable = st.pills("바꿔 볼 수 있는 것 (여러 개 가능)", list(CHANGEABLE), selection_mode="multi",
                      format_func=CHANGEABLE.get, default=base.get("changeable", []))

st.subheader(anchor=False, body="3. 최근 주문 수 (선택)")
st.caption("있으면 동네와 우리 가게를 비교할 수 있어 더 정확해요. 주마다 대략적인 주문 수만 적어 주세요.")
weeks_box = st.expander("주문 수 적기", expanded=False)
weeks_df = (pd.DataFrame(base["weeks"])[["week_start", "orders", "open_days"]]
            .assign(week_start=lambda d: pd.to_datetime(d.week_start).dt.date)) if base.get("weeks") else recent_weeks()
weeks_df = weeks_box.data_editor(weeks_df, num_rows="dynamic", hide_index=True, width="stretch",
                          column_config={"week_start": st.column_config.DateColumn("주 시작(월요일)"),
                                         "orders": st.column_config.NumberColumn("주문 수", min_value=0),
                                         "open_days": st.column_config.NumberColumn("문 연 날", min_value=0, max_value=7)})

if not st.button("결과 보기", type="primary", width="stretch"):
    st.stop()
    
# ───────────────────────── 실행
sid = code if (code and prev) else S.new_session_id()
weeks = [{"week_start": str(r["week_start"]), "orders": int(r["orders"]), "open_days": int(r["open_days"]),
          "sales_krw": 0} for r in weeks_df.dropna().to_dict("records") if r["orders"] > 0]
form = {"dong_name": dong, "dong_code": CODES.get(dong), "budget_krw": budget,
        "staff_am": staff_am, "staff_pm": staff_pm, "changeable": changeable, "weeks": weeks,
        "open_hours": base.get("open_hours"), "is_synthetic": bool(base)}

with st.spinner("동네 데이터를 불러오고 있어요. 처음엔 1분 정도 걸릴 수 있어요..."):
    out = S.run_session(sid, question, form)

st.divider()
if out.get("status") == "need_input":
    st.warning("**조금만 더 알려 주세요**\n\n" + "\n\n".join(f"- {q}" for q in out["ask_back"].split(" / ")))
    st.stop()

rp = out.get("report") or {}
st.subheader(anchor=False, body="결과 한눈에 보기")

# 범위 축소·판정 불가면 이유를 결과 맨 위에 먼저 보여 준다 (A 적합성 판정 사유)
SCOPE_TAGS = ("[범위 축소]", "[판정 불가]")
scope_notes = [s["text"] for s in rp.get("limits", []) if s["text"].startswith(SCOPE_TAGS)]
if scope_notes:
    st.info("**이번엔 일부만 분석했어요**\n\n" + "\n\n".join(f"- {t}" for t in scope_notes))

rv = out.get("experiment_review")
if rv:
    with st.container(border=True):
        st.markdown("**지난 실험은 어땠나요?**")
        st.subheader(anchor=False, body=f"{rv['verdict']}")
        if "orders_per_day_change_pct" in rv:
            st.write(f"실험 전보다 하루 평균 주문이 **{rv['orders_per_day_change_pct']:+.1f}%** 변했어요.")
        st.caption(rv["reason"] + (f" · {rv['region_note']}" if rv.get("region_note") else ""))

with st.container(border=True):
    st.markdown("**지금 우리 가게 상황**")
    st.subheader(anchor=False, body=f"{DIAGNOSIS.get(rp.get('diagnosis'), '아직 판단하기 어려워요')}")
    for s in rp.get("my_store", [])[:1] + rp.get("observed", [])[:1]:
        st.write(s["text"])
    if not weeks:
        st.info("최근 주문 수를 적어 주시면 동네 흐름과 우리 가게를 비교해서 더 정확히 알려 드릴 수 있어요.")

exp = rp.get("experiment")
if exp:
    with st.container(border=True):
        st.markdown("**이번 2주 동안 해 볼 것**")
        st.subheader(anchor=False, body=f"{exp['title']}")
        m1, m2, m3 = st.columns(3)
        m1.metric("드는 돈", f"{exp['cost_krw']:,}원")
        m2.metric("추가 인력", f"{exp['extra_staff']}명")
        m3.metric("기간", f"{exp['duration_days']}일")
        st.write(f"**이렇게 확인해요:** {exp['metric']}")
        st.write(f"**이럴 땐 그만둬요:** {exp['stop_rule']}")

# 실험이 있을 때만 재방문 코드를 준다 (실험이 없으면 저장도 안 돼서 코드가 쓸모없음)
if exp:
    with st.container(border=True):
        st.markdown("**2주 뒤에 다시 와 주세요**")
        st.header(sid, anchor=False)
        st.caption("이 코드를 적어 두시면 2주 뒤 실험 결과를 함께 점검해 드려요. "
                   "코드만 알면 기록을 볼 수 있으니 **다른 사람에게 알려 주지 마세요.**")
else:
    st.caption("이번엔 해 볼 실험을 고르지 않아서 재방문 코드는 만들지 않았어요.")

if rp.get("is_synthetic_input"):
    st.caption("※ 시연용 합성 점주 데이터로 만든 결과예요.")
if out.get("status") == "fallback":
    st.caption("※ AI 이해 기능이 잠시 불안정해 기본 방식으로 처리했어요.")

# ───────────────────────── 자세히 보기
st.subheader(anchor=False, body="자세히 보기")
with st.expander("근거와 원인 후보"):
    for title, key in [("동네에서 관측된 사실", "observed"), ("우리 가게 비교", "my_store"),
                       ("원인 후보", "hypotheses"), ("더 확인하면 좋은 것", "to_check")]:
        if rp.get(key):
            st.markdown(f"**{title}**")
            st.markdown("\n".join(f"- {s['text']}" for s in rp[key]))
if exp:
    with st.expander("왜 이 실험인가요? · 다른 후보"):
        st.write(exp["rationale"]["text"])
        for a in rp.get("alternatives", []):
            st.markdown(f"- {a['title']} ({a['cost_krw']:,}원)")
if rp.get("support_programs"):
    with st.expander("받을 수 있는 지원제도"):
        st.markdown("\n".join(f"- {s['text']}" for s in rp["support_programs"]))
with st.expander("한계와 데이터 출처"):
    st.markdown("\n".join(f"- {s['text']}" for s in rp.get("limits", [])))
    st.caption(" / ".join(rp.get("sources", [])))
with st.expander("처리 과정 (심사·개발용)"):
    st.json(out.get("trace", []))