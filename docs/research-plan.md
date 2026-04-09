# Research Plan: Runtime Architecture and Cost-Effective Agent Orchestration for Smart Contract Security

> 2026-04-06 업데이트. 실험 결과 기반 방향 전환 반영.

## One-Line Thesis

**API 기반 agent orchestration에서 runtime architecture(single-call vs tool-use loop)가 cost-effectiveness를 구조적으로 결정하며, orchestrator-centric single-call이 recall을 유지하면서 비용을 3-5× 절감한다.**

---

## 1. 논문 스토리

### 문제 제기
- LLM agent orchestration 연구는 recall만 추구, 비용 분석 부재
- Multi-agent tool-use loop은 매 턴 전체 history 재전송 → O(N²) input token 누적
- 기존 연구(Tokenomics, AgentTaxo)는 "communication tax"를 측정했지만, 근본 원인인 runtime architecture 차이를 다루지 않음

### 우리의 주장
1. **Runtime architecture가 cost를 결정**: CLI(파일시스템 접근) vs API(token-based tool-use)에 따라 같은 orchestration도 비용이 5-26× 차이
2. **Prompt cache + single-call이 최적**: API 환경에서는 tool-use loop 대신 Python orchestrator가 코드를 선별하고 LLM은 single-call로 분석하는 것이 cost-effective
3. **Negative result의 가치**: Agentic tool-use가 API 환경에서 cost-ineffective한 이유를 구조적으로 설명

### 예상 Figure 1: Scaffold별 Cost-Recall Tradeoff
```
Recall (%)
 70 |    * Orch+cache        * Agentic v2 (pre-opt)
 60 |  * Raw              * Agentic v2 (post-opt)
 50 |
 40 |
    +----+----+----+----+----→ Cost ($)
    $0  $0.5  $1   $2   $4
    
    핵심: Orch+cache가 Pareto optimal (recall↑ + cost 중간)
    Agentic v2는 recall 동등하지만 비용 3-5× → Pareto dominated
```

---

## 2. 벤치마크 전략

### EVMBench 그대로 사용 + 태깅 보강

새 벤치마크를 만들지 않는다. EVMBench (40 audits, 120 vulns, detect/patch/exploit)를 그대로 사용하되:

**필수 추가 사항:**
1. **지식 컷오프 분리 보고** — pre-cutoff (35 audits, 109 vulns) vs post-cutoff (5 audits, 11 vulns) 성능을 반드시 나눠서 보고. Pre-cutoff 결과는 contamination caveat 명시.
2. **태깅 추가** — EVMBench의 각 finding에 메타데이터 부여:
   - `locality`: single-file / cross-contract / protocol-level
   - `scale`: small / medium / large (코드베이스 .sol 파일 수 기준)
   - `difficulty`: dup 수 기반 (hard / medium / easy)
   - `category`: reentrancy / access-control / tvl-accounting / oracle / logic / ...

### 태깅 스키마

```yaml
- id: "2024-04-noya-H-01"
  audit: "2024-04-noya"
  severity: "high"
  title: "MorphoBlue TVL adds borrow instead of subtracting"
  locality: "cross-contract"     # single-file | cross-contract | protocol-level
  files_involved: 3
  codebase_sol_files: 41
  scale: "medium"                # small (≤20) | medium (21-80) | large (81+)
  difficulty: "medium"           # hard (dup≤3) | medium (4-15) | easy (16+)
  category: "tvl-accounting"
  cutoff_status: "pre"           # pre | post
```

### 이 접근의 장점
- 새 데이터 수집 불필요 — EVMBench 데이터 그대로 사용
- 재현성 — 동일 벤치마크 사용하므로 다른 연구와 직접 비교 가능
- 태깅이 분석 깊이를 더함 — "오케스트레이션이 cross-contract에서 효과적" 같은 결과
- 지식 컷오프 분리 보고 자체가 기여 — EVMBench 논문에서 언급 안 한 부분

---

## 3. 실험 설계

### 3.1 비교 대상 (모델 × scaffold)

3개 Claude 모델 × 3 scaffold = 6-9개 설정. 전부 subscription 기반 ($0 API 비용).

| 설정 | 모델 | scaffold | 특성 |
|------|------|---------|------|
| `opus_skill` | Opus 4.6 | /web3-hunt skill | 메인 플로우 (structured pipeline) |
| `opus_raw` | Opus 4.6 | 단순 프롬프트 | baseline (스킬 없음) |
| `opus_orchestrated` | Opus 4.6 | audit_orchestrator.py | 멀티에이전트 (parallel specialists) |
| `sonnet_skill` | Sonnet 4.6 | /web3-hunt skill | Opus 대비 성능-비용 tradeoff |
| `sonnet_raw` | Sonnet 4.6 | 단순 프롬프트 | |
| `haiku_skill` | Haiku 4.5 | /web3-hunt skill | 최저가 모델 |

> 전부 `claude -p` subscription 기반. API 비용 $0, rate limit만 적용.
> 병목은 비용이 아니라 실행 시간 (Opus ~15분/audit, Sonnet ~10분, Haiku ~5분).

**추가 실험 (optional)**:
- 예산 cap 실험: `--max-budget $0.5, $1, $2, $5`로 Opus 성능 변화 측정
- API baseline: GPT-4.1 raw + orch+cache (legacy 데이터 재사용, 추가 비용 없음)

### 3.2 측정 메트릭

**성능 메트릭:**
- Recall@H (High severity 탐지율)
- Recall@M (Medium severity 탐지율)
- Recall@H+M (전체)
- Precision (FP burden)
- $/valid finding (비용 효율)

**비용 메트릭:**
- 총 토큰 사용량 (input/output)
- 총 API 비용 ($)
- 실행 시간 (초)
- $/finding (유효 finding당 비용)

**분석 축:**
- cutoff별: pre-cutoff vs post-cutoff (contamination 효과 측정)
- locality별: single-file vs cross-contract vs protocol-level
- scale별: small vs medium vs large codebase
- difficulty별: easy vs medium vs hard

### 3.3 Research Questions

- **RQ1.** API 환경에서 runtime architecture(single-call vs tool-use loop)가 cost-effectiveness에 미치는 영향은 어떠한가?
- **RQ2.** Prompt cache 효율은 orchestration 방식에 따라 어떻게 달라지며, cache prefix 안정성이 비용에 미치는 정량적 영향은?
- **RQ3.** Smart contract 취약점의 어떤 특성(locality, complexity)이 scaffold별 탐지율 차이를 결정하는가?
- **RQ4.** Orchestrator-centric single-call 구조가 recall을 유지하면서 비용을 절감하는 조건과 한계는?

---

## 4. TODO (2026-04-06 개정: Claude Orchestration 집중)

> API 멀티모델 실험은 성능 부족으로 중단. legacy/ 폴더에 보관.
> Claude Code subscription 기반 `claude -p` orchestration에 집중.

### Phase 1: Related Work 보강
- [ ] 신규 논문 정독 및 positioning 반영
  - "Don't Break the Cache" (2601.06007) — cache + agentic task 상호작용
  - "Re-Evaluating EVMBench" (2603.10795) — scaffolding 영향 + post-cutoff 22 incidents
  - SWE-agent (NeurIPS'24) — ACI가 agent 행동을 구조적으로 결정
  - LLM-SmartAudit (TSE'25) — SC multi-agent 대표, 비용 분석 없음 = 우리 gap
  - iAudit (ICSE'25) — fine-tuned + agent hybrid SC audit
  - Codified Context (2602.20478) — CLI/filesystem agent의 유일한 학술 사례
  - AgentArch (2509.10769) — architecture preference가 태스크별로 다름
- [ ] research-positioning.md Related Work 섹션 업데이트
- [ ] 기존 논문(Tokenomics, AgentTaxo, Co-Saving 등) + 신규 논문 통합 정리

### Phase 2: EVMBench 태깅
- [ ] 117개 취약점 목록 정리
- [ ] cutoff_status 태깅 (pre/post, 기준: 2025-05)
- [ ] locality 태깅 (single-file / cross-contract / protocol-level)
- [ ] scale 태깅 (.sol 파일 수 기준)
- [ ] difficulty 태깅 (dup 수 기반)
- [ ] category 태깅
- [ ] 태깅 JSON 저장 → benchmark/evmbench_tags.json

### Phase 3: Claude Orchestration 실험
- [ ] **Noya baseline 놓친 6개 분석** — Opus raw 14개 vs ground truth 20개 비교, 놓친 패턴(cross-contract? edge case?) 분류
- [ ] 비용 최적화 전략 벤치마크:
  - `checklist`: structured checklist prompt (비용 동일, recall 개선)
  - `tiered`: Sonnet sweep → Opus targeted (핵심 비용 절감)
  - `cache-multipass`: 동일 prefix 다관점 순차 분석
- [ ] 실험 matrix 확정 (Claude만):
  - `opus_skill`: /web3-hunt skill (현재 메인 플로우)
  - `opus_raw`: 단순 프롬프트, 스킬 없음
  - `sonnet_skill`: Sonnet으로 동일 스킬
  - `sonnet_raw`: Sonnet 단순 프롬프트
  - `haiku_skill`: Haiku로 동일 스킬 (비용 하한)
  - `opus_orchestrated`: audit_orchestrator.py (멀티에이전트)
- [ ] 예산 cap 실험 (--max-budget $0.5, $1, $2, $5)
- [ ] post-cutoff audits 우선 실행 (contamination-free)
- [ ] 전체 40 audits 실행 (pre/post 분리 보고)
- [ ] patch mode 실행 (evmbench_patch_runner.py --post-cutoff)

### Phase 4: 비용 분석
- [ ] scaffold별 input/output/cache token 분해
- [ ] 모델별 (Opus/Sonnet/Haiku) 비용 비교
- [ ] $/finding, $/patched vuln 계산
- [ ] Re-Evaluating EVMBench 논문 결과와 직접 비교
- [ ] 실패 유형 분류 (missed, FP, timeout, budget exhaustion)

### Phase 5: 논문 작성
- [ ] RQ 재정의 (Claude orchestration 중심)
- [ ] paper-outline.md 업데이트
- [ ] Figures: Cost-Recall scatter, token breakdown bar chart, scaffold 비교 table
- [ ] 논문 초안 (IMRaD)
- [ ] 멘토 리뷰

---

## 5. 예상 비용

### Claude subscription 기반 (API 비용 $0)

| 설정 | 예상 시간/audit | × 40 audits | 비고 |
|------|----------------|-------------|------|
| opus_skill | ~15분 | ~10시간 | 메인 플로우 |
| opus_raw | ~5분 | ~3.5시간 | baseline |
| sonnet_skill | ~10분 | ~7시간 | 비용 하한 비교 |
| sonnet_raw | ~3분 | ~2시간 | |
| haiku_skill | ~5분 | ~3.5시간 | 최저가 |
| opus_orchestrated | ~20분 | ~13시간 | 멀티에이전트 |

> 전부 `claude -p` subscription → API 비용 $0, rate limit만 적용
> 병목은 비용이 아니라 **시간** (총 ~40시간)
> post-cutoff 5 audits 먼저 (~4시간) → 전체는 유의미한 결과 확인 후

---

## 6. 타임라인 (2026-04-06 기준)

| 주차 | 마일스톤 |
|------|---------|
| W1 | Phase 1 (Related Work 보강) + Phase 2 (태깅) |
| W2 | Phase 3 시작 (post-cutoff 5 audits × 6 settings pilot) |
| W3 | Phase 3 완료 (전체 40 audits 실행) |
| W4 | Phase 4 (비용 분석, 비교) |
| W5-6 | Phase 5 (논문 초안) |
| W6-7 | Phase 5 (분석 + 논문 초안) |

---

## 7. 리스크 및 대응

| 리스크 | 영향 | 대응 |
|--------|------|------|
| 저가 모델이 예상보다 성능 낮음 | 80% 목표 미달 | 실제 달성 가능한 수치로 정직하게 보고 — Pareto frontier 자체가 contribution |
| 오케스트레이션이 차이 안 남 | 핵심 주장 약화 | locality/scale별 분석으로 "어디서 차이 나는지" 세분화 |
| Pre-cutoff 결과가 contamination으로 부풀려짐 | 비교 신뢰도 하락 | post-cutoff 결과를 primary로, pre-cutoff는 참고용으로 분리 보고 |
| 비용 추적 부정확 | 비교 신뢰도 하락 | 토큰 단위 정밀 추적, 실패 run 포함, 재현 가능한 로그 |
| OpenRouter 모델 불안정/다운 | 실험 중단 | 실험 전 dry run으로 안정성 확인, 재시도 로직 포함 |

---

## 8. 실험 결과 로그

### Run 0: Noya Baseline (Opus raw vs Orchestrator) — 2026-04-03

| | Opus raw | Orchestrator (6 agent) |
|---|---|---|
| 비용 | **$1.30** | ~$6-8 (추정) |
| 시간 | 242초 | 718초 |
| Finding | 14개 | 20/20 (100% recall) |
| 토큰 | cache_create 108K + cache_read 537K + out 9.5K | 6× 이상 |

**핵심:** Opus raw $1.30으로 14개 → 이미 충분히 효율적. Orchestrator는 5-6배 비싸서 20/20.
**미해결:** Opus raw가 놓친 6개 패턴 분석 필요 (어떤 유형? cross-contract? edge case?)

### Run 1: Orchestrated (소스 전체 × N specialists) — 2026-04-04

| 모델 | Scaffold | Recall | 비용 | 입력 토큰 |
|------|---------|--------|------|----------|
| GPT-4.1 | orchestrated | **93.2%** | $16.45 | 7.2M |
| Gemini 2.5 Flash | orchestrated | **94.0%** | $3.36 | 8.5M |

**결론:** Recall 높지만 토큰 폭발 (raw 대비 2~3배). Specialist마다 전체 소스 중복 전송이 원인.

### Run 2: Tool-Augmented (Slither 기반 코드 선별) — 2026-04-05

| 모델 | Scaffold | Recall | 비용 | 입력 토큰 |
|------|---------|--------|------|----------|
| GPT-4.1 | tool_augmented | **14.5%** | $1.97 | 654K |
| Gemini 2.5 Flash | tool_augmented | **6.8%** | $0.44 | 810K |
| Qwen 3.5-122B | tool_augmented | **12.8%** | $1.19 | 758K |
| MiMo-V2-Flash | tool_augmented | **10.3%** | $0.09 | 713K |

**결론:** 토큰 90% 절약했으나 recall 치명적으로 낮음. 원인:
1. Slither 컴파일 실패율 높음 → 대부분 audit에서 0 findings
2. Skeleton만으로는 로직 버그 탐지 불가
3. **정적분석 도구에 의존하는 코드 선별은 실패** — 도구가 컴파일 못하면 전체 파이프라인 무력화

**교훈:** 코드 선별은 도구 의존 없이 해야 함. LLM 자체의 능력을 활용하되 토큰을 줄이는 방법 필요.

### Run 3: Agentic (tool-use, 모델이 read_file 선택) — 2026-04-05

| 모델 | Scaffold | Recall | 비용 | 입력 토큰 |
|------|---------|--------|------|----------|
| GPT-4.1 | agentic | **13.7%** | $6.24 | 2.6M |

**결론:** 모델이 파��을 2~3개만 읽고 성급하게 답변. tool-use 자체는 동작하지만 모델이 게으름 피움. 프롬프트로 강제해도 무시할 가능성 높음.

### Run 4: Partitioned (파일 분할, 중복 없음) — 2026-04-05

| 모델 | Scaffold | Recall | 비용 | 진행 |
|------|---------|--------|------|------|
| MiMo-V2-Flash | partitioned (파일만) | **~36%** | 매우 저렴 | 10/40 시점 중단 |

**결론:** 토큰은 raw와 비슷하나 recall 낮음. 원인: 크로스컨트랙트 컨텍스트 부재. skeleton 추가로 해결 시도했으나 아직 미검증.

---

## 9. 핵심 교훈 (2026-04-05)

### 구조적 발견

1. **Claude가 잘 된 이유는 "에이전트"가 아니라 "파일시스템 접근"**
   - Claude의 `--add-dir`는 토큰 비용 없이 파일을 읽음
   - API 모델은 소스를 프롬프트에 넣어야 함 → 구조적으로 동일하게 복제 불가

2. **코드를 줄이면 recall이 죽는다**
   - tool_augmented: 토큰 90% 절약 → recall 90% 하락
   - agentic: 모델에게 맡기면 게으름 피움
   - LLM은 코드를 직접 봐야 버그를 찾음. 요약/skeleton만으로는 안 됨

3. **코드를 중복 보내면 비용이 폭발한다**
   - orchestrated inline: N specialist × 전체 소스 = 토큰 Nx
   - 소스 중복 전송 없이 커버리지를 유지하는 게 핵심 과제

4. **정적분석 도구에 의존하면 안 된다**
   - Slither 컴파일 실패율이 높아 대부분 audit에서 무력화

5. **파티셔닝은 가능성 있지만 크로스컨트랙트가 약점**
   - 파일을 나누면 토큰은 raw와 같지만 파일 간 상호작용 버그를 놓침
   - skeleton으로 부분 해결 가능하나 미검증

### 핵심 통찰 (2026-04-06)

위 발견들을 종합하면 하나의 결론으로 수렴:

> **API 환경에서 cost-effective orchestration의 핵심은 "LLM을 agent로 만드는 것"이 아니라 "Python orchestrator가 agent 역할을 하고 LLM은 curated context로 single-call 분석만 수행하는 것"이다.**

이는 기존 연구(Tokenomics, AgentTaxo)가 측정한 communication tax의 근본 원인이 agent 설계가 아닌 **runtime architecture**(매 턴 전체 history 재전송 + cache prefix 무효화)임을 의미한다.

---

## 10. 비용 최적화 전략 (2026-04-07)

**목표:** Raw Opus 수준 recall (~70%)을 $0.50-0.80에 달성

### Option A: Opus Raw + 프롬프트 강화
- 비용: $1.30 (동일), structured checklist로 recall 올리기
- Per-file mandatory reporting + category checklist + cross-contract comparison phase

### Option B: Opus Raw + Haiku 사후 보완
- 비용: ~$1.45 ($1.30 + Haiku $0.15)
- Opus 1회 분석 → Haiku가 체크리스트 기반 표면 스캔

### Option C: Sonnet Broad Sweep → Opus Targeted Deep (핵심)
- 비용: ~$0.50-0.90 (Sonnet $0.30-0.50 + Opus targeted $0.20-0.40)
- Sonnet이 전체 코드 읽고 candidate 목록 → Opus는 candidate만 정밀 분석
- Risk: Sonnet이 놓치면 Opus도 못 봄 → recall bias 설계 필요

### Option D: Cache Multi-Pass
- 동일 prefix로 여러 관점(value-flow, access-control, cross-contract) 순차 분석
- Cache hit으로 2번째 이후 ~50% 할인
- Non-determinism 감소 효과도 기대

---

## 관련 문서

- [research-positioning.md](research-positioning.md) — 멘토 논의 기반 포지셔닝
- [paper-outline.md](paper-outline.md) — 논문 아웃라인
