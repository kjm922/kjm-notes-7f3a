# 🔥 핫딜 통합

퀘이사존 · 루리웹 · 아카라이브 핫딜 게시판을 한 페이지로 모아 봅니다.

## 1. PC에서 바로 보기
`핫딜통합_실행.bat` 더블클릭 → http://127.0.0.1:8765 가 열립니다. (PC 안에서만 접속 가능)

## 2. 어디서나 보기 (GitHub Pages) — 최초 1회 설정
1. GitHub에서 새 저장소를 만듭니다. 이름: `hotdeal` (Public)
2. 이 폴더에서 명령 프롬프트(cmd)를 열고:
   ```
   git init
   git add .
   git commit -m "hotdeal"
   git branch -M main
   git remote add origin https://github.com/<내아이디>/hotdeal.git
   git push -u origin main
   ```
3. 저장소 **Settings → Pages** → Source: `Deploy from a branch`, Branch: `main` / `/docs` → Save
4. 저장소 **Actions** 탭 → 왼쪽 "핫딜 수집" → **Run workflow** 로 한 번 수동 실행
5. 1~2분 뒤 `https://<내아이디>.github.io/hotdeal/` 접속. 이후 15분마다 자동 갱신됩니다.

휴대폰에서는 브라우저 메뉴의 "홈 화면에 추가"로 앱처럼 쓸 수 있습니다.

## 파일
- `hotdeal.py` — 수집 파서 + 로컬 서버
- `scrape.py` — GitHub Actions에서 실행, `docs/deals.json` 생성
- `docs/index.html` — 화면 (로컬 서버와 GitHub Pages 공용)
- `.github/workflows/scrape.yml` — 15분마다 수집 스케줄
