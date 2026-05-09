# Smart Money Screener

스마트머니(외국인·기관)의 매집과 출발을 추적하여, **단타**와 **텐버거** 후보 종목을 동시에 발굴하는 수급 매매 스크리너.

## 🚀 원클릭 배포

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/vipasset1004-lucky/smart-money-screener)

## 핵심 철학

> 스마트머니가 매집을 끝내고 **출발**한 종목을 잡는다.
>
> 시간 스케일만 다를 뿐, 본질은 동일.
> 매집 → 출발 → 상승 패턴을 두 시간축으로 탐지하고,
> 마지막 단계에서 **단타 / 텐버거 / 황금자리(둘 다)**로 분기.

## 두 전략

| 구분 | 매집 기간 | 결판 |
|------|-----------|------|
| ⚡ **단타** | 며칠~수 주 | 7일 내 폭발 |
| 💎 **텐버거** | 1~2년 (250~500 거래일) | 수개월~수년 보유 |
| ⭐ **황금자리** | 두 신호 동시 점등 | 진입 짧게, 보유 길게 |

## 기술 스택

- **백엔드**: Flask + APScheduler (Render.com 배포)
- **프론트엔드**: 단일 `index.html` (GitHub Pages 배포)
- **데이터**: pykrx (외국인/기관 수급, OHLCV)
- **저장소**: SQLite

## 프로젝트 구조

```
smart-money-screener/
├── ALGORITHM.md       # 알고리즘 명세
├── research/          # 이론·사례 연구
│   ├── theory/        # 대가들의 이론 정리
│   └── cases/         # 실제 종목 사례
│       ├── tenbagger/
│       ├── short_term/
│       └── failures/  # 실패 사례 (가장 중요)
├── src/               # 분석·신호·분류 엔진
├── backtest/          # 백테스트
└── frontend/          # 단일 HTML 프론트
```

## 자세한 내용

- 알고리즘 명세: [ALGORITHM.md](ALGORITHM.md)
- AI 협업 가이드: [CLAUDE.md](CLAUDE.md)
