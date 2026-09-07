"""
스케줄 메모 자동화 — Streamlit 버전 (Streamlit Community Cloud 무료 배포용).
Gradio 버전(app.py)과 같은 기능. 사진 읽기(read_schedule)와 그리기(annotate_schedule)는 공유.

로컬 실행:  streamlit run streamlit_app.py
비밀번호:   .streamlit/secrets.toml 에  APP_PASSWORD = "원하는비밀번호"  (Cloud에서는 앱 Settings → Secrets)
"""

import base64
import os
import shutil
import tempfile
import traceback
import uuid
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

# 임시 파일은 이 폴더 안 _tmp 에 (사진은 처리 직후 삭제)
_TMP_ROOT = Path(__file__).resolve().parent / "_tmp" / "streamlit"
_TMP_ROOT.mkdir(parents=True, exist_ok=True)

import onnxruntime  # noqa: F401,E402  (Pillow보다 먼저)
import annotate_schedule as A  # noqa: E402
import read_schedule as R  # noqa: E402

st.set_page_config(page_title="스케줄 메모 자동화", page_icon="📅", layout="centered")


# ── 비밀번호 ──────────────────────────────────────────────
def get_password():
    try:
        return str(st.secrets.get("APP_PASSWORD", "")).strip()
    except Exception:
        return os.environ.get("APP_PASSWORD", "").strip()


PW = get_password()
if PW and not st.session_state.get("ok"):
    st.title("스케줄 메모 자동화")
    entered = st.text_input("공유받은 비밀번호를 입력하세요", type="password")
    if st.button("입장"):
        if entered == PW:
            st.session_state["ok"] = True
            st.rerun()
        else:
            st.error("비밀번호가 달라요.")
    st.stop()


# ── OCR 엔진은 한 번만 로드 ──────────────────────────────
@st.cache_resource(show_spinner="글자 인식 준비 중...")
def load_ocr():
    return R.get_ocr()


# ── 화면 ─────────────────────────────────────────────────
st.title("스케줄 메모 자동화")
st.markdown(
    "회사 스케줄 앱 **원본 스크린샷**을 올리면 형광펜·여행 라벨·쉬는 날 큰 숫자를 자동으로 넣어 드려요.  \n"
    "올린 사진은 처리 직후 삭제되고 서버에 남지 않아요. 처리에 20~40초 정도 걸려요."
)

uploaded = st.file_uploader("원본 스케줄 스크린샷", type=["jpg", "jpeg", "png", "webp"])
title_in = st.text_input("제목 (비우면 '9월'처럼 그 달 이름만 들어가요)", placeholder="예: 9월")
go = st.button("메모 만들기", type="primary", use_container_width=True)


def run(file_bytes: bytes, name: str):
    load_ocr()
    work = _TMP_ROOT / uuid.uuid4().hex
    work.mkdir()
    try:
        src = work / "input.png"
        from PIL import Image
        import io
        Image.open(io.BytesIO(file_bytes)).convert("RGB").save(src)

        bar = st.progress(0, text="사진 읽는 중")
        spec = R.read_schedule(src, name=name,
                               progress=lambda d, n: bar.progress(min(d / n, 0.95), text=f"{d}일 읽는 중"))
        bar.progress(0.97, text="메모 그리는 중")
        out = work / f"{spec['month']}월_메모.jpg"
        info = A.annotate(src, spec, out)
        bar.empty()
        result = out.read_bytes()
        return result, spec, info
    finally:
        shutil.rmtree(work, ignore_errors=True)   # 원본·결과 모두 서버에서 삭제


if go:
    if uploaded is None:
        st.warning("스케줄 사진을 먼저 올려 주세요.")
    else:
        try:
            result, spec, info = run(uploaded.getvalue(), title_in.strip())
            st.session_state["result"] = result
            st.session_state["fname"] = f"{spec['month']}월_메모.jpg"
            lines = [f"**{spec['year']}년 {spec['month']}월**", ""]
            for t in info["trips"]:
                rng = f"{t['start']}일" if t["start"] == t["end"] else f"{t['start']}~{t['end']}일"
                lines.append(f"- {rng} : {t['label']}")
            lines.append("")
            lines.append("쉬는 날(큰 숫자): " + ", ".join(f"{d}일" for d in info["big_days"]))
            unread = [d for d, ls in spec["days"].items() for l in ls if "?" in l]
            if unread:
                lines.append("")
                lines.append("⚠️ 못 읽은 칸이 있어요: " + ", ".join(f"{d}일" for d in unread) + " (그림을 확인해 주세요)")
            st.session_state["summary"] = "\n".join(lines)
        except RuntimeError as e:
            st.error(f"사진을 읽지 못했어요: {e}\n\n앱에서 저장한 원본 스크린샷(달력 전체가 보이는 것)인지 확인해 주세요.")
        except Exception:
            traceback.print_exc()
            st.error("사진을 읽는 중 문제가 생겼어요. 원본 스크린샷인지 확인해 주세요.")

if st.session_state.get("result"):
    result = st.session_state["result"]
    fname = st.session_state["fname"]
    st.image(result, caption="완성본", use_container_width=True)

    # 사진 보관함에 저장: 폰이면 공유 창(→ 이미지 저장), PC면 다운로드
    b64 = base64.b64encode(result).decode()
    components.html(f"""
    <button id="save" style="width:100%;padding:12px;font-size:16px;border-radius:8px;
            border:1px solid #ccc;background:#f6f6f6;cursor:pointer">📥 사진 보관함에 저장</button>
    <script>
    document.getElementById('save').onclick = async () => {{
      const bin = atob("{b64}");
      const arr = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
      const blob = new Blob([arr], {{type: 'image/jpeg'}});
      const file = new File([blob], "{fname}", {{type: 'image/jpeg'}});
      if (navigator.canShare && navigator.canShare({{files: [file]}})) {{
        try {{ await navigator.share({{files: [file], title: "{fname}"}}); return; }}
        catch (e) {{ if (e && e.name === 'AbortError') return; }}
      }}
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = "{fname}";
      document.body.appendChild(a); a.click(); a.remove();
    }};
    </script>
    """, height=60)
    st.caption("폰에서는 공유 창이 뜨면 「이미지 저장」을 누르세요. PC에서는 바로 다운로드돼요.")
    st.markdown(st.session_state["summary"])
