"""
승무원 월간 스케줄 원본 이미지에 가시성 메모를 자동으로 그려 넣는 스크립트.

사용법:
    python annotate_schedule.py 9월.jpg            # 9월.schedule.json 을 읽어서 9월_메모.jpg 생성
    python annotate_schedule.py 9월.jpg --json 다른파일.json --out 결과.jpg

입력 JSON 형식 (Claude가 원본 사진을 보고 만들어 줌):
{
  "name": "소민",
  "year": 2026,
  "month": 9,
  "days": {
    "1":  ["PDO"],
    "3":  ["KE0141 ICN-XIY 09:15-11:40", "KE0142 XIY-ICN 12:57-16:54"],
    "4":  ["KE2011 ICN-HKG 23:15-"],
    "5":  ["KE2011 ICN-HKG -01:32", "LO HKG -23:25"],
    "19": ["STBY ICN 00:00-23:59"]
  },
  "labels":  {"4": "홍콩 2박 3일"},   # (선택) 자동 라벨 대신 쓸 문구. 키는 여행 시작일
  "big_days_add":    [],              # (선택) 큰 숫자를 추가로 넣을 날
  "big_days_remove": []               # (선택) 큰 숫자를 빼고 싶은 날
}

그리는 것:
  1. 상단 제목  🐬 {이름} {월}월 🐬
  2. 비행 묶음(출발~집 도착)에 노란 형광펜 + 아래에 "도시 N박 M일 / 퀵 / 밤도깨비" 라벨
  3. 쉬는 날(ATDO / ADO / PDO)에 큰 날짜 숫자
"""

import argparse
import calendar
import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ──────────────────────────────────────────────────────────────
# 설정 (필요하면 여기만 고치면 됨)
# ──────────────────────────────────────────────────────────────

HOME_AIRPORTS = {"ICN", "GMP"}          # 집(베이스) 공항. 여기서 나가면 여행 시작, 여기 도착하면 여행 끝
DAY_OFF_CODES = {"ATDO", "ADO", "PDO", "OFF"}  # 큰 숫자를 넣을 쉬는 날 코드 (YVC 휴가는 제외). OFF = 글씨는 못 읽었지만 초록 블록

# 공항 코드 → 메모에 쓸 한글 이름 (8월 메모에서 쓰던 별명 우선)
CITY_NAMES = {
    # 국내
    "PUS": "부산", "CJU": "제주", "TAE": "대구", "KWJ": "광주", "USN": "울산", "RSU": "여수",
    # 일본
    "FUK": "후쿠오카", "NRT": "나리타", "HND": "하네다", "KIX": "오사카", "NGO": "나고야",
    "CTS": "삿포로", "OKA": "오키나와", "KOJ": "가고시마", "KMJ": "구마모토", "OIT": "오이타",
    "HIJ": "히로시마", "OKJ": "오카야마", "KIJ": "니가타", "AOJ": "아오모리", "AXT": "아키타",
    "KMQ": "고마쓰", "NGS": "나가사키",
    # 중국·대만·홍콩
    "PEK": "베이징", "PKX": "베이징", "PVG": "상하이", "CAN": "광저우", "SZX": "선전",
    "XIY": "시안", "DYG": "장자제", "HKG": "홍콩", "TPE": "타이베이", "TSN": "톈진",
    "DLC": "다롄", "SHE": "선양", "CGQ": "창춘", "HRB": "하얼빈", "TAO": "칭다오",
    "WEH": "웨이하이", "YNT": "옌타이", "NKG": "난징", "HGH": "항저우", "WUH": "우한",
    "CTU": "청두", "TFU": "청두", "KMG": "쿤밍", "CKG": "충칭", "XMN": "샤먼", "CSX": "창사",
    "HFE": "허페이", "NGB": "닝보", "URC": "우루무치", "MFM": "마카오", "HAK": "하이커우",
    "SYX": "싼야", "YNJ": "옌지", "MDG": "무단장", "ZHY": "중웨이",
    # 동남아·남아시아
    "SGN": "사이공", "HAN": "하노이", "DAD": "다낭", "CXR": "나트랑", "BKK": "방콕",
    "HKT": "푸켓", "CNX": "치앙마이", "KUL": "쿠알라", "SIN": "싱가폴", "MNL": "마닐라",
    "CEB": "세부", "CRK": "클락", "KLO": "칼리보", "DPS": "발리", "CGK": "자카르타",
    "PNH": "프놈펜", "REP": "씨엠립", "RGN": "양곤", "KTM": "카트만두", "CMB": "콜롬보",
    "MLE": "몰디브", "DEL": "델리", "BOM": "뭄바이", "UBN": "울란바토르", "ULN": "울란바토르",
    # 중동·유럽
    "DXB": "두바이", "DOH": "도하", "TLV": "텔아비브", "IST": "이스탄불",
    "AMS": "암스", "LHR": "런던", "CDG": "파리", "FRA": "프랑크", "FCO": "로마",
    "MXP": "밀라노", "MAD": "마드리드", "BCN": "바르셀로나", "VIE": "비엔나",
    "ZRH": "취리히", "PRG": "프라하", "BUD": "부다페스트", "ZAG": "자그레브",
    "LIS": "리스본", "SVO": "모스크바", "VVO": "블라디", "OSL": "오슬로",
    # 미주
    "ATL": "애틀랜타", "JFK": "뉴욕", "EWR": "뉴욕", "BOS": "보스턴", "IAD": "워싱턴",
    "ORD": "시카고", "DTW": "디트로이트", "DFW": "댈러스", "IAH": "휴스턴",
    "LAX": "LA", "SFO": "샌프란", "SEA": "시애틀", "LAS": "라스베가스", "HNL": "하와이",
    "YVR": "밴쿠버", "YYZ": "토론토", "ANC": "앵커리지", "MIA": "마이애미",
    # 대양주
    "SYD": "시드니", "BNE": "브리즈번", "AKL": "오클랜드", "GUM": "괌", "SPN": "사이판",
    "NAN": "피지", "MEL": "멜번",
}

# 폰트: 같이 들어있는 assets/fonts (나눔고딕, OFL) 를 우선 쓰고, 없으면 Windows 맑은 고딕
ASSETS = Path(__file__).resolve().parent / "assets"
_WIN_FONTS = Path("C:/Windows/Fonts")
FONT_KR = ASSETS / "fonts" / "NanumGothic-Regular.ttf"
FONT_KR_BOLD = ASSETS / "fonts" / "NanumGothic-Bold.ttf"
if not FONT_KR.exists():
    FONT_KR = _WIN_FONTS / "malgun.ttf"
    FONT_KR_BOLD = _WIN_FONTS / "malgunbd.ttf"
DOLPHIN_PNG = ASSETS / "dolphin.png"        # 🐬 (Noto Emoji, Apache 2.0)

HIGHLIGHT_RGBA = (255, 228, 110, 105)   # 노란 형광펜
LABEL_COLOR = (25, 25, 25)
BIG_NUMBER_COLOR = (20, 20, 20)
TITLE_COLOR = (20, 20, 20)


# ──────────────────────────────────────────────────────────────
# 1. 스케줄 항목 파싱
# ──────────────────────────────────────────────────────────────

FLIGHT_RE = re.compile(
    r"^(?P<code>[A-Z]{2}\d{3,4}[A-Z]?)\s+(?P<frm>[A-Z]{3})\s*-\s*(?P<to>[A-Z]{3})"
    r"(?:\s+(?P<dep>\d{1,2}:\d{2})?\s*-\s*(?P<arr>\d{1,2}:\d{2})?)?\s*$"
)
LO_RE = re.compile(
    r"^LO\s+(?P<city>[A-Z]{3})(?:\s+(?P<dep>\d{1,2}:\d{2})?\s*-\s*(?P<arr>\d{1,2}:\d{2})?)?\s*$"
)
STBY_RE = re.compile(r"^STBY\b(?:\s+(?P<city>[A-Z]{3}))?.*$")


def parse_entry(text: str) -> dict:
    """셀 한 줄('KE0141 ICN-XIY 09:15-11:40', 'LO HKG -23:25', 'ATDO' ...)을 딕셔너리로."""
    t = " ".join(text.strip().split())
    m = FLIGHT_RE.match(t)
    if m:
        return {
            "kind": "flight",
            "code": m["code"],
            "from": m["frm"],
            "to": m["to"],
            "dep": m["dep"],   # 이 날 출발 시각 (없으면 전날 출발한 비행의 도착만 있는 것)
            "arr": m["arr"],   # 이 날 도착 시각 (없으면 다음 날 도착)
        }
    m = LO_RE.match(t)
    if m:
        return {"kind": "lo", "city": m["city"], "dep": m["dep"], "arr": m["arr"]}
    m = STBY_RE.match(t)
    if m:
        return {"kind": "stby", "city": m["city"]}
    head = t.split()[0].upper() if t else ""
    if head in DAY_OFF_CODES:
        return {"kind": "off", "code": head}
    return {"kind": "other", "code": head, "raw": t}


# ──────────────────────────────────────────────────────────────
# 2. 여행(비행 묶음) 찾기 + 라벨 만들기
# ──────────────────────────────────────────────────────────────

def find_trips(days: dict) -> list:
    """
    days: {day(int): [entry dict, ...]}
    돌려주는 값: [{"start": 3, "end": 3, "cities": [...], "has_lo": bool}, ...]
    규칙: 집(ICN/GMP)에서 출발하면 여행 시작, 집에 도착(도착 시각이 있는 날)하면 여행 끝.
    """
    trips = []
    cur = None
    for day in sorted(days):
        for e in days[day]:
            if e["kind"] == "flight":
                if e["from"] == e["to"]:
                    # 회항(ICN-ICN 같은 것). 여행 경계로 취급하지 않고 그 날만 포함
                    if cur is not None:
                        cur["end"] = day
                    continue
                departs_today = e["dep"] is not None
                arrives_today = e["arr"] is not None
                if cur is None:
                    if departs_today and e["from"] in HOME_AIRPORTS:
                        cur = {"start": day, "end": day, "cities": [], "lo_cities": []}
                    else:
                        continue  # 전달에 시작한 여행의 도착편 등 → 무시
                cur["end"] = day
                if e["to"] not in HOME_AIRPORTS:
                    cur["cities"].append(e["to"])
                if arrives_today and e["to"] in HOME_AIRPORTS:
                    trips.append(cur)
                    cur = None
            elif e["kind"] == "lo":
                if cur is None:
                    # 전달부터 이어진 레이오버: 이 달에 출발이 없으니 표시하지 않음
                    continue
                cur["end"] = day
                cur["lo_cities"].append(e["city"])
    if cur is not None:
        trips.append(cur)  # 달 말에 나가서 다음 달에 돌아오는 경우

    # 같은 날에 끝나고 다시 시작하는 여행은 하나로 합침 (회항 후 재출발 등)
    merged = []
    for t in trips:
        if merged and merged[-1]["end"] == t["start"]:
            merged[-1]["end"] = t["end"]
            merged[-1]["cities"] += t["cities"]
            merged[-1]["lo_cities"] += t["lo_cities"]
        else:
            merged.append(t)
    return merged


def city_name(code: str) -> str:
    return CITY_NAMES.get(code, code)


def trip_label(trip: dict) -> str:
    if trip["lo_cities"]:
        # 가장 많이 머문 도시
        city = max(set(trip["lo_cities"]), key=trip["lo_cities"].count)
    elif trip["cities"]:
        city = trip["cities"][0]
    else:
        city = "?"
    n_days = trip["end"] - trip["start"] + 1
    name = city_name(city)
    if n_days == 1:
        return f"{name} 퀵"
    if n_days == 2 and not trip["lo_cities"]:
        return f"{name} 밤도깨비"
    return f"{name} {n_days - 1}박 {n_days}일"


# ──────────────────────────────────────────────────────────────
# 3. 달력 격자 자동 감지
# ──────────────────────────────────────────────────────────────

def _group_lines(indices, gap=3):
    groups = []
    for i in indices:
        if groups and i - groups[-1][-1] <= gap:
            groups[-1].append(i)
        else:
            groups.append([i])
    return [(g[0] + g[-1]) / 2 for g in groups]


def detect_grid(img: Image.Image) -> dict:
    a = np.asarray(img.convert("RGB")).astype(int)
    mx = a.max(-1)
    mn = a.min(-1)
    light_line = (mx - mn < 25) & (mx < 242) & (mx > 120)   # 연회색 칸 테두리
    dark_line = (mx - mn < 30) & (mx < 200)                 # 진한 구분선

    h, w = mx.shape

    # 세로선: 화면 세로 전체 기준 비율이 높은 열
    col_ratio = light_line.mean(0)
    xs = _group_lines([x for x in range(w) if col_ratio[x] > 0.25])
    if len(xs) < 7:
        raise RuntimeError(f"세로 칸 선을 못 찾았어요 ({len(xs)}개). 원본 스크린샷이 맞는지 확인해 주세요.")
    # 8개가 아니면 등간격으로 보정
    if len(xs) > 8:
        # 가장 등간격인 8개 연속 조합 선택
        best = None
        for i in range(len(xs) - 7):
            cand = xs[i:i + 8]
            gaps = np.diff(cand)
            score = gaps.std()
            if best is None or score < best[0]:
                best = (score, cand)
        xs = list(best[1])
    if len(xs) == 7:
        step = (xs[-1] - xs[0]) / 6
        xs.append(xs[-1] + step)
    xs = [int(round(x)) for x in xs]

    # 세로선이 실제로 이어진 y 범위 → 달력 몸통 위/아래 (여러 세로선을 보고 가장 긴 구간 채택)
    def longest_run(mask_col):
        ys_on = np.where(mask_col)[0]
        best = (0, 0)
        start = prev = None
        for y in ys_on:
            if start is None:
                start = y
            elif y - prev > 6:
                if prev - start > best[1] - best[0]:
                    best = (start, prev)
                start = y
            prev = y
        if start is not None and prev - start > best[1] - best[0]:
            best = (start, prev)
        return best

    # 세로선 6개 중 2개 이상이 보이는 y만 인정 (형광펜 등으로 일부 선이 가려져도 버팀)
    presence = sum(light_line[:, x - 1:x + 2].any(1).astype(int) for x in xs[1:-1])
    body_top, body_bottom = longest_run(presence >= 2)

    # 가로선: 달력 가로 범위 거의 전체(70% 이상)에 걸친 연회색 행만. 줄 높이는 달마다 달라도 됨.
    row_ratio = light_line[:, xs[0]:xs[-1]].mean(1)
    ys = _group_lines([y for y in range(body_top - 4, body_bottom + 5) if 0 <= y < h and row_ratio[y] > 0.7])
    ys = [int(round(y)) for y in ys]
    cleaned = []
    for y in ys:
        if not cleaned or y - cleaned[-1] > 30:
            cleaned.append(y)
    ys = cleaned
    # 맨 위/아래 테두리는 더 연해서 안 잡힐 수 있음 → 세로선이 시작/끝나는 지점으로 보충
    if not ys or ys[0] - body_top > 10:
        ys.insert(0, int(body_top))
    if body_bottom - ys[-1] > 10:
        ys.append(int(body_bottom))
    if len(ys) < 5:
        raise RuntimeError(f"가로 칸 선을 못 찾았어요 ({len(ys)}개).")

    # 요일 헤더 밑 진한 선 (제목 위치 계산용)
    dark_ratio = dark_line[:, xs[0]:xs[-1]].mean(1)
    dark_rows = [y for y in range(0, ys[0]) if dark_ratio[y] > 0.5]
    header_line = max(dark_rows) if dark_rows else ys[0] - 40
    # 그 위의 가장 아래 연회색 선 (Actual 표 밑줄)
    light_rows = [y for y in range(0, header_line - 5) if light_line[y, xs[0]:int(xs[0] + (xs[-1] - xs[0]) * 0.45)].mean() > 0.5]
    table_line = max(light_rows) if light_rows else max(0, header_line - 90)

    return {
        "xs": xs, "ys": ys,
        "header_line": header_line, "table_line": table_line,
    }


def content_bottom(img_arr, x0, y0, x1, y1, skip_top):
    """셀 안에서 (날짜 숫자 아래로) 내용이 그려진 가장 아래 y. 없으면 None."""
    region = img_arr[y0 + skip_top:y1 - 2, x0 + 4:x1 - 4]
    nonwhite = (region.min(-1) < 225).mean(1) > 0.03
    rows = np.where(nonwhite)[0]
    if len(rows) == 0:
        return None
    return y0 + skip_top + int(rows[-1])


# ──────────────────────────────────────────────────────────────
# 4. 그리기
# ──────────────────────────────────────────────────────────────

def load_font(path, size):
    return ImageFont.truetype(str(path), int(size))


def text_size(draw, text, font):
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    return r - l, b - t


def annotate(image_path: Path, spec: dict, out_path: Path) -> dict:
    img = Image.open(image_path).convert("RGBA")
    W, H = img.size
    scale = W / 1089.0                     # 기준 이미지(1089px) 대비 배율
    grid = detect_grid(img)
    xs, ys = grid["xs"], grid["ys"]
    n_rows = len(ys) - 1

    year, month = int(spec["year"]), int(spec["month"])
    first_wd = calendar.monthrange(year, month)[0]    # 월요일=0
    first_col = (first_wd + 1) % 7                    # 일요일=0 으로 변환
    n_days_in_month = calendar.monthrange(year, month)[1]

    def cell_of(day: int):
        idx = first_col + day - 1
        r, c = divmod(idx, 7)
        if r >= n_rows:
            return None
        return r, c

    def cell_box(day: int):
        rc = cell_of(day)
        if rc is None:
            return None
        r, c = rc
        return xs[c], ys[r], xs[c + 1], ys[r + 1]

    # 항목 파싱
    days = {}
    for k, items in spec.get("days", {}).items():
        d = int(k)
        days[d] = [parse_entry(s) for s in items]

    trips = find_trips(days)
    labels_override = {int(k): v for k, v in spec.get("labels", {}).items()}
    for t in trips:
        t["label"] = labels_override.get(t["start"]) or trip_label(t)

    big_days = {d for d, items in days.items() if any(e["kind"] == "off" for e in items)}
    big_days |= {int(d) for d in spec.get("big_days_add", [])}
    big_days -= {int(d) for d in spec.get("big_days_remove", [])}

    arr = np.asarray(img.convert("RGB")).astype(int)
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    draw = ImageDraw.Draw(img)

    date_h = int(28 * scale)          # 칸 위쪽 날짜 숫자 영역 높이
    pad = int(4 * scale)

    # ── 형광펜 + 라벨 (주가 바뀌면 줄마다 따로 칠하고, 라벨도 줄마다 넣음)
    label_font = load_font(FONT_KR, 23 * scale)
    pending_labels = []
    for t in trips:
        seg_start = t["start"]
        while seg_start <= min(t["end"], n_days_in_month):
            rc = cell_of(seg_start)
            if rc is None:
                break
            r, c = rc
            seg_end = min(t["end"], seg_start + (6 - c), n_days_in_month)
            boxes = [cell_box(d) for d in range(seg_start, seg_end + 1)]
            boxes = [b for b in boxes if b]
            if not boxes:
                break
            x0 = boxes[0][0] + pad
            x1 = boxes[-1][2] - pad
            y0 = boxes[0][1] + date_h
            row_bottom = boxes[0][3]
            bottoms = [content_bottom(arr, *b, skip_top=date_h) for b in boxes]
            bottoms = [b for b in bottoms if b is not None]
            y1 = (max(bottoms) + int(6 * scale)) if bottoms else row_bottom - pad
            y1 = min(y1, row_bottom - pad)
            od.rectangle([x0, y0, x1, y1], fill=HIGHLIGHT_RGBA)

            # 라벨 위치: 형광펜 바로 아래, 칸 가운데
            tw, th = text_size(draw, t["label"], label_font)
            ty = y1 + int(3 * scale)
            if ty + th > row_bottom - int(2 * scale):
                ty = row_bottom - th - int(2 * scale)
            tx = (x0 + x1) / 2 - tw / 2
            tx = max(xs[0] + pad, min(tx, xs[-1] - tw - pad))
            pending_labels.append((int(tx), int(ty), t["label"]))
            seg_start = seg_end + 1

    for tx, ty, label in pending_labels:
        draw.text((tx, ty), label, font=label_font, fill=LABEL_COLOR,
                  stroke_width=int(3 * scale), stroke_fill=(255, 255, 255))

    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    # ── 쉬는 날 큰 숫자
    for d in sorted(big_days):
        box = cell_box(d)
        if not box:
            continue
        x0, y0, x1, y1 = box
        cb = content_bottom(arr, x0, y0, x1, y1, skip_top=date_h)
        top = (cb + int(6 * scale)) if cb else y0 + date_h
        avail_h = y1 - top
        size = max(30 * scale, min(66 * scale, avail_h * 0.62))
        f = load_font(FONT_KR, size)
        s = str(d)
        tw, th = text_size(draw, s, f)
        l, tt, r, b = draw.textbbox((0, 0), s, font=f)
        cx = (x0 + x1) / 2 - tw / 2 - l
        cy = top + avail_h / 2 - th / 2 - tt
        draw.text((int(cx), int(cy)), s, font=f, fill=BIG_NUMBER_COLOR)

    # ── 제목  🐬 이름 N월 🐬
    name = (spec.get("name", "") or "").strip()
    if spec.get("title"):
        title = spec["title"].strip()                 # 제목을 직접 정한 경우 그대로
    elif "월" in name:
        title = name                                  # '9월', '소민 9월' 처럼 이미 월이 들어 있으면 그대로
    else:
        title = f"{name} {month}월".strip()           # '소민' → '소민 9월', 비어 있으면 '9월'
    title_font = load_font(FONT_KR_BOLD, 50 * scale)
    tw, th = text_size(draw, title, title_font)
    esize = int(46 * scale)
    use_dolphin = bool(spec.get("dolphin", False))   # 기본은 글자만. 돌고래를 넣으려면 JSON에 "dolphin": true
    dolphin = Image.open(DOLPHIN_PNG).convert("RGBA").resize((esize, esize), Image.LANCZOS) if (use_dolphin and DOLPHIN_PNG.exists()) else None
    ew = esize if dolphin else 0
    gap = int(14 * scale)
    total_w = ew + gap + tw + gap + ew if dolphin else tw
    cy = (grid["table_line"] + grid["header_line"]) / 2
    x = int(W / 2 - total_w / 2)
    l, tt, r, b = draw.textbbox((0, 0), title, font=title_font)
    if dolphin:
        img.paste(dolphin, (x, int(cy - esize / 2)), dolphin)
        img.paste(dolphin, (x + ew + gap + tw + gap, int(cy - esize / 2)), dolphin)
    draw.text((int(x + (ew + gap if dolphin else 0)), int(cy - th / 2 - tt)), title, font=title_font, fill=TITLE_COLOR)

    out = img.convert("RGB")

    # ── 달력 아래 큰 여백 잘라내기 (달력 밑에 다른 내용이 있으면 그것까지 남김)
    if spec.get("trim_bottom", True):
        a2 = np.asarray(out).astype(int)
        below = a2[ys[-1]:, :, :].min(-1) < 235          # 달력 아래에서 흰색이 아닌 픽셀
        rows = np.where(below.mean(1) > 0.002)[0]
        last = ys[-1] + (int(rows[-1]) if len(rows) else 0)
        cut = min(H, last + int(24 * scale))
        if cut < H - int(10 * scale):
            out = out.crop((0, 0, W, cut))

    out.save(out_path, quality=95)
    return {"trips": trips, "big_days": sorted(big_days), "grid": grid}


# ──────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", help="원본 스케줄 이미지 (예: 9월.jpg)")
    ap.add_argument("--json", help="스케줄 JSON (기본: <이미지이름>.schedule.json)")
    ap.add_argument("--out", help="출력 파일 (기본: <이미지이름>_메모.jpg)")
    args = ap.parse_args()

    image_path = Path(args.image)
    json_path = Path(args.json) if args.json else image_path.with_name(image_path.stem + ".schedule.json")
    out_path = Path(args.out) if args.out else image_path.with_name(image_path.stem + "_메모.jpg")

    if not json_path.exists():
        sys.exit(f"스케줄 JSON이 없어요: {json_path}")
    spec = json.loads(json_path.read_text(encoding="utf-8"))
    info = annotate(image_path, spec, out_path)

    print(f"저장: {out_path}")
    print("여행:")
    for t in info["trips"]:
        print(f"  {t['start']:>2}~{t['end']:<2}일  {t['label']}")
    print("큰 숫자 넣은 날:", ", ".join(map(str, info["big_days"])))


if __name__ == "__main__":
    main()
