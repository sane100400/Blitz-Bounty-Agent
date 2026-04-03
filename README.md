# Blitz Bounty Agent

Claude Code 기반 스마트컨트랙트 보안 에이전트입니다.  
이 저장소는 세 가지 실행 경로를 제공합니다.

- Claude Code 슬래시 커맨드 기반 인터랙티브 헌팅
- `claude -p` 기반 헤드리스 루프 실행
- Python recon + specialist agent + merger로 구성된 멀티에이전트 오케스트레이션

추가로 EVMBench 기반의 detect / patch / ablation / visualization 스크립트를 포함합니다.

## 현재 구현 요약

- 메인 실행 경로는 `ANTHROPIC_API_KEY` 없이도 동작할 수 있습니다.
  Claude Code CLI가 로그인된 subscription session이면 repo-local runner들은 `claude -p`로 실행됩니다.
- 멀티에이전트 오케스트레이터는 LLM 호출 전에 Python으로 코드베이스를 먼저 정리합니다.
- EVMBench detect runner는 audit source를 clone하고 `.claudeignore`를 자동 생성해 읽기 범위를 줄입니다.
- judge는 기본적으로 `claude-haiku-4-5`를 사용하고 캐시를 남깁니다.
- quota/login/auth 문제는 공통 `claude_cli.py`에서 fail-fast 처리합니다.

최적화 내용은 [docs/optimization-ledger.md](/home/sane100400/projects/Blitz-Bounty-Agent/docs/optimization-ledger.md)에 정리되어 있고, 연구 포지셔닝은 [docs/research-positioning.md](/home/sane100400/projects/Blitz-Bounty-Agent/docs/research-positioning.md)에 정리되어 있습니다.

## 인증과 실행 경로

이 저장소에는 두 개의 실행 경로가 있습니다.

1. `repo-local Blitz path`
   `/web3-hunt`, `/web3-loop`, `hunt_loop.py`, `audit_loop.py`, `audit_orchestrator.py`, [`benchmark/evmbench_skill_runner.py`](/home/sane100400/projects/Blitz-Bounty-Agent/benchmark/evmbench_skill_runner.py), [`benchmark/evmbench_patch_runner.py`](/home/sane100400/projects/Blitz-Bounty-Agent/benchmark/evmbench_patch_runner.py)는 `claude -p`를 사용합니다. Claude Code CLI가 유효한 session으로 로그인되어 있으면 `ANTHROPIC_API_KEY` 없이 실행할 수 있습니다.

2. `official upstream EVMBench path`
   [`benchmark/evmbench_setup.sh`](/home/sane100400/projects/Blitz-Bounty-Agent/benchmark/evmbench_setup.sh)는 upstream EVMBench 실행 환경을 맞추기 위한 별도 경로입니다. 이 경로는 solver 구성에 따라 API credential이 필요할 수 있습니다.

subscription 기반 실행 가능 여부는 아래 명령으로 바로 확인할 수 있습니다.

```bash
# Claude CLI 존재 여부와 auth 관련 env 상태 확인
python3 benchmark/claude_subscription_check.py

# 실제 `claude -p` 최소 probe
python3 benchmark/claude_subscription_check.py --probe
```

`usage_exhausted`가 나오면 로그인 문제는 아니고 현재 subscription quota가 소진된 상태입니다.  
현재 runner들은 이런 상태를 `0 recall / $0`처럼 잘못 저장하지 않고 즉시 실패로 처리합니다.

## 아키텍처

```text
사용자 / CI / cron
   ├─ 인터랙티브: /web3-hunt, /web3-loop
   ├─ 헤드리스 루프: hunt_loop.py, audit_loop.py
   └─ 멀티에이전트: audit_orchestrator.py

공유 흐름
   1. Recon
   2. File Sweep
   3. Cross-Compare
   4. Attack Surface
   5. Triage
   6. PoC
   7. Report
```

멀티에이전트 경로는 아래처럼 구성됩니다.

```text
Phase 1: Pure Python Recon
  - in-scope .sol 파일 수집
  - 파일명 기반 pattern group 분류
  - 외부 프로토콜 키워드 탐지

Phase 2: 4-6 Specialist Agents
  - TVL / accounting
  - position lifecycle
  - access control + fund flow
  - external protocol semantics
  - core logic

Phase 3: Merger Agent
  - specialist 결과의 FINDING 블록만 취합
  - 중복 제거
  - triage
  - 최종 report 생성
```

기본 모델 배치는 현재 다음과 같습니다.

- deep model: `claude-opus-4-6`
- fast model: `claude-sonnet-4-6`
- judge model: `claude-haiku-4-5`

기본 예산 cap은 현재 다음과 같습니다.

- specialist: `$0.50`
- merger: `$0.80`

## 실행 모드

### 1. 인터랙티브 슬래시 커맨드

Claude Code 안에서 바로 호출하는 모드입니다.  
정의는 `.claude-commands/` 아래에 있습니다.

주요 커맨드:

- `/web3-hunt`
- `/web3-loop`

레거시 커맨드도 남아 있습니다.

- `/audit-hunt`
- `/audit-loop`
- `/immunefi-hunt`
- `/immunefi-loop`

예시:

```bash
# 감사 대회
/web3-hunt "https://cantina.xyz/competitions/..." cantina
/web3-hunt "https://code4rena.com/audits/..." codearena

# Immunefi 바운티
/web3-hunt "https://immunefi.com/bug-bounty/balancer" immunefi "https://1rpc.io/eth"

# 반복 탐색 루프
/web3-loop "https://cantina.xyz/competitions/..." cantina 10
```

### 2. 헤드리스 루프

Claude Code UI 없이 `claude -p` subprocess로 반복 실행하는 모드입니다.

`hunt_loop.py`

- Immunefi 바운티용
- drop history, lesson, PoC failure를 누적
- `HUNT_SIGNAL:*` 신호를 파싱

```bash
python3 hunt_loop.py "https://immunefi.com/bug-bounty/balancer" "https://1rpc.io/eth" 10
```

`audit_loop.py`

- 감사 대회용
- recon, candidate triage, report 파일 생성
- `AUDIT_SIGNAL:*` 신호를 파싱

```bash
python3 audit_loop.py "https://audits.sherlock.xyz/contests/123" sherlock 12
```

### 3. 멀티에이전트 오케스트레이션

`audit_orchestrator.py`는 코드베이스를 Python으로 먼저 분석한 뒤 specialist를 병렬 실행합니다.

```bash
python3 audit_orchestrator.py ./protocol-source --platform codearena --timeout 900
```

주요 옵션:

- `--deep-model`
- `--fast-model`
- `--specialist-budget`
- `--merger-budget`
- `--merge-timeout`
- `--benchmark`
- `--audit-id`

## 현재 구현된 최적화

이 저장소에서 실제 실행 경로에 반영된 최적화는 아래와 같습니다.

1. `Python recon before LLM`
   `audit_orchestrator.py`가 먼저 파일 맵, pattern group, 외부 프로토콜 힌트를 만들고 이후 prompt를 구성합니다.

2. `scope pruning with .claudeignore`
   EVMBench runner는 clone된 audit source마다 `.claudeignore`를 자동 생성해서 `lib/`, `node_modules/`, `out/`, `cache/`, 테스트 파일 등을 Claude read 범위에서 제외합니다.

3. `specialist decomposition`
   모든 분석을 한 프롬프트에 넣지 않고 역할별 prompt로 나눕니다.

4. `model tiering`
   semantic-heavy 단계는 Opus, pattern-heavy 단계는 Sonnet으로 나누어 실행할 수 있습니다.

5. `budget caps`
   per specialist, merger, per audit, per profile, total ablation run 수준의 cap을 둘 수 있습니다.

6. `judge cache`
   LLM-as-Judge는 Haiku 기본값과 캐시를 사용합니다.

7. `fail-fast CLI handling`
   `usage_exhausted`, `login_required`, `invalid_api_key`, `low_credit`를 공통으로 감지합니다.

상세 근거와 tradeoff는 [docs/optimization-ledger.md](/home/sane100400/projects/Blitz-Bounty-Agent/docs/optimization-ledger.md)에서 확인할 수 있습니다.

## 벤치마크

이 저장소의 메인 벤치마크 경로는 EVMBench wrapper들입니다.

### 1. Detect benchmark

[`benchmark/evmbench_skill_runner.py`](/home/sane100400/projects/Blitz-Bounty-Agent/benchmark/evmbench_skill_runner.py)는 `/web3-hunt` 기반 detect 평가 wrapper입니다.

동작:

- EVMBench audit 선택
- source repo clone / base commit checkout
- `.claudeignore` 생성
- `claude -p`로 `/web3-hunt` 실행
- raw output 저장
- ground truth와 비교해 score 산출

지원 기능:

- 모델 alias: `opus`, `sonnet`, `haiku`
- pre/post cutoff 분리
- `--max-budget`
- `--max-total-cost`
- `--no-judge`
- `--rescore`
- `--compare`
- `--dry-run`

예시:

```bash
# 단일 audit
python3 benchmark/evmbench_skill_runner.py --audit 2026-01-tempo-feeamm

# detect split 일부 실행
python3 benchmark/evmbench_skill_runner.py --split detect-tasks --limit 5

# 예산 cap + judge 생략
python3 benchmark/evmbench_skill_runner.py \
  --audit 2024-05-munchables \
  --timeout 600 \
  --max-budget 0.80 \
  --no-judge

# 저장된 결과 재채점
python3 benchmark/evmbench_skill_runner.py --split detect-tasks --limit 5 --rescore

# 최근 두 run 비교
python3 benchmark/evmbench_skill_runner.py --compare

# 실행 없이 선택된 audit만 확인
python3 benchmark/evmbench_skill_runner.py --split detect-tasks --dry-run
```

detect runner의 주요 옵션:

| 옵션 | 의미 |
|---|---|
| `--split` | `detect-tasks`, `patch-tasks`, `exploit-tasks`, `debug` |
| `--audit` | 단일 audit ID |
| `--model` | 모델 ID 또는 alias |
| `--limit` | audit 개수 제한 |
| `--timeout` | audit별 timeout |
| `--max-budget` | audit별 비용 cap |
| `--max-total-cost` | 전체 run 비용 cap |
| `--no-judge` | Haiku judge 대신 identifier matching만 사용 |
| `--rescore` | agent 재실행 없이 저장 결과 재채점 |
| `--compare` | 최근 두 run 비교 |
| `--post-cutoff` | cutoff 이후 audit만 선택 |
| `--pre-cutoff` | cutoff 이전 audit만 선택 |
| `--dry-run` | 실행 없이 선택된 audit만 출력 |

### 2. Patch benchmark

[`benchmark/evmbench_patch_runner.py`](/home/sane100400/projects/Blitz-Bounty-Agent/benchmark/evmbench_patch_runner.py)는 patch task용 runner입니다.

현재 구현 기준:

- foundry 기반 patch task만 평가
- agent가 실제 소스 파일을 수정해야 함
- `forge build`가 통과해야 함
- 기존 테스트가 유지되어야 함
- oracle exploit test는 실패해야 함

예시:

```bash
# post-cutoff patch audit만 실행
python3 benchmark/evmbench_patch_runner.py --post-cutoff

# 단일 audit
python3 benchmark/evmbench_patch_runner.py --audit 2026-01-tempo-feeamm

# 전체 foundry patch audit
python3 benchmark/evmbench_patch_runner.py --all

# dry run
python3 benchmark/evmbench_patch_runner.py --post-cutoff --dry-run
```

### 3. Cost / performance ablation

[`benchmark/evmbench_ablation.py`](/home/sane100400/projects/Blitz-Bounty-Agent/benchmark/evmbench_ablation.py)는 동일한 audit slice를 여러 실행 profile로 돌려 `recall`, `cost`, `$/detect`, `detect/$`를 비교합니다.

현재 profile:

- `single_opus`
- `single_sonnet`
- `single_opus_cap_050`
- `single_opus_cap_080`
- `hybrid_orchestrated`
- `all_opus_orchestrated`
- `all_sonnet_orchestrated`

예시:

```bash
# 가장 작은 post-cutoff slice부터 비교
python3 benchmark/evmbench_ablation.py \
  --audits 2026-01-tempo-feeamm,2026-01-tempo-mpp-streams,2026-01-tempo-stablecoin-dex \
  --profiles single_sonnet,single_opus,hybrid_orchestrated \
  --no-judge \
  --max-profile-cost 2.00 \
  --max-total-cost 4.00
```

권장 실행 순서:

1. `single_sonnet`
2. `single_opus`
3. 필요할 때만 `hybrid_orchestrated`
4. 1차 pass에서는 `--no-judge`
5. 이후 좋은 결과만 `--rescore` 또는 judge 재실행

### 4. 시각화

[`benchmark/visualize_ablation.py`](/home/sane100400/projects/Blitz-Bounty-Agent/benchmark/visualize_ablation.py)는 저장된 ablation JSON으로부터 HTML 대시보드를 생성합니다.

- benchmark 재실행 없음
- 추가 토큰 사용 없음
- summary table
- cost vs recall scatter
- cost per detect bar chart

예시:

```bash
# 가장 최근 ablation JSON 기준으로 HTML 생성
python3 benchmark/visualize_ablation.py

# 입력/출력 경로 지정
python3 benchmark/visualize_ablation.py \
  --input benchmark/results/ablation/ablation_YYYYMMDD_HHMMSS.json \
  --output benchmark/results/ablation/dashboard.html
```

## 점수 산출 방식

detect benchmark는 두 가지 경로를 사용합니다.

1. `LLM-as-Judge`
   기본 judge 모델은 `claude-haiku-4-5`이며, 결과는 `benchmark/results/judge_cache/`에 캐시됩니다.

2. `identifier fallback`
   judge를 끈 경우 파일명, 함수명, title word overlap 기반의 deterministic fallback을 사용합니다.

중요한 점:

- judge는 evaluation cost를 낮추기 위해 작은 모델을 기본값으로 사용합니다.
- detect runner는 저장된 raw output을 다시 score할 수 있으므로 scoring logic을 바꿔도 agent run을 다시 할 필요가 없습니다.

## 실험용 custom suite

[`benchmark/run.py`](/home/sane100400/projects/Blitz-Bounty-Agent/benchmark/run.py)는 커스텀 suite runner이지만, 현재는 `primary benchmark`가 아닙니다.

현재 상태:

- `benchmark/suites/known-vulns.yaml`만 존재
- 기본 case는 비어 있음
- precision / severity / cost는 일부 TODO가 남아 있음

즉, 이 경로는 실험용이며 메인 비교는 EVMBench wrapper들 기준으로 보는 것이 맞습니다.

```bash
python3 benchmark/run.py --suite known-vulns
python3 benchmark/run.py --compare
```

## 디렉터리 구조

```text
Blitz-Bounty-Agent/
├── .claude-commands/                 슬래시 커맨드 정의
├── hunt_loop.py                      헤드리스 Immunefi 루프
├── audit_loop.py                     헤드리스 감사 대회 루프
├── audit_orchestrator.py             멀티에이전트 오케스트레이터
├── claude_cli.py                     공통 Claude CLI wrapper / fail-fast 처리
├── benchmark/
│   ├── evmbench_skill_runner.py      detect benchmark wrapper
│   ├── evmbench_patch_runner.py      patch benchmark wrapper
│   ├── evmbench_ablation.py          cost/performance ablation
│   ├── visualize_ablation.py         ablation HTML 시각화
│   ├── claude_subscription_check.py  subscription-backed 실행 확인
│   ├── llm_judge.py                  judge + cache
│   ├── run.py                        실험용 custom suite runner
│   └── config.yaml                   모델 / cutoff / scoring 설정
├── docs/
│   ├── optimization-ledger.md
│   ├── research-positioning.md
│   └── evmbench-ablation-plan.md
├── evmbench-sources/                 clone된 audit source
├── evmbench-upstream/                upstream EVMBench data
└── audit-reports/                    생성된 report 출력
```

## 설치

### 요구사항

- Claude Code CLI
- Python 3.10+
- `PyYAML`
- Foundry (`forge`, `cast`)
- 선택: `gh` CLI
- 선택: Docker + `uv` (upstream EVMBench 경로를 쓸 때)

### 설치 예시

```bash
git clone https://github.com/sane100400/Blitz-Bounty-Agent.git
cd Blitz-Bounty-Agent

# subscription-backed 실행 여부 확인
python3 benchmark/claude_subscription_check.py

# upstream EVMBench 환경이 필요하면 별도 설정
bash benchmark/evmbench_setup.sh
```

Claude Code를 이 디렉터리에서 실행하면 `.claude-commands/` 아래 커맨드를 자동으로 사용할 수 있습니다.

## 현재 문서화 원칙

이 README는 현재 구현된 기능과 실제 실행 가능한 경로 위주로 설명합니다.

- 정적인 비용 추정 표는 의도적으로 넣지 않았습니다.
  실제 비용은 audit 크기, quota 상태, cache hit 여부, budget cap에 따라 크게 달라집니다.
- 정적인 benchmark 성능 표도 메인 본문에서 뺐습니다.
  최신 수치는 `benchmark/results/`에 저장된 artifact 기준으로 확인하거나 재실행하는 편이 맞습니다.
- repo-local path와 upstream EVMBench path는 일부러 분리해서 설명합니다.

## 라이선스

MIT
