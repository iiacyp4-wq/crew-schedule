---
title: 스케줄 메모 자동화
emoji: 📅
colorFrom: blue
colorTo: yellow
sdk: gradio
sdk_version: 6.26.0
python_version: "3.11"
app_file: app.py
pinned: false
---

# 스케줄 메모 자동화

승무원 월간 스케줄 앱의 원본 스크린샷을 올리면 형광펜·여행 라벨·쉬는 날 큰 숫자를 자동으로 넣어 줍니다.
글자 인식은 서버 안에서 RapidOCR(오프라인)로 처리하고, 외부 AI 서비스는 쓰지 않습니다.

- `streamlit_app.py` : 웹 화면 (Streamlit) — Streamlit Community Cloud 배포용
- `app.py` : 웹 화면 (Gradio) — 로컬 실행/공유 링크용
- `read_schedule.py` : 사진 → 스케줄 글자 (OCR)
- `annotate_schedule.py` : 스케줄 글자 → 메모 그린 그림
- `assets/` : 나눔고딕 폰트(OFL), 돌고래 그림(Noto Emoji, Apache 2.0)

로컬 실행: `pip install -r requirements.txt` 후 `streamlit run streamlit_app.py` 또는 `python app.py`
비밀번호: Streamlit은 `.streamlit/secrets.toml`의 `APP_PASSWORD`, Gradio는 환경변수 `APP_PASSWORD`
