# -*- coding: utf-8 -*-
"""Minimal public listing reader; no images, bodies, authentication or block bypass."""
import json, re, time, threading, webbrowser, secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import requests
from bs4 import BeautifulSoup
KST = timezone(timedelta(hours=9))
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
 'quasarzone': ('퀘이사존', 'https://quasarzone.com/bbs/qb_saleinfo', parse_quasarzone),
 'ruliweb': ('루리웹', 'https://bbs.ruliweb.com/market/board/1020', parse_ruliweb),
 'arca': ('아카라이브', 'https://arca.live/b/hotdeal', parse_arca),
 'ppomppu': ('뽐뿌', 'https://m.ppomppu.co.kr/new/bbs_list.php?id=ppomppu', parse_ppomppu),
 'clien': ('클리앙', 'https://www.clien.net/service/board/jirum', parse_clien),
}
AGENT = 'PersonalDealReader/1.0'
INTERVAL = 30 * 60
TTL = 60 * 60
LIMIT = 15
PORT = 8765
TOKEN = secrets.token_urlsafe(32)
state = {}
lock = threading.Lock()

def read_public(url, max_bytes=2_000_000):
    # No cookies, credentials, proxy environment, redirect following, or retries.
    with requests.Session() as session:
        session.trust_env = False
        with session.get(url, headers={'User-Agent': AGENT, 'Accept': 'text/html,text/plain'},
                         timeout=(5, 12), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise ValueError(f'HTTP {response.status_code}: 수집 중단')
            if not any(t in response.headers.get('Content-Type', '').lower()
                       for t in ('text/html', 'text/plain')):
                raise ValueError('예상하지 못한 응답: 수집 중단')
            chunks, size = [], 0
            for chunk in response.iter_content(16384):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError('응답 크기 제한: 수집 중단')
                chunks.append(chunk)
            response._content = b''.join(chunks)
            if not response.encoding or response.encoding.lower() == 'iso-8859-1':
                response.encoding = response.apparent_encoding
            return response.text

def collect_site(key):
    name, url, parser = SITES[key]
    with lock:
        now = time.monotonic()
        st = state.setdefault(key, {'at': None, 'items': [], 'blocked': False, 'error': ''})
        if st['at'] is not None and now - st['at'] >= TTL:
            st['items'] = []
        if st['blocked']:
            return {'name': name, 'items': [], 'error': st['error']}
        if st['at'] is not None and now - st['at'] < INTERVAL:
            return {'name': name, 'items': st['items'], 'error': '30분 간격 제한: 기존 결과 표시'}
        st['at'] = now
        try:
            origin = '{0.scheme}://{0.netloc}'.format(urlsplit(url))
            robot = RobotFileParser()
            robot.parse(read_public(origin + '/robots.txt', 256_000).splitlines())
            if not robot.can_fetch(AGENT, url):
                raise ValueError('robots.txt에서 허용되지 않은 경로')
            delay = max(robot.crawl_delay(AGENT) or 0, robot.crawl_delay('*') or 0)
            if delay > INTERVAL:
                raise ValueError('사이트 요청 간격 정책: 수집 중단')
            html = read_public(url)
            if re.search(r'cf-chl|challenge-platform|Just a moment|captcha|접근이 차단|access denied', html, re.I):
                raise ValueError('인증 또는 차단 화면 감지')
            parsed = parser(html, datetime.now(KST))
            if not parsed:
                raise ValueError('공개 목록 확인 실패')
            items = []
            for item in parsed[:LIMIT]:
                link = urlsplit(item.get('url', ''))
                if link.scheme != 'https' or not (link.hostname == urlsplit(url).hostname or
                    (key == 'ppomppu' and link.hostname == 'www.ppomppu.co.kr')):
                    continue
                items.append({field: str(item.get(field, ''))[:250] for field in
                              ('title', 'price', 'shop', 'shipping', 'url')})
            st['items'], st['error'] = items, ''
        except Exception as exc:
            st['items'], st['blocked'], st['error'] = [], True, str(exc)[:180]
        return {'name': name, 'items': st['items'], 'error': st['error']}

