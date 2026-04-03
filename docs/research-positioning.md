# Research Positioning

> 2026-04-04 멘토 이수현님과의 논의 정리

## 멘토 질문

"자동 취약점 분석을 하는 건 알겠는데, 어떤 의미를 찾는 거지? 포지셔닝은? 잘 찾는 거 이런 거 말고."

## 핵심 포지셔닝: Deployable and Cost-Efficient Orchestration for Smart Contract Vulnerability Detection

기존 도구들의 문제:
- 모델 5개 동시 실행 등 고비용 오케스트레이션
- 성능(정확도)만 추구, 비용/자원 최적화는 무시
- 개인 연구자나 소규모 팀이 사용하기 어려움

우리의 차별점:
1. **토큰 효율성 최적화** — 토큰 사용량에 한계를 두고도 검증 가능한 취약점 탐지 가능
2. **단일 머신 실행** — 개인 컴퓨터 하나로 돌릴 수 있는 경량 오케스트레이션
3. **비용 대비 성능 (cost-performance tradeoff)** — 같은 frontier급 설정 대비 총 실행 비용을 줄이는 방향
4. **실배포 가능성 (deployability)** — 클러스터, 커스텀 인프라, 대규모 병렬 실행 없이도 운영 가능

## 현재 포지션의 한계

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

## 주장 문장 초안

- Blitz는 smart contract 보안 에이전트를 위한 비용-성능 최적화 오케스트레이션을 제안한다.
- Blitz는 단일 머신 환경에서 동작하며, frontier급 모델을 사용하더라도 불필요한 토큰 사용과 병렬 오버헤드를 줄이는 방향으로 설계된다.
- Blitz는 절대적으로 저렴한 모델을 사용하는 시스템이 아니라, 고비용 모델을 더 deployable하게 운용하기 위한 경량 orchestration이다.
- Blitz는 단순 탐지 리포트가 아니라 PoC, patch, exploit failure/pass와 같은 검증 가능한 결과를 우선 지표로 삼는다.

## 연구 질문 (RQ) 초안

- **RQ1.** 동일한 예산 제약 하에서 어떤 오케스트레이션이 가장 높은 취약점 탐지/패치 성능을 내는가?
- **RQ2.** 단일 머신 경량 오케스트레이션이 고비용 멀티에이전트 설정 대비 얼마나 성능을 유지하는가?
- **RQ3.** 비용 절감의 핵심 요인은 모델 크기, 컨텍스트 절약, 탐색 전략 중 무엇인가?
- **RQ4.** 비용 최적화가 Detect뿐 아니라 Patch / Exploit / PoC-confirmed setting에서도 유지되는가?

## 연구 Contribution 프레이밍

현재는 contribution을 다음 순서로 제시하는 것이 가장 정직함:

- `absolute low cost`가 아니라 `relative efficiency`
- `economical model`이 아니라 `deployable orchestration`
- `best recall`이 아니라 `cost-performance frontier` 상의 위치

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
