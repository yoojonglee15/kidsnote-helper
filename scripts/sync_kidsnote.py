import os
import re
import json
import sqlite3
import datetime
import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JSON_PATH = os.path.join(BASE_DIR, "kidsnote-data.json")
LOCAL_DB_PATH = os.path.join(BASE_DIR, "data", "kidsnote.db")

WEEKDAYS_KR = ["월", "화", "수", "목", "금", "토", "일"]


def format_date_with_weekday(date_str):
    try:
        dt = datetime.date.fromisoformat(date_str[:10])
        return f"{dt.strftime('%Y-%m-%d')} ({WEEKDAYS_KR[dt.weekday()]})"
    except Exception:
        return date_str


def get_credentials():
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    username = os.environ.get("KIDSNOTE_USERNAME", "").strip()
    password = os.environ.get("KIDSNOTE_PASSWORD", "").strip()

    if (not api_key or not username or not password) and os.path.exists(LOCAL_DB_PATH):
        conn = sqlite3.connect(LOCAL_DB_PATH)
        rows = dict(conn.execute("SELECT key, value FROM settings").fetchall())
        conn.close()
        api_key = api_key or rows.get("gemini_api_key", "").strip()
        username = username or rows.get("kidsnote_username", "").strip()
        password = password or rows.get("kidsnote_password", "").strip()

    return api_key, username, password


def load_data():
    if os.path.exists(DATA_JSON_PATH):
        with open(DATA_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    # 최초 실행 시 로컬 SQLite DB가 있으면 마이그레이션
    posts = []
    items = []
    if os.path.exists(LOCAL_DB_PATH):
        conn = sqlite3.connect(LOCAL_DB_PATH)
        conn.row_factory = sqlite3.Row
        posts = [dict(r) for r in conn.execute("SELECT * FROM posts ORDER BY post_date DESC, id DESC").fetchall()]
        items = [dict(r) for r in conn.execute("SELECT * FROM checklist_items ORDER BY target_date ASC, id ASC").fetchall()]
        conn.close()
        for it in items:
            it["date_label"] = format_date_with_weekday(it["target_date"])

    return {
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "child_info": "HelenDoron (2026 5yrs Honeybees)",
        "posts": posts,
        "items": items,
    }


def analyze_with_gemini(api_key, source_type, title, content, post_date_str):
    try:
        base_dt = datetime.date.fromisoformat(post_date_str[:10])
    except Exception:
        base_dt = datetime.date.today()

    base_date_formatted = f"{base_dt.isoformat()} ({WEEKDAYS_KR[base_dt.weekday()]}요일)"
    upcoming_days_hint = []
    for i in range(0, 21):
        d = base_dt + datetime.timedelta(days=i)
        upcoming_days_hint.append(f"{d.isoformat()} = {d.month}월 {d.day}일 ({WEEKDAYS_KR[d.weekday()]}요일)")
    calendar_reference = "\n".join(upcoming_days_hint)

    prompt = f"""당신은 어린이집/유치원 학부모를 도와주는 '키즈노트 준비물·일정 정리 비서'입니다.
아래 [{source_type}] 글을 읽고, 부모님이 꼭 챙겨야 하는 **준비물**, **복장(입혀 보낼 옷/신발)**, **해야 할 일(동의서 회신, 숙제 지도 등)**, **주요 행사/일정**만 날짜별로 정확하게 추출하세요.

[기준 작성일]: {base_date_formatted}
[날짜 계산 참고표]:
{calendar_reference}

[작성 규칙]:
1. 일상적인 인사말이나 단순 활동 보고는 제외하고, 부모님이 준비하거나 기억해야 할 사항만 추출하세요.
2. 글에 '내일', '금요일', '다음 주 화요일' 같은 표현이 있으면 [날짜 계산 참고표]를 기준으로 정확한 `YYYY-MM-DD` 날짜로 변환하세요.
3. category(분류)는 반드시 다음 4가지 중 하나로만 지정하세요: "준비물", "복장", "해야할일", "행사"
4. 반드시 아래 JSON 형식으로만 응답하세요:

{{
  "summary": "글 전체 핵심 1~2줄 요약",
  "items": [
    {{
      "target_date": "YYYY-MM-DD",
      "event_title": "해당 날짜의 행사명 또는 일정 제목",
      "category": "준비물 | 복장 | 해야할일 | 행사",
      "item_text": "구체적으로 챙겨야 할 항목"
    }}
  ]
}}

---
[글 제목]: {title}
[글 본문]:
{content}
"""
    models_to_try = [
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite-preview",
        "gemini-2.5-flash-lite",
        "gemini-flash-latest",
    ]
    for model_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
        }
        try:
            resp = requests.post(url, json=payload, timeout=25)
            if resp.status_code == 200:
                text_out = (
                    resp.json()
                    .get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                )
                cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text_out.strip())
                parsed = json.loads(cleaned)
                if isinstance(parsed, dict) and "items" in parsed:
                    return parsed
        except Exception:
            pass

    return {"summary": content.strip().splitlines()[0][:100] if content else title, "items": []}


def fetch_kidsnote_http(username, password):
    session = requests.Session()
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0.0.0"}

    r_login = session.post(
        "https://www.kidsnote.com/api/web/login/",
        json={"username": username, "password": password, "remember_me": True},
        headers=headers,
        timeout=15,
    )
    if r_login.status_code != 200:
        raise RuntimeError(f"키즈노트 로그인 실패: HTTP {r_login.status_code}")

    r_me = session.get("https://kapi.kidsnote.com/v1/me/info/", headers=headers, timeout=15)
    r_me.raise_for_status()
    me_data = r_me.json()

    enroll = me_data["children"][0]["enrollment"][0]
    child_id = enroll["child_id"]
    center_id = enroll["center_id"]
    cls_id = enroll["belong_to_class"]

    collected = []

    # 1. 공지사항 조회
    r_notices = session.get(
        f"https://kapi.kidsnote.com/v1/centers/{center_id}/notices?cls={cls_id}&page_size=12",
        headers=headers,
        timeout=15,
    )
    if r_notices.status_code == 200:
        for entry in r_notices.json().get("results", []):
            ext_id = f"kn_공지사항_{entry.get('id')}"
            created = (entry.get("date_written") or entry.get("created") or "")[:10]
            content = (entry.get("content") or "").strip()
            title = entry.get("title") or (content.splitlines()[0][:45] if content else "공지사항")
            if len(content) > 10:
                collected.append({
                    "external_id": ext_id,
                    "source_type": "공지사항",
                    "title": title,
                    "content": content,
                    "post_date": created or datetime.date.today().isoformat(),
                })

    # 2. 알림장 조회
    r_reports = session.get(
        f"https://kapi.kidsnote.com/v1_2/children/{child_id}/reports/?page_size=12&tz=Asia%2FSeoul&center_id={center_id}&cls={cls_id}&child={child_id}",
        headers=headers,
        timeout=15,
    )
    if r_reports.status_code == 200:
        for entry in r_reports.json().get("results", []):
            ext_id = f"kn_알림장_{entry.get('id')}"
            created = (entry.get("date_written") or entry.get("created") or "")[:10]
            content = (entry.get("content") or "").strip()
            first_line = content.splitlines()[0][:45] if content else "알림장"
            title = entry.get("title") or first_line
            if len(content) > 10:
                collected.append({
                    "external_id": ext_id,
                    "source_type": "알림장",
                    "title": title,
                    "content": content,
                    "post_date": created or datetime.date.today().isoformat(),
                })

    return collected


def main():
    api_key, username, password = get_credentials()
    if not username or not password:
        print("키즈노트 계정 정보가 없습니다.")
        return

    data = load_data()
    existing_ext_ids = {p.get("external_id") for p in data.get("posts", [])}

    fetched_posts = fetch_kidsnote_http(username, password)
    next_post_id = max([p.get("id", 0) for p in data["posts"]] + [0]) + 1
    next_item_id = max([it.get("id", 0) for it in data["items"]] + [0]) + 1

    new_post_count = 0
    new_item_count = 0
    now_str = datetime.datetime.now().isoformat(timespec="seconds")

    for p in fetched_posts:
        if p["external_id"] in existing_ext_ids:
            continue

        print(f"새 글 분석 중: [{p['source_type']}] {p['title']} ({p['post_date']})")
        analysis = analyze_with_gemini(api_key, p["source_type"], p["title"], p["content"], p["post_date"])
        post_obj = {
            "id": next_post_id,
            "external_id": p["external_id"],
            "source_type": p["source_type"],
            "title": p["title"],
            "content": p["content"],
            "post_date": p["post_date"],
            "summary": analysis.get("summary", ""),
            "created_at": now_str,
        }
        data["posts"].insert(0, post_obj)
        existing_ext_ids.add(p["external_id"])

        for item in analysis.get("items", []):
            t_date = (item.get("target_date") or p["post_date"])[:10]
            cat = item.get("category") or "준비물"
            if cat not in ("준비물", "복장", "해야할일", "행사"):
                cat = "준비물"
            text = (item.get("item_text") or "").strip()
            if text:
                data["items"].append({
                    "id": next_item_id,
                    "post_id": next_post_id,
                    "target_date": t_date,
                    "date_label": format_date_with_weekday(t_date),
                    "event_title": item.get("event_title") or p["title"],
                    "category": cat,
                    "item_text": text,
                    "is_checked": 0,
                    "created_at": now_str,
                })
                next_item_id += 1
                new_item_count += 1

        next_post_id += 1
        new_post_count += 1

    data["items"].sort(key=lambda x: (x.get("target_date", ""), x.get("id", 0)))
    data["posts"].sort(key=lambda x: (x.get("post_date", ""), x.get("id", 0)), reverse=True)
    kst_now = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    data["updated_at"] = kst_now.strftime("%Y-%m-%d %H:%M")

    with open(DATA_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"동기화 완료: 새 글 {new_post_count}건, 새 항목 {new_item_count}개 (총 항목 {len(data['items'])}개)")


if __name__ == "__main__":
    main()
