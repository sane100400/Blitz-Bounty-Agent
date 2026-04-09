# Research Positioning

> 2026-04-06 업데이트. 실험 결과 기반 방향 전환 반영.

## 핵심 포지셔닝 (2026-04-06 확정)

### Runtime Architecture가 Agent Orchestration의 Cost-Effectiveness를 결정한다

동일한 분석 목표(smart contract 취약점 탐지)에서도 **runtime architecture가 cost-effectiveness를 구조적으로 바꾼다**는 점을 도메인 실험으로 증명.

- API 환경에서 tool-use loop의 **반복 context 전송이 prompt cache 효율을 저하**시키며, orchestrator-centric single-call 구조가 recall을 유지하면서 비용을 3-5× 절감
- "단순히 single-agent가 낫다"는 주장이 아니라, **왜 API 환경에서 그런 현상이 발생하는지 구조적 원인(cache prefix 안정성, context 중복 비율)을 분석**

### 포지셔닝 변천

| 시점 | 포지셔닝 | 문제점 |
|------|---------|--------|
| 2026-04-04 (초기) | "싼 모델을 똑똑하게" — 저가 모델 + orchestration으로 Opus 80% 달성 | 실험 결과 orchestration 비용이 폭발, cost-effective하지 않음 |
| 2026-04-05 (실험 후) | "Orch+cache가 cost-effective" | Orch+cache는 prompt engineering + API caching이지 진정한 orchestration이 아님 |
| **2026-04-06 (확정)** | **"Runtime architecture가 cost를 결정"** | 실험 데이터로 뒷받침 가능, 기존 연구 갭 존재 |

## 기존 포지션의 한계 (참고용)

- Blitz가 현재 주로 사용하는 모델은 `Claude Opus`이며, 이는 절대적으로 저렴한 모델이 아님
- 따라서 현재 단계에서 주장할 수 있는 것은 "economical model"이 아니라 "expensive model을 더 효율적으로 사용하는 orchestration"임
- "frontier 성능의 80%를 저비용으로 달성" 같은 문장은 아직 과장일 수 있음
- 비용 주장은 반드시 비교 기준을 동반해야 함:
  - 같은 모델, 다른 scaffold 비교
  - 다른 모델, 같은 scaffold 비교
  - 총비용에 실패 run / 재시도 / PoC 비용 포함 여부 명시

## 왜 연구적으로 의미가 있는가

- "개인 환경에서 가볍게 실행된다" 자체는 contribution이 아님
- 대신 "같은 예산/시간 제약에서 더 높은 성능" 또는 "유사 성능을 훨씬 낮은 비용으로 달성"은 의미 있는 systems/security contribution
- 보안 에이전트의 실제 활용에서는 최고 성능보다 반복 실행 가능한 비용 구조가 중요함
- 따라서 포지셔닝은 "budget-efficient agent"가 아니라 "deployable, budget-aware, verifiable security agent"로 가야 함
- 특히 Opus 기반일 경우 "저비용 모델"이 아니라 "비용 효율적 시스템 설계"로 말해야 정직함

## 핵심 메시지

> **"Don't make the LLM the agent — make the orchestrator the agent."**

API 환경에서는 LLM에게 도구를 주고 자율 탐색시키는 것보다, Python orchestrator가 코드를 선별하고 LLM은 curated context로 한 번만 분석하는 것이 recall 동등 + 비용 3-5× 절감.

### 실험적 근거 (GPT-4.1 × 5 audits)

| Scaffold | Recall | Cost | 특성 |
|----------|--------|------|------|
| Raw (single-call) | 58% | $0.79 | baseline |
| Orch+cache (specialist × single-call) | **67%** | $1.20 | cache hit으로 context 공유 |
| Agentic v2 (tool-use loop) | 58-67% | $1.67-$3.66 | O(N²) token 누적, cache miss |

## 연구 질문 (RQ)

- **RQ1.** API 기반 LLM agent orchestration에서 runtime architecture(single-call vs tool-use loop)가 cost-effectiveness에 미치는 영향은 어떠한가?
- **RQ2.** Prompt cache 효율은 orchestration 방식에 따라 어떻게 달라지며, tool-use loop에서 cache prefix 안정성이 비용에 미치는 정량적 영향은?
- **RQ3.** Smart contract 취약점의 어떤 특성(locality, complexity, 도메인 특수성)이 orchestration 방식별 탐지율 차이를 결정하는가?
- **RQ4.** Orchestrator-centric single-call 구조가 recall을 유지하면서 비용을 절감하는 조건과 한계는?

## 연구 Contribution

1. **실증 비교**: Smart contract security 도메인에서 4가지 orchestration scaffold (raw, orch+cache, agentic, agentic v2)의 recall-cost tradeoff를 EVMBench 기반으로 비교
2. **Architectural insight**: Communication tax의 근본 원인이 agent 설계가 아닌 **runtime architecture** (파일시스템 접근 vs token-based tool-use)임을 실험적으로 증명
3. **실용적 가이드라인**: API 기반 모델에서는 Python orchestrator가 agent 역할을 하고 LLM은 single-call analyst로 사용하는 것이 cost-effective
4. **Negative result**: Agentic tool-use loop은 API 환경에서 O(N²) token 누적으로 인해 cost-ineffective — agent-level 최적화로는 해결 불가능한 infrastructure-level 문제

현재 구현 상태:

- [x] `cost-performance Pareto frontier`를 측정할 실험 스크립트 추가
- [x] 같은 벤치마크에서 성능, 비용, 시간, 하드웨어 전제를 같이 보고하는 문서 구조 정리
- [x] 단일 머신에서 재현 가능한 post-cutoff ablation 프로토콜 정리
- [x] subscription-backed `claude -p` 경로와 API-style upstream 경로를 분리 문서화
- [ ] post-cutoff matrix 실측
- [ ] 같은 모델 대비 비용 절감, 같은 비용 대비 성능 유지라는 두 주장 분리 보고
- [ ] 실패 run, 재시도, PoC 작성 비용까지 포함한 총비용 정의 고정
- [ ] 과장 없는 headline number 확정

관련 구현:

- [docs/evmbench-ablation-plan.md](/home/sane100400/projects/Blitz-Bounty-Agent/docs/evmbench-ablation-plan.md)
- [docs/optimization-ledger.md](/home/sane100400/projects/Blitz-Bounty-Agent/docs/optimization-ledger.md)
- [benchmark/evmbench_ablation.py](/home/sane100400/projects/Blitz-Bounty-Agent/benchmark/evmbench_ablation.py)
- [benchmark/evmbench_skill_runner.py](/home/sane100400/projects/Blitz-Bounty-Agent/benchmark/evmbench_skill_runner.py)

## 평가 원칙

- `Detect` 단독 지표에만 의존하지 않는다.
- 가능하면 `Patch`, `Exploit`, `PoC-confirmed finding`을 상위 지표로 둔다.
- `Detect`를 쓸 경우 knowledge cutoff 이후 데이터와 이전 데이터를 반드시 분리 보고한다.
- contamination risk가 있는 결과는 appendix 또는 별도 표로 분리한다.
- 정확도뿐 아니라 false positive burden과 검증 비용을 같이 본다.
- 비용 지표는 `$/valid finding`, `$/patched vuln`, `$/first PoC`처럼 결과 기준으로 환산한다.

## 실험 설계

현재 기준 최소 실험은 다음 matrix다:

- 단일 에이전트: `single_opus`, `single_sonnet`
- 예산 제약 단일 에이전트: `single_opus_cap_050`, `single_opus_cap_080`
- 오케스트레이션: `hybrid_orchestrated`, `all_opus_orchestrated`, `all_sonnet_orchestrated`

축별 비교 질문:

- 예산 축: low / medium / high budget에서 recall이 어떻게 변하는가
- 시간 축: short / standard / long timeout에서 recall이 어떻게 변하는가
- scaffold 축: 단일 에이전트 vs 병렬 specialist가 비용 대비 성능을 실제로 개선하는가
- 모델 축: 같은 scaffold에서 Opus가 정말 cost-efficient한가
- 대체 가능성: 같은 성능 일부를 Sonnet 또는 smaller model로 대체할 수 있는가
- 컨텍스트 축: 전체 파일 sweep과 더 공격적인 scope pruning 사이의 tradeoff는 무엇인가
- 검증 축: PoC / triage 단계가 precision과 총비용에 미치는 영향은 무엇인가

실행 entrypoint:

```bash
python3 benchmark/evmbench_ablation.py \
  --profiles single_opus,single_sonnet,hybrid_orchestrated,all_opus_orchestrated,all_sonnet_orchestrated \
  --post-cutoff
```

## 분석 포인트

- 비용 절감이 어디서 발생하는지 분해한다: 입력 토큰, 출력 토큰, cache read/write, iteration 수, tool 호출 수
- 실패 유형을 분류한다: missed vuln, false positive, invalid PoC, timeout, budget exhaustion, usage exhaustion
- 어떤 취약점 클래스에서 저비용 전략이 잘 먹히고 어디서 무너지는지 본다
- 성공 사례와 실패 사례를 같이 읽어서 `budget-efficient and sufficient`가 성립하는 조건을 뽑는다
- Opus 기반일 경우 "모델이 싸다"가 아니라 "비싼 모델을 덜 낭비한다"는 식으로 해석한다

## Subscription-Backed Deployability

이 프로젝트의 배포 가능성 주장은 `API key 없는 main path`까지 포함해야 설득력이 생긴다.

- main Blitz runner는 `claude -p`를 사용하므로 Claude Code CLI가 로그인된 subscription session이면 동작한다
- 이 경로는 `ANTHROPIC_API_KEY`가 없어도 된다
- 반면 upstream official EVMBench harness는 별도 경로이며 API-style credential이 필요할 수 있다
- 따라서 논문/발표에서는 두 경로를 섞지 말고 분리해서 설명해야 한다

재현용 점검 명령:

```bash
python3 benchmark/claude_subscription_check.py
python3 benchmark/claude_subscription_check.py --probe
```

## 논문 서술 주의점

- [ ] 직접적인 저가 이미지 표현 대신 `cost-efficient`, `budget-aware`, `deployable`, `Pareto-efficient`, `lower-cost` 표현 사용
- [ ] "80% 성능"만 강조하지 말고 반드시 비교 기준과 비용 기준을 같이 명시
- [ ] contamination 가능성이 있는 Detect 결과를 capability evidence처럼 과장하지 않기
- [ ] 단일 머신 실행 가능성을 engineering convenience가 아니라 deployment realism으로 연결하기
- [ ] Opus 사용 시 "저비용" 표현은 피하고, "비용 효율적 orchestration"으로 한정하기
