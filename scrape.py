# -*- coding: utf-8 -*-
"""
GitHub Actions용 수집 스크립트: 3개 사이트를 긁어 docs/deals.json 으로 저장.
어떤 사이트가 실패하면 그 사이트는 이전 결과를 그대로 유지(stale 표시)해서 페이지가 비지 않게 한다.
"""
import json
import os
import time

import hotdeal

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "deals.json")
MAX_PER_SITE = 60


def main():
    prev = {"items": [], "status": {}}
    try:
        with open(OUT, encoding="utf-8") as f:
            prev = json.load(f)
    except (OSError, ValueError):
        pass
    prev_items = {}
    for it in prev.get("items", []):
        prev_items.setdefault(it["site"], []).append(it)

    data = hotdeal.collect(force=True)
    items = []
    for key, st in data["status"].items():
        if st["error"]:
            kept = prev_items.get(key, [])
            old = prev.get("status", {}).get(key, {})
            st.update({"count": len(kept), "stale": True, "at": old.get("at", 0)})
            items.extend(kept)
            print(f"[{st['name']}] 실패 → 이전 {len(kept)}건 유지. {st['error']}")
        else:
            st["stale"] = False
            items.extend([it for it in data["items"] if it["site"] == key][:MAX_PER_SITE])
            print(f"[{st['name']}] {st['count']}건")
    items.sort(key=lambda x: x["ts"], reverse=True)
    data["items"] = items
    data["generated"] = time.time()

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"저장: {OUT} ({len(items)}건)")


if __name__ == "__main__":
    main()
