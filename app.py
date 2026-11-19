import os
import re
import json
import uuid
import socket
import sqlite3
import datetime
import traceback
import threading
import subprocess
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import requests

MOBILE_TUNNEL_URL = "https://yoojonglee15.github.io/kidsnote-helper/"


def start_mobile_tunnel(port=8080):
    """Cloudflare Tunnel(cloudflared)을 이용해 주소가 바뀌지 않고 어디서든(5G/LTE/와이파이) 접속 가능한 모바일 HTTPS 주소를 생성합니다."""
    global MOBILE_TUNNEL_URL
    cf_path = os.path.join(BASE_DIR, "data", "cloudflared.exe")
    while True:
        try:
            if os.path.exists(cf_path):
                cmd = [cf_path, "tunnel", "--url", f"http://127.0.0.1:{port}"]
            else:
                cmd = [
                    "ssh",
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "ServerAliveInterval=30",
                    "-R", f"80:127.0.0.1:{port}",
                    "nokey@localhost.run",
                ]
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            for line in proc.stdout:
                m = re.search(r"(https://[a-zA-Z0-9\-]+\.trycloudflare\.com|https://[a-zA-Z0-9\-]+\.(?:app\.)?lhr\.life)", line)
                if m:
                    MOBILE_TUNNEL_URL = m.group(1)
                    print(f" [모바일 외부 접속 주소 생성 완료]: {MOBILE_TUNNEL_URL}")
            proc.wait()
        except Exception:
            pass
        import time
        time.sleep(5)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
STATIC_DIR = os.path.join(BASE_DIR, "static")
DB_PATH = os.path.join(DATA_DIR, "kidsnote.db")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT UNIQUE,
            source_type TEXT,
            title TEXT,
            content TEXT,
            post_date TEXT,
            summary TEXT,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS checklist_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER,
            target_date TEXT,
            event_title TEXT,
            category TEXT,
            item_text TEXT,
            is_checked INTEGER DEFAULT 0,
            created_at TEXT,
            FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()


def get_setting(key, default=""):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_db()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


WEEKDAYS_KR = ["월", "화", "수", "목", "금", "토", "일"]


def format_date_with_weekday(date_str):
    try:
        dt = datetime.date.fromisoformat(date_str)
        return f"{dt.strftime('%Y-%m-%d')} ({WEEKDAYS_KR[dt.weekday()]})"
    except Exception:
        return date_str


def analyze_post_with_gemini(source_type, title, content, post_date_str):
    """
    Google Gemini API를 호출하여 알림장/공지사항 본문에서
    부모님이 챙겨야 할 준비물, 복장, 해야 할 일, 주요 일정을 날짜별로 추출합니다.
    """
    api_key = get_setting("gemini_api_key", "").strip()
    if not post_date_str:
        post_date_str = datetime.date.today().isoformat()

    try:
        base_dt = datetime.date.fromisoformat(post_date_str[:10])
    except Exception:
        base_dt = datetime.date.today()
    base_date_formatted = f"{base_dt.isoformat()} ({WEEKDAYS_KR[base_dt.weekday()]}요일)"

    # 다가오는 14일 날짜 매핑 참고표 생성 (AI가 '이번주 금요일', '다음주 월요일' 등을 정확히 계산하도록 제공)
    upcoming_days_hint = []
    for i in range(0, 21):
        d = base_dt + datetime.timedelta(days=i)
        upcoming_days_hint.append(f"{d.isoformat()} = {d.month}월 {d.day}일 ({WEEKDAYS_KR[d.weekday()]}요일)")
    calendar_reference = "\n".join(upcoming_days_hint)

    prompt = f"""당신은 어린이집/유치원 학부모를 도와주는 '키즈노트 준비물·일정 정리 비서'입니다.
아래 [{source_type}] 글을 읽고, 부모님이 꼭 챙겨야 하는 **준비물**, **복장(입혀 보낼 옷/신발)**, **해야 할 일(동의서 회신, 투약의뢰서 등)**, **주요 행사/일정**만 날짜별로 정확하게 추출하세요.

[기준 작성일]: {base_date_formatted}
[날짜 계산 참고표]:
{calendar_reference}

[작성 규칙]:
1. 일상적인 인사말이나 단순 활동 보고("오늘 밥을 잘 먹었습니다")는 체크리스트에서 제외하고, 부모님이 준비하거나 기억해야 할 사항만 추출하세요.
2. 글에 '금요일', '내일', '다음 주 월요일', '11/22' 같은 표현이 있으면 위의 [날짜 계산 참고표]를 기준으로 정확한 `YYYY-MM-DD` 날짜로 변환하세요. 특정 날짜가 명시되지 않고 "이번 주까지" 또는 즉시 챙겨야 하는 항목이면 기준 작성일 다음 날 날짜로 지정하세요.
3. category(분류)는 반드시 다음 4가지 중 하나로만 지정하세요:
   - "준비물": 가방에 넣어 보내거나 챙겨야 할 물건 (예: 개인 물통, 낮잠이불, 여벌옷, 도시락, 우산 등)
   - "복장": 당일 아이에게 입혀 보내야 할 옷이나 신발 (예: 원복, 체육복, 편한 운동화, 따뜻한 외투 등)
   - "해야할일": 부모님이 작성/서명/신청해야 할 일 (예: 현장학습 동의서 서명, 투약의뢰서 작성, 회신하기 등)
   - "행사": 기억해두어야 할 원내 행사나 일정 (예: 숲체험 활동, 생일파티, 학부모 상담, 사진 촬영 등)
4. 반드시 아래 JSON 형식으로만 응답하세요 (마크다운 코드블록 없이 순수 JSON 권장):

{{
  "summary": "글 전체 핵심 1~2줄 요약 (부모님이 알아야 할 핵심 위주)",
  "items": [
    {{
      "target_date": "YYYY-MM-DD",
      "event_title": "해당 날짜의 행사명 또는 일정 제목 (예: 숲체험 활동, 등원 준비물)",
      "category": "준비물 | 복장 | 해야할일 | 행사",
      "item_text": "구체적으로 챙겨야 할 항목 (예: 개인 물통 지참, 원복 체육복 착용)"
    }}
  ]
}}

---
[글 제목]: {title}
[글 본문]:
{content}
"""

    if api_key:
        models_to_try = [
            "gemini-3-flash-preview",
            "gemini-3.1-flash-lite-preview",
            "gemini-2.5-flash-lite",
            "gemini-flash-latest",
        ]
        last_err = None
        for model_name in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.1,
                    "responseMimeType": "application/json",
                },
            }
            try:
                resp = requests.post(url, json=payload, timeout=25)
                if resp.status_code == 200:
                    data = resp.json()
                    text_out = (
                        data.get("candidates", [{}])[0]
                        .get("content", {})
                        .get("parts", [{}])[0]
                        .get("text", "")
                    )
                    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text_out.strip())
                    parsed = json.loads(cleaned)
                    if isinstance(parsed, dict) and "items" in parsed:
                        return parsed
                else:
                    last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            except Exception as e:
                last_err = str(e)

    # API 키가 없거나 호출 실패 시 스마트 규칙 기반 백업 추출기 작동
    return fallback_rule_based_analyzer(title, content, base_dt)


def fallback_rule_based_analyzer(title, content, base_dt):
    """API 키 입력 전이거나 오프라인일 때도 즉시 동작하는 한국어 키워드 기반 추출기"""
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    items = []
    current_date = (base_dt + datetime.timedelta(days=1)).isoformat()
    current_event = title or "어린이집/유치원 안내"

    for line in lines:
        # 날짜 패턴 감지 (예: 11월 22일, 11/22)
        m_date = re.search(r"(\d{1,2})\s*[월/.\-]\s*(\d{1,2})\s*일?", line)
        if m_date:
            month, day = int(m_date.group(1)), int(m_date.group(2))
            if 1 <= month <= 12 and 1 <= day <= 31:
                try:
                    year = base_dt.year
                    dt = datetime.date(year, month, day)
                    current_date = dt.isoformat()
                except Exception:
                    pass

        if any(k in line for k in ["숲체험", "현장학습", "소풍", "생일", "견학", "체육", "행사", "상담"]):
            for k in ["숲체험", "현장학습", "소풍", "생일파티", "견학", "체육활동", "학부모 상담"]:
                if k in line:
                    current_event = k
                    break

        if any(k in line for k in ["준비물", "지참", "챙겨", "보내주", "가방에", "물통", "이불", "도시락", "여벌옷", "우산"]):
            items.append({
                "target_date": current_date,
                "event_title": current_event,
                "category": "준비물",
                "item_text": line[:80],
            })
        elif any(k in line for k in ["복장", "입혀", "착용", "체육복", "원복", "운동화"]):
            items.append({
                "target_date": current_date,
                "event_title": current_event,
                "category": "복장",
                "item_text": line[:80],
            })
        elif any(k in line for k in ["동의서", "회신", "서명", "투약", "신청"]):
            items.append({
                "target_date": current_date,
                "event_title": current_event,
                "category": "해야할일",
                "item_text": line[:80],
            })

    summary = lines[0][:100] if lines else "공지사항/알림장 내용입니다."
    return {"summary": summary, "items": items}


def save_post_and_analyze(source_type, title, content, post_date="", external_id=""):
    if not post_date:
        post_date = datetime.date.today().isoformat()
    if not external_id:
        external_id = f"manual_{uuid.uuid4().hex[:12]}"

    conn = get_db()
    existing = conn.execute("SELECT id FROM posts WHERE external_id = ?", (external_id,)).fetchone()
    if existing:
        conn.close()
        return {"already_exists": True, "post_id": existing["id"]}

    analysis = analyze_post_with_gemini(source_type, title, content, post_date)
    summary = analysis.get("summary", "")
    extracted_items = analysis.get("items", [])

    now_str = datetime.datetime.now().isoformat(timespec="seconds")
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO posts (external_id, source_type, title, content, post_date, summary, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (external_id, source_type, title, content, post_date, summary, now_str),
    )
    post_id = cur.lastrowid

    for item in extracted_items:
        t_date = item.get("target_date") or post_date
        ev_title = item.get("event_title") or title or "어린이집 일정"
        cat = item.get("category") or "준비물"
        if cat not in ("준비물", "복장", "해야할일", "행사"):
            cat = "준비물"
        text = (item.get("item_text") or "").strip()
        if text:
            cur.execute(
                """
                INSERT INTO checklist_items (post_id, target_date, event_title, category, item_text, is_checked, created_at)
                VALUES (?, ?, ?, ?, ?, 0, ?)
                """,
                (post_id, t_date, ev_title, cat, text, now_str),
            )

    conn.commit()
    conn.close()
    return {
        "already_exists": False,
        "post_id": post_id,
        "summary": summary,
        "items_count": len(extracted_items),
    }


def fetch_from_kidsnote_playwright(username, password):
    """
    Playwright(크롬 브라우저)를 사용하여 키즈노트 웹사이트(kidsnote.com)에 로그인하고
    최신 알림장 및 공지사항을 자동으로 수집합니다.
    (최초 로그인 시 2단계 인증이 뜨면 열린 크롬 창에서 인증할 수 있도록 화면을 띄우고,
    로그인 세션을 data/browser_profile 에 유지하여 다음부터는 자동 로그인됩니다.)
    """
    from playwright.sync_api import sync_playwright

    collected_posts = []
    api_payloads = []
    profile_dir = os.path.join(DATA_DIR, "browser_profile")
    os.makedirs(profile_dir, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            channel="chrome",
            headless=True,
            viewport={"width": 1280, "height": 860},
            locale="ko-KR",
        )
        page = context.pages[0] if context.pages else context.new_page()

        def handle_response(response):
            try:
                url = response.url
                if ("kidsnote.com" in url or "kidsnote.net" in url) and "json" in response.headers.get("content-type", ""):
                    if any(k in url for k in ["report", "notice", "post", "board", "story", "feed", "v1", "v2", "v3"]):
                        data = response.json()
                        api_payloads.append((url, data))
            except Exception:
                pass

        page.on("response", handle_response)

        page.goto("https://www.kidsnote.com/login", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        # 아직 로그인 페이지라면 아이디/비밀번호 자동 입력
        if "/login" in page.url:
            try:
                page.wait_for_selector('input[name="username"]', timeout=10000)
                page.fill('input[name="username"]', username)
                page.fill('input[name="password"]', password)

                # 로그인 상태 유지 체크
                rem = page.query_selector('input[name="remember_me"]')
                if rem and not rem.is_checked():
                    try:
                        rem.check(force=True)
                    except Exception:
                        pass

                submit_btn = page.query_selector('button[type="submit"]')
                if submit_btn:
                    submit_btn.click()
                else:
                    page.press('input[name="password"]', "Enter")
            except Exception:
                pass

            # 로그인 처리 대기 (2단계 인증이 뜰 경우 사용자가 입력할 수 있도록 최대 90초 대기)
            for _ in range(45):
                page.wait_for_timeout(2000)
                cur_url = page.url
                body_txt = ""
                try:
                    body_txt = page.inner_text("body")
                except Exception:
                    pass

                if "일치하지 않습니다" in body_txt:
                    context.close()
                    raise ValueError("키즈노트 아이디 또는 비밀번호가 일치하지 않습니다. 다시 확인해 주세요.")

                # 로그인 페이지에서 벗어났거나 로그아웃/알림장 메뉴가 보이면 로그인 성공!
                if "/login" not in cur_url and "/two-factor-auth" not in cur_url:
                    break
                if any(m in body_txt for m in ["로그아웃", "알림장", "공지사항"]):
                    break
            else:
                context.close()
                raise ValueError("키즈노트 로그인 시간이 초과되었습니다. (2단계 인증 창이 떴다면 인증을 완료해 주세요)")

        page.wait_for_timeout(3000)

        # 공지사항 및 알림장 페이지로 직접 이동하여 최신 글 목록 불러오기
        for target_url in ["https://www.kidsnote.com/service/notice", "https://www.kidsnote.com/service/report"]:
            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2500)
            except Exception:
                pass

        # 1) 네트워크 API JSON 응답에서 알림장/공지사항 본문 추출
        seen_ext_ids = set()
        for url, payload in api_payloads:
            if "notices" not in url and "reports" not in url:
                continue
            src_type = "공지사항" if "notice" in url else "알림장"
            results = []
            if isinstance(payload, dict):
                results = payload.get("results") or payload.get("data") or payload.get("items") or []
            elif isinstance(payload, list):
                results = payload

            if isinstance(results, list):
                for entry in results[:15]:
                    if not isinstance(entry, dict):
                        continue
                    ext_id = str(entry.get("id") or entry.get("pk") or "")
                    full_ext_id = f"kn_{src_type}_{ext_id or uuid.uuid4().hex[:8]}"
                    if full_ext_id in seen_ext_ids:
                        continue
                    seen_ext_ids.add(full_ext_id)
                    created = (entry.get("date_written") or entry.get("created") or entry.get("created_at") or entry.get("date") or "")[:10]
                    content = entry.get("content") or entry.get("body") or entry.get("text") or ""
                    first_line = str(content).strip().splitlines()[0][:45] if content else ""
                    title = entry.get("title") or entry.get("subject") or first_line or f"{created} {src_type}"
                    if content and len(str(content).strip()) > 10:
                        collected_posts.append({
                            "external_id": full_ext_id,
                            "source_type": src_type,
                            "title": str(title),
                            "content": str(content),
                            "post_date": created or datetime.date.today().isoformat(),
                        })

        # 2) 화면 텍스트 백업 추출
        if not collected_posts:
            articles = page.query_selector_all("article, [class*='post'], [class*='item'], [class*='card'], main div")
            seen_texts = set()
            for idx, art in enumerate(articles[:20]):
                try:
                    txt = art.inner_text().strip()
                except Exception:
                    continue
                if 40 < len(txt) < 3000 and txt not in seen_texts:
                    seen_texts.add(txt)
                    first_line = txt.splitlines()[0][:50]
                    collected_posts.append({
                        "external_id": f"kn_dom_{datetime.date.today().isoformat()}_{abs(hash(txt[:120]))}",
                        "source_type": "키즈노트",
                        "title": first_line,
                        "content": txt,
                        "post_date": datetime.date.today().isoformat(),
                    })
                    if len(collected_posts) >= 8:
                        break

        context.close()

    return collected_posts


def generate_ics_content(target_date=None):
    """
    갤럭시 기본 캘린더(삼성 캘린더) 및 네이버 캘린더에서 바로 열리는 표준 .ics 달력 파일 생성
    (전날 저녁 8시 알림 + 당일 아침 7시 30분 알림 포함)
    """
    conn = get_db()
    if target_date:
        rows = conn.execute(
            "SELECT * FROM checklist_items WHERE target_date = ? ORDER BY category, id",
            (target_date,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM checklist_items WHERE is_checked = 0 ORDER BY target_date, category, id"
        ).fetchall()
    conn.close()

    grouped = {}
    for r in rows:
        d = r["target_date"]
        grouped.setdefault(d, {"titles": set(), "items": []})
        if r["event_title"]:
            grouped[d]["titles"].add(r["event_title"])
        grouped[d]["items"].append(r)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//KidsNote Organizer//KR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:키즈노트 준비물·일정",
        "X-WR-TIMEZONE:Asia/Seoul",
    ]

    now_utc = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    for d_str, info in grouped.items():
        try:
            dt = datetime.date.fromisoformat(d_str)
        except Exception:
            continue
        next_dt = dt + datetime.timedelta(days=1)
        dt_start = dt.strftime("%Y%m%d")
        dt_end = next_dt.strftime("%Y%m%d")

        title_Main = ", ".join(list(info["titles"])[:2]) or "어린이집/유치원 준비물"
        summary_text = f"[키즈노트] {title_Main}"

        desc_lines = []
        for item in info["items"]:
            check_mark = "V" if item["is_checked"] else "□"
            desc_lines.append(f"[{item['category']}] {check_mark} {item['item_text']}")
        description = "\\n".join(desc_lines)

        uid = f"kidsnote-{dt_start}-{abs(hash(summary_text))}@local"

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now_utc}",
            f"DTSTART;VALUE=DATE:{dt_start}",
            f"DTEND;VALUE=DATE:{dt_end}",
            f"SUMMARY:{summary_text}",
            f"DESCRIPTION:{description}",
            # 전날 저녁 8시 알림 (-PT4H before midnight)
            "BEGIN:VALARM",
            "TRIGGER:-PT4H",
            "ACTION:DISPLAY",
            f"DESCRIPTION:내일 {summary_text} 챙기셨나요?",
            "END:VALARM",
            # 당일 아침 7시 30분 알림 (+PT7H30M after midnight)
            "BEGIN:VALARM",
            "TRIGGER:PT7H30M",
            "ACTION:DISPLAY",
            f"DESCRIPTION:오늘 {summary_text} 확인!",
            "END:VALARM",
            "END:VEVENT",
        ])

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


SAMPLE_NOTICES = [
    {
        "source_type": "공지사항",
        "title": "11월 가을 숲체험 현장학습 및 동절기 준비물 안내",
        "content": """안녕하세요, 학부모님! 날씨가 부쩍 쌀쌀해졌습니다.
이번 주 금요일(11월 20일)에는 아이들과 함께 가을 숲체험 현장학습을 다녀올 예정입니다.

1. 숲체험 현장학습 안내 (11월 20일 금요일)
- 복장: 활동하기 편한 원복 체육복과 편한 운동화를 신겨 보내주세요.
- 준비물: 개인 물통(보온물통), 가벼운 배낭, 물티슈 1팩
- 아침 9시 20분까지 꼭 등원 완료 부탁드립니다.

2. 동절기 낮잠이불 및 여벌옷 교체 안내 (11월 23일 월요일까지)
- 날씨가 추워져 다음 주 월요일(11월 23일)까지 따뜻한 겨울용 낮잠이불을 보내주시기 바랍니다.
- 사물함에 보관할 긴팔/긴바지 여벌옷 2벌과 양말도 이름표를 붙여서 함께 보내주세요.

3. 현장학습 동의서 회신
- 내일(11월 19일 목요일)까지 키즈노트 현장학습 동의서에 꼭 서명해 주세요!""",
    },
    {
        "source_type": "알림장",
        "title": "오늘 하루도 즐겁게 보냈어요 & 내일 생일파티 안내",
        "content": """오늘 우리 아이가 블록 놀이도 재미있게 하고 점심 반찬으로 나온 불고기도 남김없이 싹 비웠답니다!

* 내일(11월 19일 목요일)은 11월 친구들 생일파티가 있는 날입니다.
- 복장: 예쁜 자유복 입혀 보내주세요 (사진 촬영이 있어요).
- 준비물: 생일 축하 카드(가정에서 아이와 한 줄 적어 보내주시면 감사하겠습니다), 양치도구 세척 후 보내주기""",
    },
]


class KidsNoteHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            index_file = os.path.join(STATIC_DIR, "index.html")
            with open(index_file, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        if path == "/manifest.json":
            manifest = {
                "name": "키즈노트 준비물·일정 정리 비서",
                "short_name": "키즈노트비서",
                "start_url": "/",
                "display": "standalone",
                "background_color": "#f7f8fc",
                "theme_color": "#ff7a00",
            }
            self._send_json(manifest)
            return

        if path == "/api/state":
            conn = get_db()
            items = [dict(r) for r in conn.execute("SELECT * FROM checklist_items ORDER BY target_date ASC, id ASC").fetchall()]
            posts = [dict(r) for r in conn.execute("SELECT * FROM posts ORDER BY id DESC LIMIT 20").fetchall()]
            conn.close()

            for it in items:
                it["date_label"] = format_date_with_weekday(it["target_date"])

            api_key = get_setting("gemini_api_key", "")
            kn_user = get_setting("kidsnote_username", "")
            local_ip = get_local_ip()
            best_mobile_url = MOBILE_TUNNEL_URL or f"http://{local_ip}:8080"

            self._send_json({
                "has_api_key": bool(api_key),
                "masked_api_key": (api_key[:8] + "..." + api_key[-4:]) if len(api_key) > 12 else ("설정됨" if api_key else ""),
                "kidsnote_username": kn_user,
                "local_url": best_mobile_url,
                "direct_ip_url": f"http://{local_ip}:8080",
                "items": items,
                "posts": posts,
            })
            return

        if path == "/api/calendar.ics":
            qs = parse_qs(parsed.query)
            t_date = qs.get("date", [None])[0]
            ics_text = generate_ics_content(t_date)
            body = ics_text.encode("utf-8")
            filename = f"kidsnote_{t_date or 'all'}.ics"
            self.send_response(200)
            self.send_header("Content-Type", "text/calendar; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            data = self._read_json()

            if path == "/api/settings":
                if "gemini_api_key" in data and data["gemini_api_key"].strip():
                    set_setting("gemini_api_key", data["gemini_api_key"].strip())
                if "kidsnote_username" in data:
                    set_setting("kidsnote_username", data["kidsnote_username"].strip())
                if "kidsnote_password" in data and data["kidsnote_password"]:
                    set_setting("kidsnote_password", data["kidsnote_password"])
                self._send_json({"ok": True, "message": "설정이 안전하게 저장되었습니다!"})
                return

            if path == "/api/analyze":
                source_type = data.get("source_type", "공지사항")
                title = data.get("title", "").strip() or f"{source_type} 내용"
                content = data.get("content", "").strip()
                if not content:
                    self._send_json({"ok": False, "error": "내용을 입력해 주세요."}, 400)
                    return
                res = save_post_and_analyze(source_type, title, content)
                self._send_json({"ok": True, "result": res})
                return

            if path == "/api/sample":
                added_total = 0
                for sample in SAMPLE_NOTICES:
                    res = save_post_and_analyze(
                        sample["source_type"],
                        sample["title"],
                        sample["content"],
                    )
                    added_total += res.get("items_count", 0)
                self._send_json({"ok": True, "added_items": added_total})
                return

            if path == "/api/fetch-kidsnote":
                username = (data.get("username") or get_setting("kidsnote_username", "")).strip()
                password = data.get("password") or get_setting("kidsnote_password", "")
                if not username or not password:
                    self._send_json({"ok": False, "error": "키즈노트 아이디와 비밀번호를 입력해 주세요."}, 400)
                    return
                set_setting("kidsnote_username", username)
                set_setting("kidsnote_password", password)

                posts = fetch_from_kidsnote_playwright(username, password)
                new_posts = 0
                new_items = 0
                for p in posts:
                    res = save_post_and_analyze(
                        p["source_type"],
                        p["title"],
                        p["content"],
                        p["post_date"],
                        p["external_id"],
                    )
                    if not res.get("already_exists"):
                        new_posts += 1
                        new_items += res.get("items_count", 0)
                self._send_json({
                    "ok": True,
                    "fetched_count": len(posts),
                    "new_posts": new_posts,
                    "new_items": new_items,
                })
                return

            if path == "/api/check":
                item_id = int(data.get("id", 0))
                is_checked = 1 if data.get("is_checked") else 0
                conn = get_db()
                conn.execute("UPDATE checklist_items SET is_checked = ? WHERE id = ?", (is_checked, item_id))
                conn.commit()
                conn.close()
                self._send_json({"ok": True})
                return

            if path == "/api/delete-item":
                item_id = int(data.get("id", 0))
                conn = get_db()
                conn.execute("DELETE FROM checklist_items WHERE id = ?", (item_id,))
                conn.commit()
                conn.close()
                self._send_json({"ok": True})
                return

            if path == "/api/clear-all":
                conn = get_db()
                conn.execute("DELETE FROM checklist_items")
                conn.execute("DELETE FROM posts")
                conn.commit()
                conn.close()
                self._send_json({"ok": True})
                return

            self.send_error(404)
        except Exception as e:
            traceback.print_exc()
            self._send_json({"ok": False, "error": str(e)}, 500)


def main():
    init_db()
    port = 8080
    threading.Thread(target=start_mobile_tunnel, args=(port,), daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", port), KidsNoteHandler)
    local_ip = get_local_ip()
    print("=" * 60)
    print(f" [키즈노트 준비물·일정 정리 비서] 서버가 시작되었습니다!")
    print(f" - 내 PC에서 열기: http://localhost:{port}")
    print(f" - 내 갤럭시 폰에서 열기: 화면 우측 상단 [📱 핸드폰으로 열기 (QR코드)] 클릭")
    print("=" * 60)
    server.serve_forever()


if __name__ == "__main__":
    main()
