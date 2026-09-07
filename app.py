"""
스케줄 메모 자동화 웹페이지.
사진을 올리면 컴퓨터 안의 글자 인식(OCR)으로 읽고, 형광펜·여행 라벨·큰 날짜를 넣은 그림을 돌려준다.
Claude 등 외부 AI 서비스 호출 없음 → 사용 비용 0원.

로컬 실행:  python app.py   →  브라우저에서 http://127.0.0.1:7860
Hugging Face Spaces 에 올리면 링크 하나로 누구나 사용 가능.
"""

import os
import shutil
import time
import traceback
import uuid
from pathlib import Path

# 임시 파일은 이 폴더 안 _tmp 에 둔다 (백신·다른 프로그램이 잡고 있는 시스템 임시 폴더를 피함)
_TMP_ROOT = Path(__file__).resolve().parent / "_tmp"
_TMP_ROOT.mkdir(exist_ok=True)
os.environ.setdefault("GRADIO_TEMP_DIR", str(_TMP_ROOT / "gradio"))

import onnxruntime  # noqa: F401  (Pillow보다 먼저 불러와야 Windows에서 DLL 충돌 없음)
import gradio as gr
from PIL import Image

import annotate_schedule as A
import read_schedule as R

TMP = _TMP_ROOT / "work"
TMP.mkdir(exist_ok=True)
KEEP_SECONDS = 10 * 60          # 결과 파일은 10분 뒤 자동 삭제 (다운로드할 시간만 줌)


def cleanup_old():
    """오래된 작업 폴더 삭제. 사진은 서버에 남기지 않는다."""
    now = time.time()
    for d in TMP.iterdir():
        try:
            if d.is_dir() and now - d.stat().st_mtime > KEEP_SECONDS:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def process(image, name, progress=gr.Progress()):
    if image is None:
        raise gr.Error("스케줄 사진을 먼저 올려 주세요.")
    cleanup_old()
    name = (name or "").strip()
    work = TMP / uuid.uuid4().hex        # 사람마다 다른 임시 폴더 (동시에 써도 안 섞임)
    work.mkdir()
    src = work / "input.png"
    Image.fromarray(image).convert("RGB").save(src)

    try:
        spec = R.read_schedule(src, name=name,
                               progress=lambda d, n: progress(d / n, desc=f"{d}일 읽는 중"))
    except RuntimeError as e:
        raise gr.Error(f"사진을 읽지 못했어요: {e}\n앱에서 저장한 원본 스크린샷(달력 전체가 보이는 것)인지 확인해 주세요.")
    except Exception:
        traceback.print_exc()
        raise gr.Error("사진을 읽는 중 문제가 생겼어요. 원본 스크린샷인지 확인해 주세요.")

    progress(0.95, desc="메모 그리는 중")
    safe = "".join(ch for ch in name if ch.isalnum() or ch in " _-").strip()
    out = work / f"{(safe + '_') if safe and '월' not in safe else ''}{spec['month']}월_메모.jpg"
    info = A.annotate(src, spec, out)
    src.unlink(missing_ok=True)          # 원본 사진은 그리자마자 삭제

    lines = [f"**{spec['year']}년 {spec['month']}월**", ""]
    for t in info["trips"]:
        rng = f"{t['start']}일" if t["start"] == t["end"] else f"{t['start']}~{t['end']}일"
        lines.append(f"- {rng} : {t['label']}")
    lines.append("")
    lines.append("쉬는 날(큰 숫자): " + ", ".join(f"{d}일" for d in info["big_days"]))
    unread = [(d, l) for d, ls in spec["days"].items() for l in ls if "?" in l]
    if unread:
        lines.append("")
        lines.append("⚠️ 못 읽은 칸이 있어요: " + ", ".join(f"{d}일" for d, _ in unread) + " (그림을 확인해 주세요)")

    return str(out), "\n".join(lines), str(out)


# delete_cache=(검사 주기, 보관 시간) 초 단위: Gradio가 받아둔 업로드/결과 사본도 10분 뒤 삭제
with gr.Blocks(title="스케줄 메모 자동화", delete_cache=(300, KEEP_SECONDS)) as demo:
    gr.Markdown(
        "# 스케줄 메모 자동화\n"
        "회사 스케줄 앱 **원본 스크린샷**을 올리면 형광펜·여행 라벨·쉬는 날 큰 숫자를 자동으로 넣어 드려요.\n"
        "올린 사진은 처리 직후 삭제되고 결과는 10분 뒤 자동 삭제돼요. 처리에 20~40초 정도 걸려요."
    )
    with gr.Row():
        with gr.Column():
            img_in = gr.Image(label="원본 스케줄 스크린샷", type="numpy")
            name_in = gr.Textbox(label="제목 (비우면 '9월'처럼 그 달 이름만 들어가요)", placeholder="예: 9월")
            btn = gr.Button("메모 만들기", variant="primary")
        with gr.Column():
            img_out = gr.Image(label="완성본", type="filepath")
            save_btn = gr.Button("📥 사진 보관함에 저장", variant="secondary")
            gr.Markdown("<small>폰에서는 공유 창이 뜨면 「이미지 저장」을 누르세요. PC에서는 바로 다운로드돼요.</small>")
            dl = gr.File(label="파일로 다운로드", visible=False)
            summary = gr.Markdown()
    btn.click(process, inputs=[img_in, name_in], outputs=[img_out, summary, dl])

    # 브라우저에서 실행되는 저장 동작: 폰이면 공유 창(→ 이미지 저장), 아니면 파일 다운로드
    save_btn.click(None, inputs=[img_out], js="""
    async (img) => {
      if (!img || !img.url) { alert('먼저 메모를 만들어 주세요.'); return; }
      const res = await fetch(img.url);
      const blob = await res.blob();
      const name = (img.orig_name || 'schedule.jpg').replace(/\.png$/i, '.jpg');
      const file = new File([blob], name, {type: 'image/jpeg'});
      if (navigator.canShare && navigator.canShare({files: [file]})) {
        try { await navigator.share({files: [file], title: name}); return; }
        catch (e) { if (e && e.name === 'AbortError') return; }
      }
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = name;
      document.body.appendChild(a); a.click(); a.remove();
    }
    """)

if __name__ == "__main__":
    import sys
    share = "--share" in sys.argv or os.environ.get("SHARE", "").strip() == "1"
    # 비밀번호: 환경변수 APP_PASSWORD 가 있으면 로그인 창이 뜸 (아이디는 crew).
    # Hugging Face Spaces 에서는 Settings → Variables and secrets 에 APP_PASSWORD 를 넣으면 됨.
    pw = os.environ.get("APP_PASSWORD", "").strip()
    if share and not pw:
        print("=" * 60)
        print("동료에게 알려줄 비밀번호를 정해서 입력하고 Enter (비우면 비밀번호 없음)")
        try:
            pw = input("비밀번호: ").strip()
        except EOFError:
            pw = ""
    auth = ("crew", pw) if pw else None
    on_server = bool(os.environ.get("SPACE_ID"))     # Hugging Face Spaces 에서 실행 중인지
    if share:
        print()
        print("잠시 후 'Running on public URL: https://xxxx.gradio.live' 줄이 뜹니다.")
        print("그 링크를 핸드폰이나 동료에게 보내세요. 아이디는 crew, 비밀번호는 방금 정한 것.")
        print("이 창을 닫으면 링크가 끊깁니다. (최대 1주일)")
        print("=" * 60)
    elif not on_server:
        print("웹페이지를 켜는 중입니다. 20~30초 뒤 브라우저가 자동으로 열려요. 이 창은 닫지 마세요.")
    demo.launch(server_name="0.0.0.0", server_port=int(os.environ.get("PORT", "7860")), show_error=True, auth=auth,
                auth_message="공유받은 비밀번호를 입력하세요 (아이디: crew)" if auth else None,
                inbrowser=not on_server and not share,   # 내 컴퓨터에서는 준비 끝나면 브라우저 자동 열기
                share=share)
