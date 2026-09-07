"""
스케줄 스크린샷을 컴퓨터 안에서 직접 읽어(OCR) schedule JSON을 만드는 모듈. Claude 없이 동작.

사용법:
    python read_schedule.py 9월.jpg                 # 9월.schedule.json 생성 후 바로 9월_메모.jpg까지 생성
    python read_schedule.py 9월.jpg --name 소민
    python read_schedule.py 9월.jpg --json-only     # JSON만 만들고 그림은 안 그림

방식:
  1. annotate_schedule.detect_grid 로 달력 칸 위치를 찾는다.
  2. 각 칸 안에서 색깔 띠(파랑=비행, 하늘=레이오버, 초록=휴무, 회색=STBY, 빨강=휴가)를 찾는다.
  3. 칸을 3배로 키워 OCR(RapidOCR, 오프라인)로 글자를 읽고, 각 색깔 띠 아래 글자를 그 블록의 상세(노선·시각)로 붙인다.
  4. annotate_schedule 이 쓰는 JSON 형식으로 조립한다.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# 주의: onnxruntime(OCR 엔진)은 Pillow의 ImageDraw보다 먼저 불러와야 Windows에서 DLL 충돌이 없다.
import onnxruntime  # noqa: F401
import numpy as np
from PIL import Image

import annotate_schedule as A

_OCR = None


def get_ocr():
    """OCR 엔진. 호출하면 [(box(4점), text, conf), ...] 를 돌려주는 함수를 반환.
    새 패키지 rapidocr(파이썬 3.13 지원)를 우선 쓰고, 없으면 예전 rapidocr_onnxruntime."""
    global _OCR
    if _OCR is not None:
        return _OCR
    try:
        from rapidocr import RapidOCR
        eng = RapidOCR()

        def run(img):
            r = eng(img)
            if r is None or r.boxes is None or r.txts is None:
                return []
            return [(np.asarray(b).tolist(), t, float(c)) for b, t, c in zip(r.boxes, r.txts, r.scores)]
    except ImportError:
        from rapidocr_onnxruntime import RapidOCR
        eng = RapidOCR()

        def run(img):
            res, _ = eng(img)
            return [(b, t, float(c)) for b, t, c in (res or [])]
    _OCR = run
    return _OCR


# ──────────────────────────────────────────────────────────────
# 색깔 띠(블록) 찾기
# ──────────────────────────────────────────────────────────────

def classify_color(rgb):
    r, g, b = rgb
    mx, mn = max(rgb), min(rgb)
    if mx - mn < 20 and 150 < mx < 230:
        return "stby"
    if g > r > b and g > 150:
        return "off"
    if r > 200 and g < 170 and b < 170:
        return "yvc"
    if b > r + 60 and b > 180:
        return "flight" if r < 90 else "lo"
    return None


def find_blocks(arr, x0, y0, x1, y1, skip_top):
    """칸 안의 색깔 띠 목록 [(top, bottom, kind, rgb), ...] (좌표는 원본 기준)."""
    region = arr[y0 + skip_top:y1 - 2, x0 + 6:x1 - 6]
    if region.size == 0:
        return []
    mx = region.max(-1)
    mn = region.min(-1)
    colored = ((mx - mn) > 40) | ((mx < 225) & (mx > 150) & ((mx - mn) < 20))  # 유채색 또는 회색 블록
    frac = colored.mean(1)
    rows = frac > 0.6
    blocks = []
    y = 0
    n = len(rows)
    while y < n:
        if rows[y]:
            s = y
            while y < n and rows[y]:
                y += 1
            e = y
            if e - s >= 8:
                band = region[s:e]
                rgb = tuple(int(v) for v in np.median(band.reshape(-1, 3), axis=0))
                kind = classify_color(rgb)
                if kind:
                    blocks.append((y0 + skip_top + s, y0 + skip_top + e, kind, rgb))
        else:
            y += 1
    return blocks


# ──────────────────────────────────────────────────────────────
# OCR + 글자 정리
# ──────────────────────────────────────────────────────────────

def ocr_cell(img, box, scale=3):
    x0, y0, x1, y1 = box
    crop = img.crop((x0, y0, x1, y1)).resize(((x1 - x0) * scale, (y1 - y0) * scale), Image.LANCZOS)
    res = get_ocr()(np.asarray(crop))
    tokens = []
    for poly, txt, conf in res:
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        tokens.append({
            "text": txt.strip(),
            "conf": float(conf),
            "x": x0 + min(xs) / scale,
            "y": y0 + (min(ys) + max(ys)) / 2 / scale,   # 세로 중심 (원본 좌표)
        })
    return tokens


LETTER_FIX = str.maketrans({"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G"})
DIGIT_FIX = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "S": "5", "B": "8", "Z": "2"})

ROUTE_RE = re.compile(r"([A-Z0-9]{3})\s*-\s*([A-Z0-9]{3})")
TIME_RE = re.compile(r"\d{1,2}[:.]\d{2}")
CODE3_RE = re.compile(r"^[A-Z0-9]{3}$")


def fix_code(s):
    return s.upper().translate(LETTER_FIX)


def norm_times(s):
    """시각 부분을 정규화: '-11:35' / '12:55' / '09:15-11:40' → (dep, arr)"""
    s2 = s.translate(DIGIT_FIX).replace(".", ":").replace(" ", "")
    times = TIME_RE.findall(s2)
    if not times:
        return None, None
    if len(times) >= 2:
        return times[0], times[1]
    t = times[0]
    idx = s2.find(t)
    before = s2[:idx]
    if "-" in before:
        return None, t        # '-11:35' : 도착만
    return t, None            # '12:55 -' 또는 '12:55' : 출발만


def parse_detail(kind, text):
    """블록 아래 상세 글자 → JSON 한 줄. 실패하면 None."""
    t = " ".join(text.split())
    if kind == "flight":
        m = ROUTE_RE.search(t.upper())
        if not m:
            return None
        frm, to = fix_code(m.group(1)), fix_code(m.group(2))
        dep, arr = norm_times(t[m.end():])
        return frm, to, dep, arr
    if kind in ("lo", "stby"):
        parts = t.upper().split()
        city = None
        rest = []
        for p in parts:
            if city is None and CODE3_RE.match(p) and not TIME_RE.search(p):
                city = fix_code(p)
            else:
                rest.append(p)
        if city is None:
            return None
        dep, arr = norm_times(" ".join(rest))
        return city, dep, arr
    return None


def fmt_time(dep, arr):
    if dep is None and arr is None:
        return "-"
    return f"{dep or ''}-{arr or ''}"


def read_cell(img, arr, box, date_h):
    """칸 하나 → JSON 줄 목록"""
    x0, y0, x1, y1 = box
    blocks = find_blocks(arr, x0, y0, x1, y1, date_h)
    if not blocks:
        return []
    tokens = ocr_cell(img, box)
    lines = []
    for i, (top, bottom, kind, rgb) in enumerate(blocks):
        next_top = blocks[i + 1][0] if i + 1 < len(blocks) else y1
        # 블록 자체 글자(KE0141 등)와 그 아래 상세 글자
        head_toks = [t for t in tokens if top - 2 <= t["y"] <= bottom + 2]
        det_toks = sorted([t for t in tokens if bottom + 2 < t["y"] < next_top - 1], key=lambda t: (round(t["y"] / 8), t["x"]))
        head = " ".join(t["text"] for t in sorted(head_toks, key=lambda t: t["x"]))
        detail = " ".join(t["text"] for t in det_toks)
        if kind == "flight":
            code = fix_flight_code(head)
            p = parse_detail("flight", detail)
            if p:
                frm, to, dep, arr_ = p
                lines.append(f"{code} {frm}-{to} {fmt_time(dep, arr_)}")
            else:
                lines.append(f"{code} ???-??? -")   # 읽기 실패 표시
        elif kind == "lo":
            p = parse_detail("lo", detail)
            if p:
                city, dep, arr_ = p
                lines.append(f"LO {city} {fmt_time(dep, arr_)}")
            else:
                lines.append("LO ??? -")
        elif kind == "stby":
            p = parse_detail("stby", detail)
            city = p[0] if p else "ICN"
            lines.append(f"STBY {city} 00:00-23:59")
        elif kind == "off":
            code = head.upper().replace("0", "O")
            if code not in A.DAY_OFF_CODES:
                # 블록만 따로 색 반전해서 다시 읽기 (흰 글씨가 잘 안 읽히는 경우)
                code = guess_off_code(ocr_block_text(img, (x0 + 6, top, x1 - 6, bottom)) or code)
            lines.append(code)
        elif kind == "yvc":
            lines.append("YVC")
    return lines


def ocr_block_text(img, box, scale=4):
    """색 블록 안의 흰 글씨를 색 반전 후 크게 키워서 읽는다."""
    from PIL import ImageOps
    x0, y0, x1, y1 = box
    if x1 - x0 < 10 or y1 - y0 < 6:
        return ""
    crop = img.crop((x0, y0, x1, y1)).resize(((x1 - x0) * scale, (y1 - y0) * scale), Image.LANCZOS)
    crop = ImageOps.invert(crop.convert("L")).convert("RGB")
    res = get_ocr()(np.asarray(crop))
    return " ".join(t for _, t, _ in res)


def guess_off_code(s):
    s = re.sub(r"[^A-Z0-9]", "", s.upper()).replace("0", "O").replace("1", "I")
    for c in ("ATDO", "PDO", "ADO"):
        if c in s:
            return c
    if len(s) == 4 and s.startswith("A") and s.endswith("DO"):
        return "ATDO"          # AIDO, ALDO 같은 오독
    if s.startswith("AT"):
        return "ATDO"
    if s.startswith("P"):
        return "PDO"
    return "OFF"   # 글씨를 못 읽었지만 초록 블록이므로 쉬는 날


def fix_flight_code(head):
    m = re.search(r"([A-Z0-9]{2})\s?(\d{3,4}[A-Z]?)", head.upper().replace(" ", ""))
    if not m:
        return "KE0000"
    return fix_code(m.group(1)) + m.group(2).translate(DIGIT_FIX)


def read_header(img, grid):
    """'2026.09' 같은 연·월 읽기"""
    W = img.width
    y0, y1 = max(0, grid["table_line"]), grid["header_line"]
    # 너무 넓은 띠는 OCR이 글자를 놓치므로 왼쪽 일부 → 전체 순으로 시도
    for x1, sc in ((int(W * 0.35), 3), (int(W * 0.6), 3), (W, 2)):
        for t in ocr_cell(img, (0, y0, x1, y1), scale=sc):
            m = re.search(r"(20\d{2})\s*[.\-/]\s*(\d{1,2})", t["text"].translate(DIGIT_FIX))
            if m:
                return int(m.group(1)), int(m.group(2))
    return None, None


# ──────────────────────────────────────────────────────────────

def read_schedule(image_path, name="", year=None, month=None, progress=None):
    import calendar
    img = Image.open(image_path).convert("RGB")
    arr = np.asarray(img).astype(int)
    scale = img.width / 1089.0
    grid = A.detect_grid(img)
    xs, ys = grid["xs"], grid["ys"]
    n_rows = len(ys) - 1
    date_h = int(28 * scale)

    if year is None or month is None:
        y2, m2 = read_header(img, grid)
        year = year or y2
        month = month or m2
    if not year or not month:
        raise RuntimeError("사진에서 연·월(예: 2026.09)을 못 읽었어요. --year --month 로 알려주세요.")

    first_col = (calendar.monthrange(year, month)[0] + 1) % 7
    n_days = calendar.monthrange(year, month)[1]

    days = {}
    for d in range(1, n_days + 1):
        idx = first_col + d - 1
        r, c = divmod(idx, 7)
        if r >= n_rows:
            break
        box = (xs[c], ys[r], xs[c + 1], ys[r + 1])
        if progress:
            progress(d, n_days)
        lines = read_cell(img, arr, box, date_h)
        if lines:
            days[str(d)] = lines
    return {"name": name, "year": year, "month": month, "days": days}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--name", default="소민")
    ap.add_argument("--year", type=int)
    ap.add_argument("--month", type=int)
    ap.add_argument("--json-only", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()

    image_path = Path(args.image)
    spec = read_schedule(image_path, args.name, args.year, args.month,
                         progress=lambda d, n: print(f"\r읽는 중 {d}/{n}일", end="", flush=True))
    print()
    json_path = image_path.with_name(image_path.stem + ".schedule.json")
    json_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {json_path}")
    bad = [(d, l) for d, ls in spec["days"].items() for l in ls if "?" in l]
    if bad:
        print("못 읽은 곳:", bad)
    if args.json_only:
        return
    out_path = Path(args.out) if args.out else image_path.with_name(image_path.stem + "_메모.jpg")
    info = A.annotate(image_path, spec, out_path)
    print(f"저장: {out_path}")
    for t in info["trips"]:
        print(f"  {t['start']:>2}~{t['end']:<2}일  {t['label']}")
    print("큰 숫자:", ", ".join(map(str, info["big_days"])))


if __name__ == "__main__":
    main()
