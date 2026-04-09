# Paper Outline: Cost-Effective LLM Strategies for Smart Contract Vulnerability Detection

> 2026-04-06 작성. 실험 결과 기반 논문 흐름 정리.

## One-Line Summary

**스마트컨트랙트 취약점 탐지를 위한 LLM 전략을 체계적으로 비교하고, API 환경에서 cost-effective한 orchestration 조건과 한계를 실증적으로 규명한다.**

---

## 논문 흐름

### Section 1. Introduction — 문제 제기

LLM 기반 스마트컨트랙트 취약점 탐지는 비싸다.
- Opus 단일 run: $5-15/audit, 대규모 프로토콜은 $30+
- 반복 실행 불가능 → 실무 배포에 장벽
- 기존 연구는 recall만 추구, 비용 분석 부재

**Research Question:**
> "싸게 할 수 있는가? 어떤 전략이 cost-effective한가? 그리고 왜?"

### Section 2. Related Work

#### 2.1 LLM 기반 스마트컨트랙트 보안
- EVMBench (OpenAI+Paradigm, 2026) — 40 audits, 117 vulns 벤치마크
- 기존 tool: Slither, Mythril 등 정적 분석 → LLM과 상호보완적

#### 2.2 Multi-Agent Orchestration의 비용 문제
- **Tokenomics** (MSR '26) — Code Review 단계가 토큰 59.4% 소비, input token 53.9% `[conference]`
- **AgentTaxo** (ICLR '25 Workshop) — duplicated tokens + communication tax `[workshop]`
- **Co-Saving** (arXiv '25.05) — budget-aware collaboration으로 50.85% 토큰 절감 `[preprint]`

#### 2.3 Single-Agent vs Multi-Agent
- **When SAS Replace MAS** (2025) — MAS→SAS 컴파일로 토큰 53.7% 감소, API 호출 3-4→1회 `[preprint]`
- **Skill Distillation** (2026.04) — adaptive distillation으로 비용 최대 8× 감소 `[preprint]`

#### 2.4 연구 갭
기존 연구는 범용 SE 태스크에서 토큰 낭비를 측정하거나 agent-level에서 줄임.
**Smart contract security 도메인에서 orchestration 전략별 cost-performance tradeoff를 체계적으로 비교한 연구 없음.**

### Section 3. Methodology

#### 3.1 Orchestration Scaffolds (4가지)

| Scaffold | 구조 | 핵심 특성 |
|----------|------|---------|
| **Raw** | Single LLM call, 전체 소스 inline | Baseline. 가장 단순 |
| **Orch+cache** | N specialist × single-call + prompt caching | Specialist 간 source context 공유 (cache hit) |
| **Agentic v1** | Tool-use loop (read_file, grep_file, list_files) | LLM이 agent로 자율 탐색 |
| **Agentic v2** | Python recon 선행 + smart tools + structured pipeline | Phase 1을 Python으로 무료 실행, LLM은 deep read만 |

#### 3.2 Models (4개)

| 모델 | Provider | $/1M input | $/1M output | Context |
|------|----------|-----------|------------|---------|
| GPT-4.1 | OpenAI | $2.00 | $8.00 | 1M |
| Gemini 2.5 Flash | Google | $0.30 | $2.50 | 1M |
| Qwen 3.5-122B | OpenRouter | $0.26 | $2.08 | 128K |
| MiMo-V2-Flash | OpenRouter | $0.09 | $0.29 | 128K |

#### 3.3 Benchmark
- EVMBench detect-tasks: 40 audits, 117 vulnerabilities
- LLM judge (Haiku 기반) scoring
- 메트릭: Recall, Cost ($), $/finding, findings/$

#### 3.4 구현
- `audit_orchestrator.py` — 5가지 orchestration 모드
- `llm_client.py` — 5개 모델 통합 API + tool-use loop + context caching
- `tool_runner.py` — smart tools 5개 (regex 기반, 컴파일 불필요)
- `evmbench_multimodel_runner.py` — 7가지 scaffold benchmark runner

### Section 4. Results

#### 4.1 Raw Baseline (40 audits)

| 모델 | Recall | Cost | $/finding |
|------|--------|------|-----------|
| MiMo-V2-Flash | 6.0% (7/117) | $0.21 | $0.030 |
| Qwen 3.5-122B | 6.8% (8/117) | $1.71 | $0.214 |
| Gemini 2.5 Flash | 11.1% (13/117) | $1.11 | $0.085 |
| GPT-4.1 | 13.7% (16/117) | $5.91 | $0.370 |

#### 4.2 Scaffold 비교 (GPT-4.1 × 5 audits)

| Scaffold | Recall | Cost | $/finding | findings/$ |
|----------|--------|------|-----------|------------|
| Raw | 58% (7/12) | $0.79 | $0.113 | 8.8 |
| **Orch+cache** | **67% (8/12)** | **$1.20** | **$0.149** | **6.7** |
| Agentic v2 (최적화 전) | 67% (8/12) | $3.66 | $0.458 | 2.2 |
| Agentic v2 (최적화 후) | 58% (7/12) | $1.67 | $0.239 | 4.2 |

**Orch+cache가 Pareto optimal**: recall 최고 (67%) + cost 중간 ($1.20).
Agentic v2는 recall 동등하지만 비용 3-5× → Pareto dominated.

#### 4.3 Audit별 상세 (5 audits)

| Audit | Files | Raw | Orch+cache | Agentic v2 |
|-------|-------|-----|-----------|-----------|
| pooltogether | 3 | 2/2 $0.04 | 1/2 $0.09 | 1/2 $0.15 |
| nextgen | 76 | 1/2 $0.25 | 2/2 $0.60 | 2/2 $0.53 |
| ethereumcreditguild | 22 | 1/2 $0.38 | 0/2 $0.32 | 0/2 $0.56 |
| canto | 4 | 0/2 $0.08 | 2/2 $0.10 | 2/2 $0.23 |
| curves | 5 | 3/4 $0.04 | 3/4 $0.09 | 2/4 $0.21 |

#### 4.4 Orch+cache × 40 audits (Section 4 메인 데이터)

*(벤치마크 실행 중 — 완료 후 채울 것)*

#### 4.5 탐지 불가 취약점 분석

4개 취약점은 모든 scaffold에서 미탐지:

| 유형 | 예시 | 원인 |
|------|------|------|
| 프로토콜 고유 상태 머신 | share truncation, rebase profit index | 도메인 특화 로직, 코드만으로 판단 불가 |
| 다중 트랜잭션 공격 | 상태 누적 후 exploit | 단일 함수 분석으로는 도달 불가 |
| Memory vs Storage 불일치 | memory snapshot stale after storage update | 실행 추적 필요 |
| Edge case 입력 | self-transfer, 0-amount deposit | happy path 분석의 한계 |

**→ Recall 천장 67% (8/12). Scaffold 변경이 아닌 분석 접근법(동적 실행, invariant 검증 등)이 필요.**

### Section 5. Discussion — 왜 이런 결과가 나왔는가

#### 5.1 Tool-Use Loop의 구조적 비용 문제

API tool-use loop에서 매 턴 전체 history 재전송 → O(N²) input token 누적:

```
pooltogether (파일 3개):
  Raw (single-call):    13,597 input tokens → $0.04
  Agentic v2 (13 turns): 352,923 input tokens → $0.76
  Input token 배율: 26×
```

#### 5.2 Prompt Cache와 Tool-Use의 상호작용

- **Orch+cache**: 동일 source prefix → cache hit (50% 할인) → specialist 간 context 공유 효과
- **Agentic**: 매 턴 history 추가 → prefix 변경 → cache miss → 공유 불가

Tool-use loop에서는 prefix가 자주 변경되어 cache 효율이 크게 저하됨.
(tool_choice 변경은 cached blocks를 무효화할 수 있으나, tool definitions와 system prompts는 유지 가능)

#### 5.3 Runtime Architecture가 Cost를 결정한다

| Runtime | 파일 읽기 비용 | Context 공유 | 비용 수준 |
|---------|-------------|------------|---------|
| CLI (파일시스템 접근) | 0 tokens | 파일시스템 공유 | 최저 |
| API single-call | Source inline (1회) | Prompt cache 50% 할인 | 중간 |
| API tool-use loop | 매 턴 재전송 O(N²) | Cache miss | 최고 |

**핵심**: 같은 orchestration 전략이라도 runtime architecture에 따라 비용이 구조적으로 달라짐.
이는 agent 설계 최적화(Co-Saving 등)로는 해결 불가능한 infrastructure-level 제약.

#### 5.4 실용적 가이드라인

| 예산 | 권장 전략 | 예상 recall |
|------|---------|-----------|
| < $1/audit | Raw single-call (GPT-4.1 또는 Gemini) | 10-14% |
| $1-3/audit | Orch+cache (GPT-4.1, 3-5 specialists) | 15-25%* |
| $5+/audit | CLI 기반 orchestration (Claude Code 등) | 30-46%** |

*40 audit 결과 확정 후 업데이트
**EVMBench 공식 baseline 참고

### Section 6. Threats to Validity

- 5 audit pilot 기반 scaffold 비교 — 40 audit 전체 결과로 확장 필요
- EVMBench pre-cutoff 데이터 오염 가능성 — post-cutoff 분리 보고 필요
- GPT-4.1 단일 모델 scaffold 비교 — 다른 모델에서 동일 패턴 확인 필요
- LLM judge scoring의 한계 — false positive/negative 가능
- Non-determinism — 동일 설정 재실행 시 결과 변동 가능

### Section 7. Conclusion

1. Smart contract 취약점 탐지를 위한 4가지 LLM orchestration 전략을 EVMBench에서 체계적으로 비교
2. Orch+cache (specialist 분할 + prompt caching)가 cost-performance Pareto optimal
3. Agentic tool-use loop은 API 환경에서 O(N²) token 누적으로 cost-ineffective (negative result)
4. 비용 병목의 근본 원인은 agent 설계가 아닌 runtime architecture (context 전달 방식)
5. 모든 scaffold에서 탐지 불가능한 취약점 유형 존재 → orchestration의 recall 천장 규명

---

## 예상 Figures & Tables

| # | 유형 | 내용 |
|---|------|------|
| Fig 1 | Scatter plot | Scaffold별 Cost vs Recall (Pareto frontier) |
| Fig 2 | Line chart | Tool-use loop turn별 input token 누적 (O(N²) 시각화) |
| Fig 3 | Bar chart | 4개 모델 × raw recall 비교 |
| Tab 1 | Table | 4 scaffold × 5 audits 상세 결과 |
| Tab 2 | Table | 40 audits raw baseline (모델별) |
| Tab 3 | Table | 40 audits orch+cache 결과 |
| Tab 4 | Table | 미탐지 취약점 특성 분류 |

---

## 남은 작업

- [ ] Orch+cache × 40 audits 완료 (실행 중)
- [ ] Cache hit rate 정량 분석
- [ ] EVMBench 취약점 태깅 (locality, scale, difficulty)
- [ ] Post-cutoff 분리 보고
- [ ] 논문 초안 작성 (IMRaD)
