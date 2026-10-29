# K2 KPI Board

8대 공정(포토 · 식각 · 증착 · 확산 · 이온주입 · 금속배선 · CMP · 세정) 그룹 공통 핵심 KPI 대시보드.
상단 KPI 카드로 현황을 한눈에 보고, 카드를 선택하면 같은 페이지 아래에 연결 데이터(추이 · 구성 · 설비 랭킹 · 히트맵 · 실시간 이벤트 · 공정 비교 · 자동 분석)가 열린다.

![전체 · 설비 정지로스](docs/screenshot-all-downloss.png)

## KPI

| KPI | 정의 | 목표 | 원천 |
|-----|------|------|------|
| 설비 정지로스 | 비계획 정지시간 ÷ 캘린더 시간 | ≤ 3.5% | EES · MES · PMS |
| 가동률 | Run 시간 ÷ 캘린더 시간 | 85% | EES · MES |
| PM 자동화율 | 자동 수행 PM 항목 ÷ 전체 (주간) | 75% | PMS |
| MOVE량 | 최근 24h Track-out Step 수 | 공정별 일 목표 | MES |
| 병목설비 | 현재 대기 WIP 최대 설비 | 1,000매 | MES WIP · EES |
| 생산량 | 최근 24h Out 웨이퍼 | 공정별 일 목표 | MES |
| TPSS | TPSS 설비별 지수 평균 | 92 | TPSS |
| 재작업률 | Rework 웨이퍼 ÷ 처리 웨이퍼 | ≤ 1.5% | MES · 계측 |

판정: 목표 충족 = 달성, 목표의 95% 이상 = 주의, 그 외 = 미달.

## 실행

```bash
pip install -r requirements.txt
python app.py            # http://localhost:8000
pytest -q                # API 테스트
```

- 외부 CDN 없이 동작 (폰트 로컬 포함, 차트는 SVG 직접 렌더링) — 사내망 배포 가능
- 공유 링크: `/#process=photo&kpi=downloss` 형태로 공정 · KPI 상태가 URL에 저장됨
- 상세 화면의 `Excel(CSV) 내보내기`로 추이 · 랭킹 · 이벤트 표를 내려받을 수 있음

## 구조

```
app.py            FastAPI: 페이지, REST API, WebSocket(구독형 실시간 push)
data_source.py    KPIProvider 인터페이스 + SimulatedProvider(데모 데이터)
templates/        index.html
static/           css / js / fonts
tests/            API · 데이터 일관성 테스트
mockup/           최초 디자인 시안
```

### API

| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/processes` | 공정 목록 + 공정별 미달/주의 KPI 수 |
| GET | `/api/kpis?process=all` | KPI 카드 8종 |
| GET | `/api/kpis/{kpi}?process=all` | KPI 상세 (stats, trend, breakdown, ranking, heatmap, events, compare, insights) |
| GET | `/api/sources` | 연결 데이터 소스 상태 |
| WS | `/ws` | `{"type":"subscribe","process":"photo","kpi":"downloss"}` 구독 → `kpi_update` 주기 push |

`process`: `all`, `photo`, `etch`, `depo`, `diff`, `imp`, `metal`, `cmp`, `clean`
`kpi`: `downloss`, `util`, `pm_auto`, `move`, `bottleneck`, `output`, `tpss`, `rework`

## 실데이터 연동

현재는 **시뮬레이션 데이터**(헤더에 `DEMO 데이터` 표시)로 동작한다. 정지 이벤트를 원천으로
정지로스 → 가동률 → MOVE → 생산량 · 대기 WIP · TPSS 를 파생시켜 화면의 숫자가 서로 맞도록 만들었다.

실데이터 연동 시:
1. `data_source.py`의 `KPIProvider`를 구현한 클래스 작성 (`processes` / `cards` / `detail` / `tick` / `sources`)
   - 반환 JSON 구조는 `SimulatedProvider`와 동일하게 맞추면 프론트엔드 수정 불필요
   - `tick()`에서 MES / EES / PMS / TPSS 폴링 또는 스트림 반영
2. `get_provider()`에 등록 후 `KPI_PROVIDER=<이름>` 환경 변수로 선택
3. 갱신 주기: `KPI_TICK_SEC` (기본 3초)

TPSS 지표 정의와 KPI 목표값(`KPIS` 목록)은 실제 기준에 맞게 조정 필요.
