# Experiment Log: 2026-04-07

> Raw Opus 성능을 더 싸게 달성할 수 있는가? 오케스트레이션, 모델 티어링, ensemble 실험.

## 1. 실험 목표

- **Baseline**: Opus raw $1.30, 14/20 (70%) on Noya (todo.md 기록)
- **목표**: 같은 recall을 더 싸게, 또는 같은 비용에서 더 높은 recall

---

## 2. Noya 단일 audit 실험 (2024-04-noya, 20 vulns, 173 .sol files)

### 2.1 Strategy 비교

| Strategy | Model | Recall | Cost | $/finding | 비고 |
|----------|-------|--------|------|-----------|------|
| **raw** | Opus | 14/20 (70%) | $1.30 | $0.09 | baseline (과거 기록) |
| **raw** | Sonnet | 4/20 (20%) | $0.82 | $0.20 | |
| **raw** | Haiku | 2/20 (10%) | $0.54 | $0.27 | |
| checklist | Opus | 7/20 (35%) | $4.36 | $0.62 | 구조 강제가 역효과 |
| tiered v1 | Sonnet→Opus | 0/20 (0%) | $1.04 | - | budget 부족 + 파싱 실패 |
| tiered v2 | Sonnet→Opus | 9/20 (45%) | $2.52 | $0.28 | budget 스케일링 적용 |

**결론**: Raw Opus가 압도적 Pareto optimal. 오케스트레이션은 비용만 증가.

### 2.2 Sonnet Ensemble 실험 (Non-determinism 활용 시도)

동일 프롬프트(Sonnet raw)를 4번 반복 실행 → union recall 측정.

| Run | Recall | Cost |
|-----|--------|------|
| r0 | 4/20 (20%) | $0.82 |
| r1 | 4/20 (20%) | $1.66 |
| r2 | 5/20 (25%) | $0.86 |
| r3 | 2/20 (10%) | $0.83 |
| **Union** | **7/20 (35%)** | **$4.17** |

Per-vuln breakdown (✓ = detected in that run):

| Vuln | r0 | r1 | r2 | r3 | Union | Category |
|------|----|----|----|----|-------|----------|
| H-06 | ✓ | ✓ | ✓ | | ✓ | tvl-accounting |
| H-08 | | | ✓ | | ✓ | access-control |
| H-11 | ✓ | ✓ | ✓ | ✓ | ✓ | tvl-accounting |
| H-13 | | | ✓ | | ✓ | tvl-accounting |
| H-15 | | ✓ | | | ✓ | tvl-accounting |
| H-18 | ✓ | | | | ✓ | state-management |
| H-19 | ✓ | ✓ | ✓ | ✓ | ✓ | tvl-accounting |
| (13 others) | | | | | | 4회 모두 미탐지 |

**결론**: Ensemble은 실패. 4회 union (35%) < Opus 1회 (70%), 비용 3.2배.
- Non-determinism은 "다른 버그를 찾는다"가 아니라 "같은 버그를 찾다 말다"
- 13개 vuln은 4회 전부 미탐지 → Sonnet의 추론 깊이 한계, 반복으로 극복 불가

---

## 3. Post-cutoff 5 audits 실험 (contamination-free)

Audits: panoptic (3 files), sequence (49), tempo-feeamm (1), tempo-mpp (1), tempo-stablecoin (2)
Total: 8 vulns

### 3.1 Model 비교 (raw strategy)

| Model | Recall | Cost | $/finding |
|-------|--------|------|-----------|
| Haiku | 2/8 (25%) | $0.65 | $0.33 |
| Sonnet | 6/8 (75%) | $3.50 | $0.58 |
| Opus | 5/8 (62.5%) | $3.64 | $0.73 |

### 3.2 Audit별 상세

| Audit (vulns) | Haiku | Sonnet | Opus |
|---------------|-------|--------|------|
| panoptic (2) | 0/2 | **2/2** | 0/2 ($0, 실행 실패) |
| sequence (2) | 0/2 | 0/2 | 1/2 |
| tempo-feeamm (1) | 1/1 | 1/1 | 1/1 |
| tempo-mpp (1) | 0/1 | 1/1 | 1/1 |
| tempo-stablecoin (2) | 1/2 | 2/2 | 2/2 |

### 3.3 토큰 사용량 비교

| Model | Total tokens | Cost | 토큰 단가 |
|-------|-------------|------|----------|
| Haiku | 2.18M | $0.65 | 최저 |
| Opus | 1.31M | $3.64 | 최고 |
| Sonnet | 1.39M | $3.50 | 중간 |

**결론**: Sonnet이 Opus를 이긴 것처럼 보이지만, 토큰 사용량이 비슷한 가격에서 Sonnet이 더 많은 토큰을 쓴 것.
같은 비용 → Sonnet이 더 많은 토큰 소비 가능 → 성능은 토큰 소비에 비례.
**Opus panoptic $0.00은 실행 에러** — 이걸 빼면 Opus 5/6 (83%).

---

## 4. 카테고리별 구조적 약점 분석

Opus의 모든 Noya 실행 데이터 (15회) 기반 카테고리별 탐지율:

| Category | Det% | 특성 |
|----------|------|------|
| **state-management** | 13% | 다중 트랜잭션 상태 변화 추적 |
| **logic** | 15% | 도메인 특화 비즈니스 로직 |
| **frontrunning** | 15% | 트랜잭션 순서 의존 공격 |
| **oracle-manipulation** | 20% | 가격 피드 단위/스탈링 |
| **dos** | 23% | 리버트 조건, 블록 가스 |
| **math** | 32% | 정밀도, 라운딩, 오버플로우 |
| tvl-accounting | 37% | 값 추적 (비교적 잘 잡힘) |
| signature-replay | 40% | |
| input-validation | 52% | |
| access-control | 55% | modifier 비교로 잡힘 |
| **reentrancy** | **100%** | 패턴 매칭으로 잡힘 |

### 약점의 본질

- **패턴 매칭으로 잡히는 것**: reentrancy, access-control → 높은 탐지율
- **추론이 필요한 것**: state-management, logic, oracle → 낮은 탐지율
- **외부 프로토콜 스펙 필요**: Pendle 관련 4개 전멸 (모든 모델, 모든 전략)
- **protocol-level**: 0% — 시스템 설계 결함은 아예 못 찾음

### Sonnet vs Opus 격차의 본질

Sonnet이 찾는 것: 코드 표면에 답이 보이는 버그 (TVL += borrow → 빼야 하는 거)
Opus만 찾는 것: "이 코드의 의도가 뭔지" 추론 후 구현과 비교 (oracle 단위, 상태 머신)
둘 다 못 찾는 것: 외부 프로토콜 지식 필요, 다중 트랜잭션 공격

---

## 5. 핵심 발견

### 5.1 오케스트레이션은 (아직) 안 먹힌다
- Checklist: 구조 강제가 모델 자유도를 제약 → recall 하락
- Tiered: 파일 필터링이 정보 손실 → cross-contract 버그 놓침
- Ensemble: 같은 추론 깊이에서 반복은 무의미 → 같은 쉬운 버그만 반복 탐지

### 5.2 성능 ≈ f(토큰 소비량)
- 같은 달러 예산이면 토큰을 더 쓴 모델이 이김
- 오케스트레이션 overhead는 순손실 — 같은 토큰을 raw 분석에 쓰는 게 나음
- 이건 강력한 negative result: "orchestration tax"가 "communication tax" (Tokenomics, AgentTaxo) 보다 더 나쁨

### 5.3 모델 능력 격차는 추론 깊이에서 발생
- 패턴 매칭 vs 의도 추론의 차이
- 이 격차는 ensemble/반복으로 극복 불가
- 프롬프트 힌트로 약점 카테고리를 보강하는 방향은 미검증 (boosted 실험 진행 중)

---

## 6. MiMo-V2-Flash Swarm 실험 (토큰 인해전술)

### 6.1 가설

MiMo-V2-Flash ($0.09/1M input)는 Opus보다 ~30배 싸다.
파일 파티셔닝 + skeleton context + multi-lens + prompt caching으로
토큰을 대량 투입하면 저가 모델로도 recall을 끌어올릴 수 있는가?

### 6.2 구현

- **OpenRouter API** 경유 MiMo-V2-Flash 호출 (`openrouter_client.py`)
- **파티셔닝**: .sol 파일을 8개씩 분할, 각 파티션에 나머지 파일의 skeleton(interface/signature) 첨부
- **Skeleton → system prompt**: 동일 audit 내 모든 call이 같은 system prompt 공유 → **prompt caching** 활용
- **Multi-lens**: value-flow / access-state / external-math 3개 렌즈로 분석 관점 분리
- **캐시 효과 확인**: 후반 파티션에서 50~93% cache hit, 비용 절반 이하

### 6.3 전략 비교 (post-cutoff 6 audits, 9 vulns)

| Strategy | Lenses | Recall | Cost | $/finding | Calls |
|----------|--------|--------|------|-----------|-------|
| **partitioned** | 1 (general) | **3/9 (33%)** | **$0.05** | **$0.017** | 21 |
| **full-swarm** | 3 (value/access/ext) | 2/9 (22%) | $0.14 | $0.070 | 75 |

### 6.4 Audit별 상세

| Audit (vulns) | Files | Partitions | Partitioned | Full-swarm |
|---------------|-------|------------|-------------|------------|
| blackhole (1) | 120 | 15 | 0/1, $0.037 | 0/1, $0.103 |
| panoptic (2) | 3 | 1 | 0/2, $0.002 | 0/2, $0.004 |
| sequence (2) | 49 | 7 | 0/2, $0.009 | 0/2, $0.028 |
| tempo-feeamm (1) | 1 | 1 | **1/1**, $0.0004 | **1/1**, $0.001 |
| tempo-mpp (1) | 1 | 1 | **1/1**, $0.0008 | 0/1, $0.002 |
| tempo-stablecoin (2) | 2 | 1 | **1/2**, $0.0006 | **1/2**, $0.002 |

### 6.5 캐시 효과 분석

blackhole (120 files, 15 partitions) 기준:

| 파티션 구간 | 평균 cache hit | 평균 비용/call | 평균 응답시간 |
|------------|---------------|---------------|-------------|
| p0~p3 (초반) | 0~40% | $0.004 | 30~160s |
| p7~p10 (중반) | 91~93% | $0.001 | 5~9s |
| p12~p14 (후반) | 71~93% | $0.001 | 7~8s |

**캐시가 안정적으로 동작**: 동일 system prompt(skeleton)가 반복되면서 2번째 call부터 cache hit.
비용 절감: 캐시 히트 시 input 토큰 비용 50% 할인 ($0.09 → $0.045/1M).

### 6.6 핵심 결론

1. **Partitioned > Full-swarm**: 렌즈를 나누면 각 렌즈의 시야가 좁아져 general에서 잡던 것도 놓침. 렌즈 추가가 역효과.
2. **$/finding 최강**: MiMo partitioned $0.017/finding — Opus($0.73)의 **43배**, Haiku($0.33)의 **19배** 저렴.
3. **큰 audit 전멸**: 120파일(blackhole), 49파일(sequence), 3파일(panoptic) 모두 0%. 1~2파일 audit만 탐지.
4. **Token quantity ≠ reasoning depth**: 45 calls × 3렌즈 = 75 API 호출을 쏟아부어도 cross-contract/logic 버그는 못 찾음.
5. **Prompt caching은 유효**: 비용 절감 기법으로서 캐싱은 잘 동작. 논문 실험의 독립적 contribution 가능.

### 6.7 전체 모델 비교 (post-cutoff)

| 설정 | Recall | Cost | $/finding | 특성 |
|------|--------|------|-----------|------|
| MiMo partitioned | 3/9 (33%) | $0.05 | $0.017 | 패턴 매칭만 |
| MiMo full-swarm | 2/9 (22%) | $0.14 | $0.070 | 렌즈가 역효과 |
| Haiku raw | 2/8 (25%) | $0.65 | $0.33 | |
| Opus raw | 5/8 (62%) | $3.64 | $0.73 | |
| Sonnet raw | 6/8 (75%) | $3.50 | $0.58 | |

> **"cheap model flooding의 한계: token quantity cannot substitute for reasoning depth"**
> MiMo는 코드 표면의 패턴(1~2파일 단순 버그)은 극저가로 탐지하지만,
> 추론이 필요한 버그(cross-contract, state machine, oracle)는 call 수를 늘려도 못 찾는다.
> Sonnet ensemble과 동일한 패턴: 약한 추론을 반복하면 같은 쉬운 버그만 반복 탐지.

---

## 7. 진행 중 / 다음 단계

### 진행 중
- [ ] `opus-boosted-postcut`: post-cutoff 5 audits에서 약점 카테고리 힌트 프롬프트 테스트

### 다음 단계
- [ ] Boosted 결과 분석 → 힌트가 recall 개선하는지 확인
- [ ] Boosted가 효과 있으면: pre-cutoff에서도 검증 (overfitting 방어)
- [ ] 실행 시간 최적화 (작은 audit 우선 실행, sequence 분리)
- [ ] 연구 방향 결정: negative result 논문 vs 돌파구 탐색 계속
- [ ] 멘토 논의: 현재 데이터로 어디까지 주장 가능한지

---

## 7. 실험 파일 참조

모든 결과: `benchmark/results/evmbench/skill_run_20260407_*.json`

| 파일 | Label | Model | Target | Recall | Cost |
|------|-------|-------|--------|--------|------|
| `_144941.json` | tiered v1 | Opus | Noya | 0/20 | $1.04 |
| `_145520.json` | checklist | Opus | Noya | 7/20 | $4.36 |
| `_150337.json` | tiered-v2 | Opus | Noya | 9/20 | $2.52 |
| `_151007.json` | haiku-raw | Haiku | Noya | 2/20 | $0.54 |
| `_151212.json` | sonnet-raw-r0 | Sonnet | Noya | 4/20 | $0.82 |
| `_152109.json` | haiku-raw-postcut | Haiku | post-cutoff | 2/8 | $0.65 |
| `_154921.json` | sonnet-raw-postcut | Sonnet | post-cutoff | 6/8 | $3.50 |
| `_155546.json` | opus-raw-postcut | Opus | post-cutoff | 5/8 | $3.64 |
| `_160647.json` | sonnet-raw-r3 | Sonnet | Noya | 2/20 | $0.83 |
| `_160726.json` | sonnet-raw-r1 | Sonnet | Noya | 4/20 | $1.66 |
| `_160728.json` | sonnet-raw-r2 | Sonnet | Noya | 5/20 | $0.86 |

Swarm 결과: `benchmark/results/evmbench/swarm_20260407_*.json`

| 파일 | Label | Model | Target | Recall | Cost |
|------|-------|-------|--------|--------|------|
| `swarm_..._204327.json` | mimo-partitioned-postcut | MiMo-V2-Flash | post-cutoff | 3/9 | $0.05 |
| `swarm_..._211015.json` | mimo-fullswarm-postcut | MiMo-V2-Flash | post-cutoff | 2/9 | $0.14 |
