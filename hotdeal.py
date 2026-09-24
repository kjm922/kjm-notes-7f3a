# -*- coding: utf-8 -*-
"""
핫딜 통합 뷰어 — 퀘이사존 · 루리웹 · 아카라이브 · 뽐뿌 · 클리앙 핫딜 게시판을 한 페이지로.

실행:  python hotdeal.py      (브라우저가 자동으로 열립니다: http://127.0.0.1:8765)
필요:  pip install requests beautifulsoup4   (아나콘다에는 기본 포함)
"""
import json
import os
import re
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("필요한 패키지가 없습니다. 아래 명령을 먼저 실행하세요:\n  pip install requests beautifulsoup4")
    sys.exit(1)

try:                       # 봇 차단(Cloudflare)을 피하려고 크롬처럼 접속하는 라이브러리 (있으면 사용)
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None

KST = timezone(timedelta(hours=9))
PORT = 8765
CACHE_SECONDS = 180          # 같은 사이트를 3분 안에 다시 긁지 않음
TIMEOUT = 12

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

ENDED_RE = re.compile(r"종료|품절|마감|매진")


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def to_int(s):
    m = re.search(r"[\d,]+", s or "")
    return int(m.group().replace(",", "")) if m else 0


def rel_ko_to_dt(text, now):
    """'1시간 전', '20분전', '3일 전', '방금' -> datetime (근사값)."""
    t = (text or "").replace(" ", "")
    m = re.match(r"(\d+)(초|분|시간|일|주|개월|달|년)", t)
    if not m:
        return now if "방금" in t else None
    n, unit = int(m.group(1)), m.group(2)
    delta = {"초": timedelta(seconds=n), "분": timedelta(minutes=n), "시간": timedelta(hours=n),
             "일": timedelta(days=n), "주": timedelta(weeks=n), "개월": timedelta(days=30 * n),
             "달": timedelta(days=30 * n), "년": timedelta(days=365 * n)}[unit]
    return now - delta


# ----------------------------------------------------------------------------
# 사이트별 파서: 각 함수는 HTML 문자열을 받아 dict 리스트를 돌려줍니다.
#   {id, site, title, url, category, shop, price, shipping, thumb,
#    comments, likes, views, ended, ts(epoch초), time_text}
# ----------------------------------------------------------------------------

def parse_quasarzone(html, now):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for row in soup.select("div.v2-list-row"):
        a = row.select_one("a.subject-link")
        if not a:
            continue
        cls = row.get("class", [])
        time_text = clean(row.select_one(".v2-list-row__time").get_text()) if row.select_one(".v2-list-row__time") else ""
        dt = rel_ko_to_dt(time_text, now)
        ships = row.select(".v2-list-row__price-group .v2-list-row__ship")
        shop, shipping = "", ""
        if ships:
            img = ships[0].find("img")
            shop = clean(img.get("alt")) if img else clean(ships[0].get_text())
            if len(ships) > 1:
                shipping = clean(ships[-1].get_text()).replace("배송비", "").strip()
        price_el = row.select_one(".v2-list-row__price")
        price = clean(price_el.get_text()).replace("￦", "").strip() if price_el else ""
        if price in ("0", ""):
            price = ""
        title = clean(a.get_text())
        out.append({
            "id": "qz-" + (row.get("data-qc-id") or a["href"].rstrip("/").split("/")[-1]),
            "site": "quasarzone",
            "title": title,
            "url": urljoin("https://quasarzone.com/", a["href"]),
            "category": clean(row.select_one(".v2-badge").get_text()) if row.select_one(".v2-badge") else "",
            "shop": shop,
            "price": price,
            "shipping": shipping,
            "thumb": (row.get("data-preview") or "").rstrip("?"),
            "comments": to_int(row.select_one(".qc-count-comment").get_text()) if row.select_one(".qc-count-comment") else 0,
            "likes": to_int(row.select_one(".qc-count-good").get_text()) if row.select_one(".qc-count-good") else 0,
            "views": clean(row.select_one(".qc-count-hit").get_text()) if row.select_one(".qc-count-hit") else "",
            "hot": row.select_one(".v2-badge--hot") is not None,
            "ended": "is-done" in cls or bool(ENDED_RE.search(title)),
            "ts": dt.timestamp() if dt else 0,
            "time_text": time_text,
        })
    return out


def parse_ruliweb(html, now):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for row in soup.select("tr.table_body"):
        cls = row.get("class", [])
        if "notice" in cls or "best" in cls:      # 공지 / 상단 BEST 중복 제외
            continue
        a = row.select_one("a.subject_link")
        if not a:
            continue
        reply = row.select_one(".num_reply")
        comments = to_int(reply.get_text()) if reply else 0
        # 제목: 링크 텍스트에서 댓글수 제거
        for junk in a.select(".num_reply, i"):
            junk.decompose()
        title = clean(a.get_text())
        subj_td = row.select_one("td.subject")
        ended = bool(ENDED_RE.search(clean(subj_td.get_text()))) if subj_td else False
        time_text = clean(row.select_one("td.time").get_text()) if row.select_one("td.time") else ""
        dt = None
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", time_text)
        if m:
            dt = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
            if dt > now + timedelta(minutes=5):
                dt -= timedelta(days=1)
        else:
            m = re.fullmatch(r"(\d{2,4})\.(\d{1,2})\.(\d{1,2})", time_text)
            if m:
                y = int(m.group(1))
                y = y + 2000 if y < 100 else y
                dt = datetime(y, int(m.group(2)), int(m.group(3)), 12, 0, tzinfo=KST)
        rid = clean(row.select_one("td.id").get_text()) if row.select_one("td.id") else a["href"].split("/read/")[-1].split("?")[0]
        out.append({
            "id": "rw-" + rid,
            "site": "ruliweb",
            "title": title,
            "url": a["href"].split("?")[0],
            "category": clean(row.select_one("td.divsn").get_text()) if row.select_one("td.divsn") else "",
            "shop": "",
            "price": "",
            "shipping": "",
            "thumb": "",
            "comments": comments,
            "likes": to_int(row.select_one("td.recomd").get_text()) if row.select_one("td.recomd") else 0,
            "views": clean(row.select_one("td.hit").get_text()) if row.select_one("td.hit") else "",
            "hot": False,
            "ended": ended,
            "ts": dt.timestamp() if dt else 0,
            "time_text": time_text,
        })
    return out


def parse_arca(html, now):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for row in soup.select(".list-table .vrow"):
        cls = row.get("class", [])
        if "head" in cls or "notice" in cls:
            continue
        a = row.select_one("a.title.hybrid-title") or row.select_one("a.title")
        if not a:
            continue
        cc = row.select_one(".comment-count")
        comments = to_int(cc.get_text()) if cc else 0
        for junk in a.select(".info, .media-icon"):
            junk.decompose()
        title = clean(a.get_text())
        t = row.select_one("time")
        dt = None
        time_text = clean(t.get_text()) if t else ""
        if t and t.get("datetime"):
            try:
                dt = datetime.fromisoformat(t["datetime"].replace("Z", "+00:00")).astimezone(KST)
            except ValueError:
                dt = None
        thumb = row.select_one(".vrow-preview img")
        thumb_src = thumb.get("src", "") if thumb else ""
        if thumb_src.startswith("//"):
            thumb_src = "https:" + thumb_src
        badge = row.select_one(".badges a.badge")
        store = row.select_one(".deal-store")
        price = row.select_one(".deal-price")
        deliv = row.select_one(".deal-delivery")
        rid = clean(row.select_one(".col-id").get_text()) if row.select_one(".col-id") else a["href"].split("/")[-1].split("?")[0]
        out.append({
            "id": "ac-" + rid,
            "site": "arca",
            "title": title,
            "url": urljoin("https://arca.live/", a["href"].split("?")[0]),
            "category": clean(badge.get_text()) if badge else "",
            "shop": clean(store.get_text()) if store else "",
            "price": clean(price.get_text()).replace("원", "").strip() if price else "",
            "shipping": clean(deliv.get_text()) if deliv else "",
            "thumb": thumb_src,
            "comments": comments,
            "likes": to_int(row.select_one(".col-rate").get_text()) if row.select_one(".col-rate") else 0,
            "views": clean(row.select_one(".col-view").get_text()) if row.select_one(".col-view") else "",
            "hot": False,
            "ended": bool(ENDED_RE.search(title)),
            "ts": dt.timestamp() if dt else 0,
            "time_text": time_text,
        })
    return out



def parse_ppomppu(html, now):
    """뽐뿌게시판 모바일 목록 (m.ppomppu.co.kr/new/bbs_list.php?id=ppomppu)."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for li in soup.select("li.bbs_list_thumbnail"):
        a = li.select_one("a[href*='bbs_view.php']")
        if not a or "id=ppomppu" not in a.get("href", ""):
            continue
        m = re.search(r"no=(\d+)", a["href"])
        if not m:
            continue
        rid = m.group(1)
        cont = li.select_one(".title .cont")
        if not cont:
            continue
        pre = cont.select_one(".subject_preface")
        shop = clean(pre.get_text()).strip("[]") if pre else ""
        hot = bool(cont.select_one("img.newhot"))
        if pre:
            pre.decompose()
        for img in cont.select("img"):
            img.decompose()
        title = clean(cont.get_text())
        price, shipping = "", ""
        pm = re.search(r"\(([\d,]+)\s*원?\s*/\s*([^)]*)\)\s*$", title)
        if pm:
            price, shipping = pm.group(1), clean(pm.group(2))
            title = clean(title[:pm.start()])
        rp = li.select_one(".title .rp")
        names = clean(li.select_one(".names").get_text()) if li.select_one(".names") else ""
        cm = re.match(r"\[([^\]]+)\]", names)
        category = cm.group(1) if cm else ""
        t = li.select_one(".exp time")
        time_text = clean(t.get_text()) if t else ""
        dt = None
        try:
            if re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", time_text):
                h, mi, sec = map(int, time_text.split(":"))
                dt = now.replace(hour=h, minute=mi, second=sec, microsecond=0)
                if dt > now + timedelta(minutes=5):
                    dt -= timedelta(days=1)
            elif re.fullmatch(r"\d{2}/\d{2}/\d{2}", time_text):
                dt = datetime.strptime(time_text, "%y/%m/%d").replace(tzinfo=KST)
            elif re.fullmatch(r"\d{2}/\d{2}", time_text):
                dt = datetime.strptime(time_text, "%m/%d").replace(year=now.year, tzinfo=KST)
                if dt > now + timedelta(days=1):
                    dt = dt.replace(year=now.year - 1)
        except ValueError:
            dt = None
        thumb = li.select_one(".thmb_N img")
        thumb_src = thumb.get("src", "") if thumb else ""
        if thumb_src.startswith("//"):
            thumb_src = "https:" + thumb_src
        if "no_img" in thumb_src:
            thumb_src = ""
        view = li.select_one(".exp .view")
        recs = li.select_one(".exp .recs")
        out.append({
            "id": "pp-" + rid,
            "site": "ppomppu",
            "title": title,
            "url": "https://www.ppomppu.co.kr/zboard/view.php?id=ppomppu&no=" + rid,
            "category": category,
            "shop": shop,
            "price": price,
            "shipping": shipping,
            "thumb": thumb_src,
            "comments": to_int(rp.get_text()) if rp else 0,
            "likes": to_int(recs.get_text()) if recs else 0,
            "views": clean(view.get_text()) if view else "",
            "hot": hot,
            "ended": bool(ENDED_RE.search(title)),
            "ts": dt.timestamp() if dt else 0,
            "time_text": time_text,
        })
    return out



def parse_clien(html, now):
    """클리앙 알뜰구매 (clien.net/service/board/jirum)."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for row in soup.select(".list_item[data-board-sn]"):
        cls = row.get("class", [])
        if "notice" in cls or "hongbo" in cls:
            continue
        a = row.select_one("[data-role='list-title-text']") or row.select_one(".list_subject a")
        if not a:
            continue
        rid = row["data-board-sn"]
        title = clean(a.get_text())
        shop = ""
        sm = re.match(r"\[([^\]]{1,20})\]\s*", title)
        if sm:
            shop, title = sm.group(1), clean(title[sm.end():])
        price, shipping = "", ""
        pm = re.search(r"\(([\d,]+)\s*원?(?:\s*/\s*([^)]*))?\)\s*$", title)
        if pm:
            price, shipping = pm.group(1), clean(pm.group(2) or "")
            title = clean(title[:pm.start()])
        kw = row.select_one(".keyword a.icon_keyword, .keyword .icon_keyword")
        ts_el = row.select_one(".list_time .timestamp")
        dt = None
        time_text = clean(row.select_one(".list_time .time").contents[0]) if row.select_one(".list_time .time") and row.select_one(".list_time .time").contents else ""
        if ts_el:
            try:
                dt = datetime.strptime(clean(ts_el.get_text()), "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
            except ValueError:
                dt = None
        thumb = row.select_one(".list_thumbnail img")
        thumb_src = thumb.get("src", "") if thumb else ""
        votes = row.select_one(".list_votes") or row.select_one(".list_symph em")
        hit = row.select_one(".list_hit .hit")
        out.append({
            "id": "cl-" + rid,
            "site": "clien",
            "title": title,
            "url": "https://www.clien.net/service/board/jirum/" + rid,
            "category": clean(kw.get_text()) if kw else "",
            "shop": shop,
            "price": price,
            "shipping": shipping,
            "thumb": thumb_src,
            "comments": int(row.get("data-comment-count") or 0),
            "likes": to_int(votes.get_text()) if votes else 0,
            "views": clean(hit.get_text()) if hit else "",
            "hot": False,
            "ended": "sold_out" in cls or bool(ENDED_RE.search(title)),
            "ts": dt.timestamp() if dt else 0,
            "time_text": time_text,
        })
    return out


SITES = {
    "quasarzone": {"name": "퀘이사존", "url": "https://quasarzone.com/bbs/qb_saleinfo", "parse": parse_quasarzone, "color": "#e11d48"},
    "ruliweb":    {"name": "루리웹",   "url": "https://bbs.ruliweb.com/market/board/1020", "parse": parse_ruliweb, "color": "#2563eb"},
    "arca":       {"name": "아카라이브", "url": "https://arca.live/b/hotdeal", "parse": parse_arca, "color": "#7c3aed"},
    "ppomppu":    {"name": "뽐뿌",     "url": "https://m.ppomppu.co.kr/new/bbs_list.php?id=ppomppu", "parse": parse_ppomppu, "color": "#059669"},
    "clien":      {"name": "클리앙",   "url": "https://www.clien.net/service/board/jirum", "parse": parse_clien, "color": "#0891b2"},
}

_cache = {}          # site -> {"at": epoch, "items": [...], "error": str|None}
_lock = threading.Lock()


IMPERSONATE = ["chrome", "chrome131", "chrome124", "edge101", "safari17_0", "firefox133"]
_pw_lock = threading.Lock()


def _looks_blocked(text):
    return ("Just a moment" in text or "cf-chl" in text or "challenge-platform" in text) and "v2-list-row" not in text


def _browser_get(url):
    """최후 수단: PC에 설치된 크롬을 (창 없이) 띄워서 페이지를 받아온다. playwright 필요."""
    from playwright.sync_api import sync_playwright   # pip install playwright
    with _pw_lock, sync_playwright() as p:
        browser = None
        for channel in ("chrome", "msedge", None):
            try:
                browser = p.chromium.launch(channel=channel, headless=True) if channel else p.chromium.launch(headless=True)
                break
            except Exception:  # noqa: BLE001
                continue
        if browser is None:
            raise RuntimeError("크롬/엣지를 찾지 못함")
        try:
            ctx = browser.new_context(locale="ko-KR", user_agent=HEADERS["User-Agent"].replace("HeadlessChrome", "Chrome"))
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            for _ in range(10):                    # Cloudflare 검사 화면이면 잠시 기다림
                html = page.content()
                if not _looks_blocked(html):
                    return html
                page.wait_for_timeout(1500)
            return page.content()
        finally:
            browser.close()


def http_get(url):
    """페이지 HTML을 가져온다. requests → curl_cffi(브라우저 지문 흉내) → 실제 크롬 순으로 시도."""
    tried = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if not r.encoding or r.encoding.lower() == "iso-8859-1":   # 뽐뿌(EUC-KR)처럼 헤더에 문자셋이 없을 때
            r.encoding = r.apparent_encoding
        if r.status_code == 200 and not _looks_blocked(r.text):
            return r.text
        tried.append(f"requests={r.status_code}")
    except Exception as e:  # noqa: BLE001
        tried.append(f"requests={type(e).__name__}")
    if cffi_requests is not None:
        for imp in IMPERSONATE:
            try:
                r = cffi_requests.get(url, impersonate=imp, timeout=TIMEOUT,
                                      headers={"Accept-Language": HEADERS["Accept-Language"]})
                if r.status_code == 200 and not _looks_blocked(r.text):
                    return r.text
                tried.append(f"{imp}={r.status_code}")
            except Exception as e:  # noqa: BLE001
                tried.append(f"{imp}={type(e).__name__}")
    else:
        tried.append("curl_cffi 미설치")
    try:
        html = _browser_get(url)
        if not _looks_blocked(html):
            return html
        tried.append("chrome=차단화면")
    except ImportError:
        tried.append("playwright 미설치")
    except Exception as e:  # noqa: BLE001
        tried.append(f"chrome={type(e).__name__}: {str(e)[:80]}")
    raise RuntimeError("접속 차단됨 (" + ", ".join(tried) + ")")


def fetch_site(key, force=False):
    site = SITES[key]
    with _lock:
        c = _cache.get(key)
        if c and not force and time.time() - c["at"] < CACHE_SECONDS:
            return c
    result = {"at": time.time(), "items": [], "error": None}
    try:
        result["items"] = site["parse"](http_get(site["url"]), datetime.now(KST))
        if not result["items"]:
            result["error"] = "글 목록을 찾지 못했습니다 (사이트 구조가 바뀌었을 수 있음)"
    except Exception as e:      # noqa: BLE001
        result["error"] = f"{type(e).__name__}: {e}"
        if c:                   # 실패하면 이전 결과라도 유지
            result["items"] = c["items"]
    with _lock:
        _cache[key] = result
    return result


def collect(force=False):
    with ThreadPoolExecutor(max_workers=len(SITES)) as ex:
        results = dict(zip(SITES, ex.map(lambda k: fetch_site(k, force), SITES)))
    items = []
    status = {}
    for k, r in results.items():
        items.extend(r["items"])
        status[k] = {"name": SITES[k]["name"], "count": len(r["items"]), "error": r["error"],
                     "at": r["at"], "color": SITES[k]["color"], "url": SITES[k]["url"]}
    items.sort(key=lambda x: x["ts"], reverse=True)
    return {"items": items, "status": status, "generated": time.time()}


# ----------------------------------------------------------------------------
# 웹 페이지
# ----------------------------------------------------------------------------
_EMBEDDED_PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>핫딜 통합</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--fg:#1a1d24;--muted:#6b7280;--line:#e5e7eb;--accent:#111827}
@media(prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--fg:#e6e8ee;--muted:#9aa1ad;--line:#272b35;--accent:#e6e8ee}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 -apple-system,"Malgun Gothic","Apple SD Gothic Neo",system-ui,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--card);border-bottom:1px solid var(--line);padding:10px 16px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
h1{font-size:18px;margin:0 8px 0 0}
.chip{border:1px solid var(--line);background:transparent;color:var(--fg);border-radius:999px;padding:4px 12px;cursor:pointer;font-size:13px;display:inline-flex;gap:6px;align-items:center}
.chip.on{background:var(--accent);color:var(--bg);border-color:var(--accent)}
.chip .dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.chip .n{opacity:.7}
input[type=search]{flex:1;min-width:160px;border:1px solid var(--line);background:var(--bg);color:var(--fg);border-radius:8px;padding:7px 10px;font-size:14px}
label.sw{font-size:13px;color:var(--muted);display:inline-flex;gap:4px;align-items:center;cursor:pointer}
button.act{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:8px;padding:6px 12px;cursor:pointer;font-size:13px}
#status{font-size:12px;color:var(--muted);padding:6px 16px;display:flex;gap:14px;flex-wrap:wrap}
#status .err{color:#dc2626}
main{max-width:1100px;margin:0 auto;padding:8px 16px 40px}
.item{display:grid;grid-template-columns:72px 1fr;gap:12px;align-items:start;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px;margin-bottom:8px;text-decoration:none;color:inherit}
.item:hover{border-color:var(--muted)}
.item.seen{opacity:.55}
.item.ended{opacity:.4}.item.ended .t{text-decoration:line-through}
.item.new .t::before{content:"NEW";font-size:10px;font-weight:700;color:#fff;background:#dc2626;border-radius:4px;padding:1px 5px;margin-right:6px;vertical-align:middle}
.th{width:72px;height:72px;border-radius:8px;background:var(--bg);object-fit:cover;display:block}
.th.noimg{display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:11px}
.t{font-weight:600;margin:0 0 4px;word-break:break-all}
.meta{font-size:12px;color:var(--muted);display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.src{font-weight:700;padding:1px 7px;border-radius:4px;color:#fff;font-size:11px}
.price{font-weight:700;color:var(--fg)}
.hot{color:#f97316;font-weight:700}
.empty{text-align:center;color:var(--muted);padding:60px 0}
@media(max-width:520px){.item{grid-template-columns:56px 1fr}.th{width:56px;height:56px}}
</style></head><body>
<header>
 <h1>🔥 핫딜 통합</h1>
 <span id="chips"></span>
 <input type="search" id="q" placeholder="검색 (예: 모니터, SSD, 쿠팡)">
 <label class="sw"><input type="checkbox" id="hideEnded" checked>종료 숨기기</label>
 <label class="sw"><input type="checkbox" id="hideSeen">본 글 숨기기</label>
 <button class="act" id="refresh">새로고침</button>
 <button class="act" id="markAll" title="현재 목록을 모두 읽음 처리">모두 읽음</button>
</header>
<div id="status"></div>
<main id="list"><div class="empty">불러오는 중…</div></main>
<script>
const $=s=>document.querySelector(s);
const store=(k,v)=>{try{v===undefined?null:localStorage.setItem(k,JSON.stringify(v));return JSON.parse(localStorage.getItem(k))}catch(e){return null}};
let data={items:[],status:{}}, seen=new Set(store('seen')||[]), firstIds=null;
let enabled=new Set(store('enabled')||['quasarzone','ruliweb','arca']);
function fmtAgo(ts){if(!ts)return'';const m=Math.round((Date.now()/1000-ts)/60);if(m<1)return'방금';if(m<60)return m+'분 전';const h=Math.floor(m/60);if(h<24)return h+'시간 전';return Math.floor(h/24)+'일 전'}
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function renderChips(){$('#chips').innerHTML=Object.entries(data.status).map(([k,s])=>`<button class="chip ${enabled.has(k)?'on':''}" data-k="${k}"><span class="dot" style="background:${s.color}"></span>${s.name}<span class="n">${s.count}</span></button>`).join(' ');
 document.querySelectorAll('.chip').forEach(b=>b.onclick=()=>{const k=b.dataset.k;enabled.has(k)?enabled.delete(k):enabled.add(k);store('enabled',[...enabled]);render()})}
function render(){renderChips();
 const q=$('#q').value.trim().toLowerCase(), hideEnded=$('#hideEnded').checked, hideSeen=$('#hideSeen').checked;
 const rows=data.items.filter(it=>enabled.has(it.site)&&(!hideEnded||!it.ended)&&(!hideSeen||!seen.has(it.id))&&(!q||(it.title+' '+it.shop+' '+it.category).toLowerCase().includes(q)));
 $('#list').innerHTML=rows.length?rows.map(it=>{const s=data.status[it.site];const isNew=firstIds&&!firstIds.has(it.id);
  return `<a class="item ${seen.has(it.id)?'seen':''} ${it.ended?'ended':''} ${isNew?'new':''}" href="${esc(it.url)}" target="_blank" rel="noopener" data-id="${it.id}">
   ${it.thumb?`<img class="th" loading="lazy" src="${esc(it.thumb)}" referrerpolicy="no-referrer" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'th noimg',textContent:'no img'}))">`:`<div class="th noimg">${esc(s.name)}</div>`}
   <div><div class="t">${it.hot?'<span class="hot">🔥</span> ':''}${esc(it.title)}</div>
   <div class="meta"><span class="src" style="background:${s.color}">${esc(s.name)}</span>
   ${it.category?`<span>${esc(it.category)}</span>`:''}${it.shop?`<span>${esc(it.shop)}</span>`:''}
   ${it.price?`<span class="price">${esc(it.price)}원</span>`:''}${it.shipping?`<span>배송 ${esc(it.shipping)}</span>`:''}
   <span>💬 ${it.comments}</span><span>👍 ${it.likes}</span>${it.views?`<span>👁 ${esc(it.views)}</span>`:''}
   <span>${fmtAgo(it.ts)||esc(it.time_text)}</span></div></div></a>`}).join(''):'<div class="empty">표시할 글이 없습니다</div>';
 document.querySelectorAll('.item').forEach(a=>a.addEventListener('click',()=>{seen.add(a.dataset.id);store('seen',[...seen].slice(-3000));a.classList.add('seen')}));
 const st=Object.values(data.status).map(s=>s.error?`<span class="err">⚠ ${esc(s.name)}: ${esc(s.error)}</span>`:`<span>${esc(s.name)} ${s.count}건</span>`).join('');
 $('#status').innerHTML=st+`<span>갱신 ${new Date(data.generated*1000).toLocaleTimeString('ko-KR')}</span>`;}
async function load(force){try{$('#refresh').textContent='불러오는 중…';const r=await fetch('/api/deals'+(force?'?force=1':''));data=await r.json();
 if(firstIds===null){firstIds=new Set(store('lastIds')||[]);if(!firstIds.size)firstIds=null}
 store('lastIds',data.items.map(i=>i.id));render()}catch(e){$('#status').innerHTML='<span class="err">서버 연결 실패: '+esc(String(e))+'</span>'}finally{$('#refresh').textContent='새로고침'}}
$('#refresh').onclick=()=>load(true);$('#q').oninput=render;$('#hideEnded').onchange=render;$('#hideSeen').onchange=render;
$('#markAll').onclick=()=>{data.items.forEach(i=>seen.add(i.id));store('seen',[...seen].slice(-3000));render()};
load(false);setInterval(()=>load(true),5*60*1000);
</script></body></html>"""


def load_page():
    """같은 폴더의 docs/index.html(GitHub Pages와 공용)을 우선 사용. 없으면 내장 페이지."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "index.html")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().replace("deals.json", "/api/deals")
    except OSError:
        return _EMBEDDED_PAGE


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):   # 콘솔 조용히
        pass

    def _send(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/deals"):
            force = "force=1" in self.path
            self._send(json.dumps(collect(force), ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
        elif self.path == "/" or self.path.startswith("/?"):
            self._send(load_page().encode("utf-8"), "text/html; charset=utf-8")
        else:
            self.send_error(404)


# ----------------------------------------------------------------------------
# GitHub Pages로 결과 올리기 (github_token.txt 가 있을 때만 동작)
# ----------------------------------------------------------------------------
GITHUB_REPO = "kjm922/kjm-notes-7f3a"
GITHUB_PATH = "docs/deals.json"
PUSH_INTERVAL = 15 * 60


def _token():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "github_token.txt")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def github_push_once(token):
    import base64
    import scrape
    api = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_PATH}"
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
           "X-GitHub-Api-Version": "2022-11-28"}
    prev, sha = {"items": [], "status": {}}, None
    r = requests.get(api, headers=hdr, timeout=20)
    if r.status_code == 200:
        sha = r.json()["sha"]
        try:
            prev = json.loads(base64.b64decode(r.json()["content"]).decode("utf-8"))
        except (ValueError, KeyError):
            pass
    elif r.status_code not in (404,):
        raise RuntimeError(f"GitHub 읽기 실패 {r.status_code}: {r.text[:120]}")
    data = scrape.build(prev, collect(force=True))
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload = {"message": f"update deals {time.strftime('%Y-%m-%d %H:%M')}",
               "content": base64.b64encode(body).decode("ascii")}
    if sha:
        payload["sha"] = sha
    r = requests.put(api, headers=hdr, json=payload, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"GitHub 쓰기 실패 {r.status_code}: {r.text[:120]}")
    return {k: v["count"] for k, v in data["status"].items()}


def github_push_loop(token):
    while True:
        wait = PUSH_INTERVAL
        for attempt in range(3):                    # 일시적 네트워크 오류면 1분 뒤 재시도 (최대 3회)
            try:
                counts = github_push_once(token)
                print(f"[{time.strftime('%H:%M')}] GitHub 업로드 완료 {counts}")
                break
            except Exception as e:  # noqa: BLE001
                print(f"[{time.strftime('%H:%M')}] GitHub 업로드 실패: {e}")
                if attempt < 2:
                    time.sleep(60)
        time.sleep(wait)


def main():
    if "--push" in sys.argv:                       # 한 번만 올리고 종료
        print(github_push_once(_token() or sys.exit("github_token.txt 없음")))
        return
    if "--test" in sys.argv:                       # 파싱만 확인
        d = collect(True)
        for k, s in d["status"].items():
            print(f"[{s['name']}] {s['count']}건  {('오류: ' + s['error']) if s['error'] else 'OK'}")
        for it in d["items"][:15]:
            print(f"  {SITES[it['site']]['name']:<5} {it['time_text']:>8} {it['price']:>10} {it['title'][:60]}")
        return
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}/"
    print(f"핫딜 통합 뷰어 실행 중: {url}   (종료: Ctrl+C)")
    token = _token()
    if token:
        print(f"GitHub 업로드 켜짐: {PUSH_INTERVAL // 60}분마다 https://kjm922.github.io/{GITHUB_REPO.split('/')[1]}/ 갱신")
        threading.Thread(target=github_push_loop, args=(token,), daemon=True).start()
    else:
        print("github_token.txt 가 없어 GitHub 업로드는 꺼져 있습니다 (PC에서만 보기)")
    threading.Thread(target=lambda: (time.sleep(0.6), webbrowser.open(url)), daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
