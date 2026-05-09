# CLAUDE.md — Smart Money Screener

이 파일은 AI(Claude)가 이 프로젝트에서 작업할 때 참고할 가이드입니다.

## 프로젝트 개요

스마트머니(외국인·기관)의 매집과 출발을 추적해 **단타**와 **텐버거** 후보를 동시에 발굴하는 수급 매매 스크리너.

상세 알고리즘은 `ALGORITHM.md` 참조.

## 두 전략

| 구분 | 매집 기간 | 결판 |
|------|-----------|------|
| ⚡ 단타 | 며칠~수 주 | 7일 내 폭발 |
| 💎 텐버거 | 1~2년 | 수개월~수년 보유 |
| ⭐ 황금자리 | 두 신호 동시 점등 | — |

## 기술 스택

- **백엔드**: Flask + APScheduler, Render.com 배포 (GitHub main 브랜치 auto-deploy)
- **프론트엔드**: 단일 `index.html`, GitHub Pages (gh-pages 브랜치)
- **데이터**: pykrx
- **저장소**: SQLite

## 배포 방법

신고가 스크리너와 동일한 흐름:

1. `git add . && git commit -m "..." && git push origin main` (Render 자동 배포)
2. `git checkout gh-pages && git checkout main -- frontend/index.html && git commit && git push origin gh-pages`
3. GitHub Pages 반영 3~5분, 모바일은 시크릿 탭으로 캐시 우회

## 작업 원칙

1. **이론 → 사례 → 알고리즘 → 코드 순서**. 코드부터 짜지 말 것.
2. **백테스트 통과 못하면 이론·사례 단계로 회귀**. 코드 더 만지지 말 것.
3. **실패 사례를 더 많이 본다**. False positive 회피가 진짜 알파.
4. 모바일 레이아웃은 **flex-wrap** (탭 줄바꿈), 가로 스크롤 NO. (신고가 스크리너 사용자 선호)

## 폴더 구조

```
smart-money-screener/
├── ALGORITHM.md       # 알고리즘 명세
├── research/
│   ├── theory/        # 대가별 이론 압축본
│   └── cases/
│       ├── tenbagger/
│       ├── short_term/
│       └── failures/  # 실패 사례 (가장 중요)
├── src/
│   ├── data/          # pykrx 데이터 수집
│   ├── analysis/      # 수급 점수, 매집 단계 분석
│   ├── signals/       # 단타/텐버거 출발 신호
│   ├── classifier/    # 분류 로직
│   └── server.py      # Flask + SSE
├── backtest/
└── frontend/
    └── index.html
```

## 백테스트 기준점

이걸 통과해야 운영 시작:

- **단타**: 승률 55%+, 평균 수익률 양수 (5일 보유)
- **텐버거**: 출발 신호 종목 중 50%+ 상승 비율 30%+ (6개월 보유)
