# Lessons Learned: LLM-Based Smart Contract Vulnerability Detection

> 2026-04-08~09 실험 기록. .HACK Conference 2026 프롬프트 엔지니어링 패턴 적용 → EVMBench 벤치마크 검증 → 반복 실험에서 얻은 교훈.

---

## 1. 핵심 결론

**프롬프트 엔지니어링은 LLM의 attention을 재분배할 뿐, 능력을 증폭하지 못한다.**

모든 전략의 $/finding이 ~$0.78로 수렴. skeleton, pattern RAG, LLM 경고, 유형별 분화 등 어떤 기법을 적용해도 Sonnet의 recall ceiling(~30%)은 변하지 않았다.

---

## 2. 전략별 실험 결과 (Hard Audits, 9 audits, 39 vulns)

| 전략 | 개념 | Recall | Precision | Cost | $/finding |
|------|------|--------|-----------|------|-----------|
| **S-raw** | 아무것도 안 넣고 "다 읽어라" | **31%** | 42% | $6.14 | $0.77 |
| S-lean (LLM 경고) | raw + "확실하지 않으면 스킵" | 24% | 100% | $5.76 | - |
| S-lean-v2 (output 압축) | lean + "5문장 이내" | 7% | - | $4.34 | - |
| S-lean-v4 (skeleton context) | 아키텍처 맵 + full read | 23% | 75% | $4.65 | $0.78 |
| S-lean-v5 (skeleton + RAG) | skeleton + 757패턴 체크리스트 | 27% | 78% | $5.55 | $0.79 |
| S-lean-v6 (raw + RAG only) | 패턴 체크리스트만 | **5%** | - | $5.79 | - |
| S-v7 (multi-pass partition) | 30파일씩 나눠서 깊게 | TBD | TBD | TBD | TBD |

---

## 3. 무엇이 효과가 있었는가

### 3.1 Precision 개선은 가능하다
- LLM 경고 6줄 추가 → raw의 FP 17개를 0으로 줄임 (secondswap)
- skeleton context → precision 42%→75%
- **하지만 precision을 올릴 때마다 recall이 떨어짐** — trade-off는 피할 수 없다

### 3.2 비용 절감은 가능하다
- skeleton: input 토큰 ~5x 압축
- output 압축 지시: 출력 토큰 절감
- budget cap: 과다 지출 방지
- **하지만 비용을 줄이면 recall도 같이 줄어든다** — 성능 ≈ f(토큰)

### 3.3 Raw가 가장 좋다
- 모든 전략을 이긴 것은 **아무것도 안 넣은 raw 프롬프트**
- 프롬프트에 뭔가를 추가할수록 모델의 자유 분석 토큰이 줄어든다
- "이걸 찾아라"가 "이것만 찾아라"로 해석됨

---

## 4. 무엇이 효과가 없었는가

### 4.1 .HACK Conference 8가지 패턴 — 전부 실패 또는 무의미
| 패턴 | 결과 | 원인 |
|------|------|------|
| CVE diff 축적 | recall 변화 없음 | 예시에 anchoring되어 다른 유형 놓침 |
| Negative example | recall ↓ | "보고하지 마라" → 소극적 |
| 분석 프로세스 명시 | recall ↓↓ | 프로세스 따르느라 분석 토큰 부족 |
| 유형별 분화 | recall 변화 없음 | focus가 좁아져 cross-cutting 놓침 |
| Multi-Agent 검증 | 비용만 증가 | 검증 토큰이 순손실 |
| LLM 실패 모드 경고 | precision ↑, recall ↓ | 모델이 소극적으로 변함 |
| 취약점 정의 진화 | 미검증 | - |
| Vulnerable > Fixed 페어 | recall 변화 없음 | anchoring 효과 |

### 4.2 Skeleton이 recall을 해친다
- skeleton을 보여주면 모델이 "이미 구조를 알겠다" → 파일 안 읽음
- skeleton에서 "안전해 보이는" 함수는 body를 안 읽고 넘김
- **skeleton의 유일한 가치: RAG 검색용 키워드 추출 (LLM에 보내지 않는 것)**

### 4.3 Pattern RAG — 체크리스트가 자유 분석을 억제
- 757개 패턴 DB에서 top-10 매칭 → 체크리스트로 전달
- v6 (raw + 패턴만): recall 31%→5% — 재앙
- 모델이 "이 패턴을 찾아라" = "이것만 찾아라"로 해석
- 자유롭게 코드를 분석하는 게 체크리스트를 따르는 것보다 나음

### 4.4 Budget cap이 recall을 죽인다
- lean-v2 ($0.50/audit cap): 중형 audit에서 파일 다 읽기 전에 끊김
- 9개 중 8개가 0/N
- **budget cap은 소형 audit에서만 유효, 중형 이상에서는 자살**

---

## 5. 모델 능력 격차의 본질

### 5.1 Sonnet vs Opus
| 특성 | Sonnet | Opus |
|------|--------|------|
| 패턴 매칭 (reentrancy, access control) | 잘 찾음 | 잘 찾음 |
| 의도 추론 (business logic, state machine) | 못 찾음 | 찾음 |
| Cross-contract 추적 | 얕음 | 깊음 |
| 토큰 단가 | $3/M | $15/M (5x) |

### 5.2 못 찾는 버그의 특성
- Easy + single-file인데도 못 찾는 경우가 많음 (50+ 인간 auditor가 찾은 것)
- 원인: 파일을 "읽었지만 분석하지 않음" — 함수 이름/NatSpec만 보고 넘김
- reentrancy (easy, phi H-06): CEI 위반인데 못 찾음 — 패턴 매칭조차 실패하는 경우 있음
- access control (easy, virtuals H-03): public 함수인데 위험으로 인식 못 함

### 5.3 Sonnet이 유일하게 이기는 경우
- post-cutoff 소형 audit (1-5파일): Sonnet 75%, Opus 62% (단, Opus 실행 에러 포함)
- 파일이 적으면 전부 깊게 읽을 수 있어서 Sonnet의 약점이 안 드러남
- **Sonnet의 sweet spot: 1-20파일 소규모 audit**

---

## 6. 비용 효율 분석

### 6.1 $/finding은 전략에 무관하게 수렴
모든 Sonnet 전략이 ~$0.77-0.79/finding으로 수렴. 프롬프트 엔지니어링은 "무엇을 찾느냐"를 바꿀 뿐 "얼마나 찾느냐"는 바꾸지 못함.

### 6.2 Opus raw가 가성비 최고
- Opus: 6/15 (40%), $3.16, precision 100%, $/finding $0.53
- Sonnet: 4/15 (27%), $1.21, precision 19%, $/finding $0.30
- **$/finding은 Sonnet이 싸지만, precision 포함하면 Opus가 나음**
- Opus는 보고한 게 전부 진짜 → 사람 리뷰 비용 0

### 6.3 Non-determinism이 크다
- 같은 전략, 같은 모델로 돌려도 매 run마다 다른 버그를 찾음
- secondswap raw: 첫 run 0/3 + 17 FP, 두번째 run 3/3 + 0 FP
- **단일 run 비교는 신뢰도가 낮음 — 3회 이상 반복 필요**

---

## 7. 기술적 인프라 교훈

### 7.1 벤치마크에 precision 필수
- recall만 측정하면 FP 17개짜리 전략이 "좋아 보임"
- precision + F1 추가 후 진짜 비교가 가능해짐
- **count_model_findings()** regex로 보고된 finding 수 카운트

### 7.2 실행 안정성
- `claude -p` 병렬 호출 → $0.00 에러 빈번
- 빈 소스 디렉토리 → judge 에러로 전체 중단
- **judge 에러 graceful fallback** + **소스 검증** 필수
- 순차 실행이 느리지만 안정적

### 7.3 tree-sitter Solidity 파싱
- `abch-tree-sitter-solidity` + `get_parser()` API
- skeleton 추출: function signature + state vars, body → `{ ... }`
- ~5x 토큰 절감 (skeleton용), 하지만 LLM에 보내면 역효과

### 7.4 Pattern DB 구축
- DeFiHackLabs 726 PoC → Haiku 배치 요약 → 757 패턴
- 카테고리: business-logic(124), oracle-price(88), access-control(73), reentrancy(40)
- keyword 기반 검색은 너무 generic — semantic search 필요

---

## 8. Immunefi/Audit Competition 실전 교훈

### 8.1 ROI 기반 타겟 선정
- 소규모 (≤20 files): Sonnet으로 충분, $0.30-0.50/audit
- 중규모 (20-50 files): Opus raw 권장, $1.00/audit
- 대규모 (50+ files): LLM 단독 비효율, 정적분석 병행 필요
- **heavily audited 프로토콜 (WONTFIX 20+건): 포기**

### 8.2 Early Exit
- 3회 연속 GO 없으면 중단 → 비용 낭비 방지
- cost cap은 소형에서만, 중형 이상에서는 recall 사망

### 8.3 실전 워크플로우 제안
```
1. recon (Python, 0 토큰): 파일 수, 크기, audit 이력 확인
2. 소규모 → Sonnet raw 1회
3. 중규모 → Opus raw 1회
4. 대규모 → Slither + Opus raw (정적분석이 easy cover)
5. 결과 리뷰: precision 확인, FP 필터링
6. 유망한 finding만 PoC 작성
```

---

## 9. 연구 방향

### 9.1 확정된 Negative Results (논문 기여)
- 프롬프트 엔지니어링 6가지 기법이 recall ceiling을 돌파하지 못함
- $/finding이 전략에 무관하게 수렴 (~$0.78)
- skeleton context가 recall을 해침 (정보 제공이 오히려 방해)
- pattern RAG 체크리스트가 자유 분석을 억제

### 9.2 미검증 방향 (추후 실험)
- **Multi-pass partition** (v7): 파일을 나눠서 깊게 읽기 → Opus 가격 이하에서 recall↑?
- **Slither + LLM hybrid**: 정적분석이 easy를 커버, LLM은 logic에 집중
- **Opus multi-run union**: Opus 3회 반복 → union recall?
- **Fine-tuning**: 과거 audit finding으로 모델 자체를 학습 (프롬프트가 아닌 가중치 변경)

### 9.3 논문 포지셔닝
> "Prompt Engineering as Attention Redistribution: 프롬프트 엔지니어링은 LLM의 취약점 탐지 능력을 증폭하지 못하며, attention을 재분배할 뿐이다. 6가지 기법 (CVE few-shot, failure warnings, type rotation, skeleton context, pattern RAG, output compression) 모두 recall ceiling을 돌파하지 못했고, $/finding은 전략에 무관하게 수렴했다."

---

## 10. 데이터 참조

- 벤치마크 결과: `benchmark/results/evmbench/skill_run_20260408_*.json`, `20260409_*.json`
- Pattern DB: `patterns/vulnerability_patterns_full.json` (757 패턴)
- PoC 인덱스: `patterns/poc_index.json` (726 PoC)
- 이전 실험 로그: `docs/experiment-log-20260407.md`
- 코드: `benchmark/evmbench_skill_runner.py` (전략 v1-v7)
