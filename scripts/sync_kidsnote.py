import os
import re
import json
import base64
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
        return f"{dt.month}월 {dt.day}일 ({WEEKDAYS_KR[dt.weekday()]})"
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
            data = json.load(f)
            if isinstance(data, dict) and "posts" in data and "items" in data:
                return data
    return {
        "updated_at": "",
        "child_info": "HelenDoron (2026 5yrs Honeybees)",
        "center_phone": "",
        "posts": [],
        "items": [],
    }


def fetch_image_parts(image_urls, max_images=3):
    parts = []
    for url in (image_urls or [])[:max_images]:
        try:
            r = requests.get(url, timeout=12)
            if r.status_code == 200 and len(r.content) < 4_500_000:
                ctype = r.headers.get("content-type", "image/jpeg").split(";")[0].strip()
                if not ctype.startswith("image/"):
                    ctype = "image/jpeg"
                parts.append({
                    "inlineData": {
                        "mimeType": ctype,
                        "data": base64.b64encode(r.content).decode("ascii"),
                    }
                })
        except Exception:
            pass
    return parts


def analyze_with_gemini(api_key, source_type, title, content, post_date_str, image_urls=None, center_phone="", child_name=""):
    try:
        base_dt = datetime.date.fromisoformat(post_date_str[:10])
    except Exception:
        base_dt = datetime.date.today()

    base_date_formatted = f"{base_dt.isoformat()} ({WEEKDAYS_KR[base_dt.weekday()]}요일)"
    upcoming_days_hint = []
    for i in range(0, 28):
        d = base_dt + datetime.timedelta(days=i)
        upcoming_days_hint.append(f"{d.isoformat()} = {d.month}월 {d.day}일 ({WEEKDAYS_KR[d.weekday()]}요일)")
    calendar_reference = "\n".join(upcoming_days_hint)

    prompt = f"""당신은 어린이집/유치원 학부모를 도와주는 '키즈노트 준비물·일정 정리 비서'입니다.
아래 [{source_type}] 글과 첨부된 이미지(있는 경우 표, 연락처, 주간안내문 이미지 포함)를 모두 읽고,
부모님이 꼭 챙겨야 하는 **준비물**, **복장**, **해야 할 일**, **주요 행사/일정**만 깔끔하고 정돈된 형태로 추출하세요.

[원아 정보]: {child_name or "유치원생"} (원 대표전화: {center_phone or "없음"})
[기준 작성일]: {base_date_formatted}
[날짜 계산 참고표]:
{calendar_reference}

[작성 규칙 - 매우 중요]:
1. 일상 인사말, 식사/놀이 보고, 다른 원아(우리 아이 이름이 아닌 다른 친구)의 Show&Tell 발표 순서 등은 제외하세요.
2. 첨부 이미지 안에 표, 주간안내, 시간표, **선생님/기사님 전화번호**가 있으면 반드시 끝까지 읽고 반영하세요!
3. 글이나 이미지에 **전화번호(010-..., 031-...)**, **버스/시간/장소**, **준비물**, **교재 페이지** 같은 구체 정보가 있으면:
   - `item_text`: 한눈에 들어오는 짧은 핵심 제목 (예: "차량 선생님 연락처 저장", "수영복·수건 챙기기", "P.E 수업 편한 복장")
   - `detail`: 구체적인 시간·장소·페이지·안내 요약 (1줄, 없으면 빈 문자열)
   - `contacts`: 관련 전화번호가 글/이미지에 있거나 "원으로 연락" 안내가 있을 경우 `{{"name": "이름/역할", "phone": "010-0000-0000"}}` 배열로 정확히 넣으세요. (예: Blue버스 하원 김현정T 010-7426-7403, Pink버스 엘라T 010-8327-9128, 원 대표번호 {center_phone})
4. 날짜 표현('내일', '다음 주 수요일' 등)은 [날짜 계산 참고표] 기준으로 정확한 `YYYY-MM-DD`로 변환하세요.
5. `category`는 반드시 `"준비물"`, `"복장"`, `"해야할일"`, `"행사"` 중 하나만 쓰세요.
6. 반드시 순수 JSON만 응답하세요:

{{
  "summary": "글 전체 핵심 1~2줄 요약 (이미지 내용 포함)",
  "items": [
    {{
      "target_date": "YYYY-MM-DD",
      "event_title": "짧은 행사/수업명 (예: 수영 수업, 4분기 데이케어)",
      "category": "준비물 | 복장 | 해야할일 | 행사",
      "item_text": "짧은 핵심 제목 (20자 안팎 권장)",
      "detail": "구체 정보 (시간/장소/페이지/세부내용)",
      "contacts": [
        {{"name": "김현정T (Blue버스 하원)", "phone": "010-7426-7403"}}
      ]
    }}
  ]
}}

---
[글 제목]: {title}
[글 본문]:
{content}
"""

    parts = [{"text": prompt}] + fetch_image_parts(image_urls, max_images=3)
    models_to_try = [
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite-preview",
        "gemini-2.5-flash-lite",
        "gemini-flash-latest",
    ]
    for model_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
        }
        try:
            resp = requests.post(url, json=payload, timeout=20)
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
            elif resp.status_code == 429:
                import time
                time.sleep(2)
        except Exception:
            pass

    return {"summary": content.strip().splitlines()[0][:100] if content else title, "items": []}


def format_phone(p):
    digits = re.sub(r"[^0-9]", "", str(p or ""))
    if len(digits) == 11:
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    if len(digits) == 10 and digits.startswith("02"):
        return f"{digits[:2]}-{digits[2:6]}-{digits[6:]}"
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return str(p or "")


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

    child = me_data["children"][0]
    enroll = child["enrollment"][0]
    child_id = enroll["child_id"]
    center_id = enroll["center_id"]
    cls_id = enroll["belong_to_class"]
    child_name = child.get("name", "")
    center_name = enroll.get("center_name", "")
    class_name = enroll.get("class_name", "")

    center_phone = ""
    try:
        c_info = session.get(f"https://kapi.kidsnote.com/v1/centers/{center_id}", headers=headers, timeout=10).json()
        center_phone = format_phone(c_info.get("phone", ""))
    except Exception:
        pass

    collected = []

    # 1. 공지사항 조회
    r_notices = session.get(
        f"https://kapi.kidsnote.com/v1/centers/{center_id}/notices?cls={cls_id}&page_size=10",
        headers=headers,
        timeout=15,
    )
    if r_notices.status_code == 200:
        for entry in r_notices.json().get("results", []):
            ext_id = f"kn_공지사항_{entry.get('id')}"
            created = (entry.get("date_written") or entry.get("created") or "")[:10]
            content = (entry.get("content") or "").strip()
            title = entry.get("title") or (content.splitlines()[0][:45] if content else "공지사항")
            imgs = [im.get("original") or im.get("large") for im in (entry.get("attached_images") or []) if im.get("original") or im.get("large")]
            if len(content) > 5 or imgs:
                collected.append({
                    "external_id": ext_id,
                    "source_type": "공지사항",
                    "title": title,
                    "content": content,
                    "post_date": created or datetime.date.today().isoformat(),
                    "images": imgs,
                })

    # 2. 알림장 조회
    r_reports = session.get(
        f"https://kapi.kidsnote.com/v1_2/children/{child_id}/reports/?page_size=10&tz=Asia%2FSeoul&center_id={center_id}&cls={cls_id}&child={child_id}",
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
            imgs = [im.get("original") or im.get("large") for im in (entry.get("attached_images") or []) if im.get("original") or im.get("large")]
            if len(content) > 5 or imgs:
                collected.append({
                    "external_id": ext_id,
                    "source_type": "알림장",
                    "title": title,
                    "content": content,
                    "post_date": created or datetime.date.today().isoformat(),
                    "images": imgs,
                })

    meta = {
        "child_info": f"{center_name} ({class_name})".strip(),
        "child_name": child_name,
        "center_phone": center_phone,
    }
    return collected, meta


def is_duplicate_item(existing_items, new_date, new_text):
    norm_new = re.sub(r"[^가-힣a-zA-Z0-9]", "", new_text).lower()
    for it in existing_items:
        if it.get("target_date") != new_date:
            continue
        norm_old = re.sub(r"[^가-힣a-zA-Z0-9]", "", it.get("item_text", "")).lower()
        if not norm_old or not norm_new:
            continue
        if norm_old == norm_new or norm_old in norm_new or norm_new in norm_old:
            return True
        # 핵심 키워드 중복 방지 (라즈키즈, 크리스마스 공연 등)
        for kw in ["라즈키즈", "크리스마스공연", "산타행사", "데이케어", "대여도서"]:
            if kw in norm_old and kw in norm_new:
                return True
    return False


def main(reanalyze_recent=False):
    api_key, username, password = get_credentials()
    if not username or not password:
        print("키즈노트 계정 정보가 없습니다.")
        return

    data = load_data()
    fetched_posts, meta = fetch_kidsnote_http(username, password)
    data["child_info"] = meta["child_info"] or data.get("child_info", "")
    data["center_phone"] = meta["center_phone"] or data.get("center_phone", "")

    if reanalyze_recent:
        # 최신 구조(이미지 OCR + 연락처 + detail + 중복 제거)로 전체 재구성
        data["posts"] = []
        data["items"] = []

    existing_by_ext = {p.get("external_id"): p for p in data.get("posts", [])}
    next_post_id = max([p.get("id", 0) for p in data["posts"]] + [0]) + 1
    next_item_id = max([it.get("id", 0) for it in data["items"]] + [0]) + 1

    new_post_count = 0
    new_item_count = 0
    now_str = datetime.datetime.now().isoformat(timespec="seconds")

    # 최신 글부터 처리하여 중복 시 최신 글 정보가 우선 남도록 정렬
    fetched_posts.sort(key=lambda x: x["post_date"], reverse=True)

    posts_to_analyze = []
    for p in fetched_posts:
        if p["external_id"] in existing_by_ext:
            existing_by_ext[p["external_id"]]["images"] = p.get("images", [])
            continue
        posts_to_analyze.append(p)

    from concurrent.futures import ThreadPoolExecutor

    def analyze_one(p):
        print(f"새 글 분석 중: [{p['source_type']}] {p['title']} ({p['post_date']}, 이미지 {len(p.get('images', []))}장)", flush=True)
        res = analyze_with_gemini(
            api_key,
            p["source_type"],
            p["title"],
            p["content"],
            p["post_date"],
            image_urls=p.get("images"),
            center_phone=meta["center_phone"],
            child_name=meta["child_name"],
        )
        return p, res

    analyzed_results = []
    if posts_to_analyze:
        with ThreadPoolExecutor(max_workers=3) as ex:
            analyzed_results = list(ex.map(analyze_one, posts_to_analyze))

    for p, analysis in analyzed_results:
        post_obj = {
            "id": next_post_id,
            "external_id": p["external_id"],
            "source_type": p["source_type"],
            "title": p["title"],
            "content": p["content"],
            "post_date": p["post_date"],
            "images": p.get("images", []),
            "summary": analysis.get("summary", ""),
            "created_at": now_str,
        }
        data["posts"].append(post_obj)
        existing_by_ext[p["external_id"]] = post_obj

        for item in analysis.get("items", []):
            t_date = (item.get("target_date") or p["post_date"])[:10]
            cat = item.get("category") or "준비물"
            if cat not in ("준비물", "복장", "해야할일", "행사"):
                cat = "준비물"
            text = (item.get("item_text") or "").strip()
            detail = (item.get("detail") or "").strip()
            contacts = item.get("contacts") or []
            if not isinstance(contacts, list):
                contacts = []
            clean_contacts = []
            for c in contacts:
                if isinstance(c, dict) and c.get("phone"):
                    clean_contacts.append({
                        "name": str(c.get("name") or "연락처").strip(),
                        "phone": format_phone(c.get("phone")),
                    })

            if text and not is_duplicate_item(data["items"], t_date, text):
                data["items"].append({
                    "id": next_item_id,
                    "post_id": next_post_id,
                    "target_date": t_date,
                    "date_label": format_date_with_weekday(t_date),
                    "event_title": (item.get("event_title") or p["title"]).strip(),
                    "category": cat,
                    "item_text": text,
                    "detail": detail,
                    "contacts": clean_contacts,
                    "is_checked": 0,
                    "created_at": now_str,
                })
                next_item_id += 1
                new_item_count += 1

        next_post_id += 1
        new_post_count += 1

    data["items"].sort(key=lambda x: (x.get("target_date", ""), x.get("id", 0)))
    data["posts"].sort(key=lambda x: (x.get("post_date", ""), x.get("id", 0)), reverse=True)
    kst_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    data["updated_at"] = kst_now.strftime("%Y-%m-%d %H:%M")

    with open(DATA_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"동기화 완료: 새 글 {new_post_count}건, 새 항목 {new_item_count}개 (총 항목 {len(data['items'])}개)")


if __name__ == "__main__":
    import sys
    reanalyze = "--rebuild" in sys.argv
    main(reanalyze_recent=reanalyze)
