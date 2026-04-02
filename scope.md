# Origin Protocol - Bug Bounty Scope

## 프로그램 정보
- **최대 상금**: $1,000,000
- **Critical 상금**: 경제적 피해의 10% (최소 $50,000)
- **Live Since**: 2021년 11월 22일
- **Last Updated**: 2026년 3월 10일
- **PoC 필수**: Yes (로컬 포크만, 메인넷/퍼블릭 테스트넷 금지)
- **보상 통화**: OUSD (Ethereum)
- **Arbitration**: 활성화
- **Safe Harbor**: TBD

---

## Impacts in Scope

### Critical
- 거버넌스 투표 결과 조작
- 유저 자금 직접 탈취 (at-rest or in-motion, 미수령 yield 제외)
- 자금 영구 동결
- 프로토콜 파산 (insolvency)
- 시스템 커맨드 실행 가능
- 타 유저 트랜잭션 서명
- deposit/withdrawal 리다이렉트
- 서브도메인 탈취 (금전적 손실 발생 시)
- 유저 지갑 상호작용 조작 (손실 발생)
- 트랜잭션 변조
- 연결된 지갑에 악성 트랜잭션 제출

### High
- 미수령 yield 탈취
- 미수령 yield 영구 동결
- 자금 임시 동결

---

## Assets in Scope (34개)

### Ethereum Mainnet

| 컨트랙트 이름 | 주소 | 업데이트 |
|-------------|------|---------|
| Supernova AMO Strategy | `0xf9E04C36CC7e6065cBBcc972613e8Dd75D6B5967` | 2026-03-10 |
| OUSD Morpho V2 CrossChain Master Strategy | `0xB1d624fc40824683e2bFBEfd19eB208DbBE00866` | 2026-02-23 |
| Curve USDC AMO Strategy | `0x26a02ec47ACC2A3442b757F45E0A82B8e993Ce11` | 2025-12-30 |
| Curve OETH+WETH (AMO) Strategy | `0xba0e352AB5c13861C26e4E773e7a833C3A223FE6` | 2025-12-30 |
| Morpho OUSD v2 Strategy | `0x3643cafA6eF3dd7Fcc2ADaD1cabf708075AFFf6e` | 2025-12-30 |
| BeaconProofs | `0xc4444C5D9e7C1a5A0a01c5E4b11692d589DcAF22` | 2025-11-05 |
| CompoundingStakingStrategyView | `0xEDf495F92c2eBdEEAB797E9C503aA7A3302A9c88` | 2025-11-05 |
| CompoundingStakingSSVStrategyProxy | `0xaF04828Ed923216c77dC22a2fc8E077FDaDAA87d` | 2025-11-04 |
| ARM (stETH/WETH) | `0x85b78aca6deae198fbf201c82daf6ca21942acc6` | - |
| Vault | `0xe75d77b1865ae93c7eaa3040b038d7aa7bc02f70` | - |
| OGN Rewards | `0x7609c88e5880e934dd3a75bcfef44e31b1badb8b` | - |
| xOGN | `0x63898b3b6ef3d39332082178656e9862bee45c57` | - |
| OGN Staking | `0x501804B374EF06fa9C427476147ac09F1551B9A0` | - |
| wOETH Token | `0xDcEe70654261AF21C44c093C300eD3Bb97b78192` | - |
| OETH Vault | `0x39254033945AA2E4809Cc2977E7087BEE48bd7Ab` | - |
| Governor / Timelock | `0x1D3Fbd4d129Ddd2372EA85c5Fa00b2682081c9EC` | - |
| OUSD Token | `0x2A8e1E676Ec238d8A992307B495b45B3fEAa5e86` | - |
| Migrator | `0x95c347d6214614a780847b8aaf4f96eb84f4da6d` | - |

### Base Network

| 컨트랙트 이름 | 주소 | 업데이트 |
|-------------|------|---------|
| OUSD Morpho V2 CrossChain Remote Strategy | `0xB1d624fc40824683e2bFBEfd19eB208DbBE00866` | 2026-02-23 |
| AerodromeAMOStrategyProxy | `0xF611cC500eEE7E4e4763A05FE623E2363c86d2Af` | - |
| BridgedWOETHStrategy | `0x80c864704DD06C3693ed5179190786EE38ACf835` | - |
| SuperOETHb Token | `0xdbfefd2e8460a6ee4955a68582f85708baea60a3` | - |
| Wrapped SuperOETHb | `0xdbfefd2e8460a6ee4955a68582f85708baea60a3` | - |
| SuperOETHb Vault | `0x98a0cbef61bd2d21435f433be4cd42b56b38cc93` | - |
| Timelock (Base) | `0xf817cb3092179083c48c014688d98b72fb61464f` | - |

### Sonic Network

| 컨트랙트 이름 | 주소 | 업데이트 |
|-------------|------|---------|
| Origin Sonic Dripper | `0x5b72992e9CDe8C07CE7C8217eB014EC7fD281f03` | 2025-03-06 |
| Origin Timelock | `0x31a91336414d3b955e494e7d485a6b06b55fc8fb` | 2025-03-06 |
| Sonic Staking Strategy | `0x596B0401479f6DfE1cAF8c12838311FeE742B95c` | 2025-03-06 |
| Origin Sonic | `0xb1e25689D55734FD3ffFc939c4C3Eb52DFf8A794` | - |
| Origin Sonic Vault | `0xa3c0eCA00D2B76b4d1F170b0AB3FdeA16C180186` | - |
| Wrapped Origin Sonic | `0x9F0dF7799f6FDAd409300080cfF680f5A23df4b1` | - |

### Web & App
- `https://app.originprotocol.com`

---

## Out of Scope

### Smart Contract 특이사항
- 서드파티 오라클이 잘못된 데이터 제공 (단, flash loan 조합 oracle 조작은 **in scope**)
- 51% 거버넌스 공격
- 유동성 부족으로 인한 영향
- Sybil 공격 영향
- 중앙화 리스크

### Known Issues (제출 금지)
- 스테이블코인 backing 구성 변경 (total 감소 없는 경우)
- 전략 운영 수수료로 인한 감소
- Flipper 컨트랙트 rounding → 의도적 설계
- 레거시 OGN 스테이킹(`0x501804B374EF06fa9C427476147ac09F1551B9A0`) 에어드랍 관련 이슈

### 공통 Out of Scope
- 이미 공격자가 exploit한 공격 기법
- 유출된 키/자격증명 필요한 공격
- 권한 주소 접근 필요한 공격 (추가 코드 수정 없이)
- 직접 코드 버그로 인하지 않은 외부 스테이블코인 디페깅
- 프로덕션 미사용 노출 시크릿
- 베스트 프랙티스 권고
- 기능 요청
- 테스트/설정 파일 관련
- 피싱/소셜 엔지니어링

---

## 참고 링크
- Audits: https://docs.ousd.com/security-and-risks/audits
- Immunefi: https://immunefi.com/bug-bounty/originprotocol/
