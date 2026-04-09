#!/usr/bin/env python3
"""
LLM-classified locality and category tags for EVMBench vulnerabilities.
Applied by evmbench_tagger.py --apply-llm-tags.

Categories:
  access-control, reentrancy, tvl-accounting, oracle-manipulation,
  logic, signature-replay, input-validation, state-management,
  math, cross-chain, frontrunning, dos

Locality:
  single-file: bug contained in one contract
  cross-contract: bug involves 2+ contracts interacting
  protocol-level: bug involves external protocol integration or system-wide design
"""

TAGS: dict[str, tuple[str, str]] = {
    # (locality, category)
    # === 2023-07 pooltogether ===
    "2023-07-pooltogether-H-02": ("cross-contract", "tvl-accounting"),
    "2023-07-pooltogether-H-04": ("single-file", "access-control"),
    # === 2023-10 nextgen ===
    "2023-10-nextgen-H-01": ("single-file", "reentrancy"),
    "2023-10-nextgen-H-02": ("single-file", "logic"),
    # === 2023-12 ethereumcreditguild ===
    "2023-12-ethereumcreditguild-H-01": ("single-file", "state-management"),
    "2023-12-ethereumcreditguild-H-02": ("single-file", "tvl-accounting"),
    # === 2024-01 canto ===
    "2024-01-canto-H-01": ("single-file", "math"),
    "2024-01-canto-H-02": ("single-file", "math"),
    # === 2024-01 curves ===
    "2024-01-curves-H-02": ("cross-contract", "state-management"),
    "2024-01-curves-H-03": ("single-file", "logic"),
    "2024-01-curves-H-04": ("single-file", "access-control"),
    "2024-01-curves-H-05": ("single-file", "access-control"),
    # === 2024-01 init-capital ===
    "2024-01-init-capital-invitational-H-01": ("single-file", "access-control"),
    "2024-01-init-capital-invitational-H-02": ("single-file", "input-validation"),
    "2024-01-init-capital-invitational-H-03": ("single-file", "frontrunning"),
    # === 2024-01 renft ===
    "2024-01-renft-H-01": ("cross-contract", "input-validation"),
    "2024-01-renft-H-02": ("cross-contract", "logic"),
    "2024-01-renft-H-03": ("cross-contract", "reentrancy"),
    "2024-01-renft-H-05": ("cross-contract", "logic"),
    "2024-01-renft-H-06": ("cross-contract", "input-validation"),
    "2024-01-renft-H-07": ("cross-contract", "logic"),
    # === 2024-02 althea ===
    "2024-02-althea-liquid-infrastructure-H-01": ("single-file", "state-management"),
    # === 2024-03 abracadabra ===
    "2024-03-abracadabra-money-H-01": ("single-file", "oracle-manipulation"),
    "2024-03-abracadabra-money-H-02": ("single-file", "math"),
    "2024-03-abracadabra-money-H-03": ("cross-contract", "tvl-accounting"),
    "2024-03-abracadabra-money-H-04": ("single-file", "oracle-manipulation"),
    # === 2024-03 canto ===
    "2024-03-canto-H-01": ("cross-contract", "cross-chain"),
    "2024-03-canto-H-02": ("cross-contract", "cross-chain"),
    # === 2024-03 coinbase ===
    "2024-03-coinbase-H-01": ("single-file", "signature-replay"),
    # === 2024-03 gitcoin ===
    "2024-03-gitcoin-H-01": ("single-file", "state-management"),
    # === 2024-03 neobase ===
    "2024-03-neobase-H-01": ("cross-contract", "state-management"),
    # === 2024-03 taiko ===
    "2024-03-taiko-H-01": ("single-file", "math"),
    "2024-03-taiko-H-02": ("cross-contract", "logic"),
    "2024-03-taiko-H-03": ("single-file", "logic"),
    "2024-03-taiko-H-04": ("cross-contract", "frontrunning"),
    "2024-03-taiko-H-05": ("single-file", "signature-replay"),
    # === 2024-04 noya ===
    "2024-04-noya-H-01": ("cross-contract", "oracle-manipulation"),
    "2024-04-noya-H-03": ("single-file", "math"),
    "2024-04-noya-H-04": ("single-file", "dos"),
    "2024-04-noya-H-05": ("cross-contract", "tvl-accounting"),
    "2024-04-noya-H-06": ("cross-contract", "tvl-accounting"),
    "2024-04-noya-H-07": ("cross-contract", "tvl-accounting"),
    "2024-04-noya-H-08": ("cross-contract", "access-control"),
    "2024-04-noya-H-09": ("protocol-level", "logic"),
    "2024-04-noya-H-10": ("single-file", "state-management"),
    "2024-04-noya-H-11": ("cross-contract", "tvl-accounting"),
    "2024-04-noya-H-12": ("single-file", "logic"),
    "2024-04-noya-H-13": ("cross-contract", "tvl-accounting"),
    "2024-04-noya-H-14": ("cross-contract", "tvl-accounting"),
    "2024-04-noya-H-15": ("cross-contract", "tvl-accounting"),
    "2024-04-noya-H-16": ("cross-contract", "input-validation"),
    "2024-04-noya-H-18": ("cross-contract", "state-management"),
    "2024-04-noya-H-19": ("cross-contract", "tvl-accounting"),
    "2024-04-noya-H-21": ("cross-contract", "state-management"),
    "2024-04-noya-H-22": ("cross-contract", "tvl-accounting"),
    "2024-04-noya-H-23": ("cross-contract", "state-management"),
    # === 2024-05 arbitrum ===
    "2024-05-arbitrum-foundation-H-01": ("cross-contract", "logic"),
    # === 2024-05 loop ===
    "2024-05-loop-H-01": ("single-file", "logic"),
    # === 2024-05 munchables ===
    "2024-05-munchables-H-01": ("single-file", "dos"),
    "2024-05-munchables-H-02": ("single-file", "input-validation"),
    # === 2024-05 olas ===
    "2024-05-olas-H-01": ("single-file", "state-management"),
    "2024-05-olas-H-02": ("cross-contract", "cross-chain"),
    # === 2024-06 size ===
    "2024-06-size-H-01": ("single-file", "math"),
    "2024-06-size-H-02": ("single-file", "frontrunning"),
    "2024-06-size-H-03": ("single-file", "math"),
    "2024-06-size-H-04": ("single-file", "math"),
    # === 2024-06 thorchain ===
    "2024-06-thorchain-H-01": ("single-file", "tvl-accounting"),
    "2024-06-thorchain-H-02": ("single-file", "logic"),
    # === 2024-06 vultisig ===
    "2024-06-vultisig-H-01": ("cross-contract", "logic"),
    "2024-06-vultisig-H-03": ("cross-contract", "dos"),
    # === 2024-07 basin ===
    "2024-07-basin-H-01": ("single-file", "access-control"),
    "2024-07-basin-H-02": ("single-file", "math"),
    # === 2024-07 benddao ===
    "2024-07-benddao-H-01": ("cross-contract", "tvl-accounting"),
    "2024-07-benddao-H-02": ("single-file", "input-validation"),
    "2024-07-benddao-H-03": ("single-file", "state-management"),
    "2024-07-benddao-H-04": ("single-file", "math"),
    "2024-07-benddao-H-06": ("protocol-level", "logic"),
    "2024-07-benddao-H-07": ("single-file", "access-control"),
    "2024-07-benddao-H-08": ("cross-contract", "logic"),
    # === 2024-07 munchables ===
    "2024-07-munchables-H-01": ("single-file", "logic"),
    "2024-07-munchables-H-02": ("single-file", "input-validation"),
    "2024-07-munchables-H-03": ("single-file", "math"),
    "2024-07-munchables-H-04": ("single-file", "math"),
    "2024-07-munchables-H-05": ("single-file", "state-management"),
    # === 2024-07 traitforge ===
    "2024-07-traitforge-H-02": ("cross-contract", "logic"),
    "2024-07-traitforge-H-03": ("single-file", "math"),
    # === 2024-08 phi ===
    "2024-08-phi-H-01": ("single-file", "signature-replay"),
    "2024-08-phi-H-02": ("single-file", "signature-replay"),
    "2024-08-phi-H-03": ("single-file", "dos"),
    "2024-08-phi-H-04": ("single-file", "input-validation"),
    "2024-08-phi-H-06": ("single-file", "reentrancy"),
    "2024-08-phi-H-07": ("single-file", "access-control"),
    # === 2024-08 wildcat ===
    "2024-08-wildcat-H-01": ("single-file", "tvl-accounting"),
    # === 2024-12 secondswap ===
    "2024-12-secondswap-H-01": ("cross-contract", "state-management"),
    "2024-12-secondswap-H-02": ("cross-contract", "state-management"),
    "2024-12-secondswap-H-03": ("single-file", "math"),
    # === 2025-01 liquid-ron ===
    "2025-01-liquid-ron-H-01": ("single-file", "tvl-accounting"),
    # === 2025-01 next-generation ===
    "2025-01-next-generation-H-01": ("single-file", "signature-replay"),
    # === 2025-02 thorwallet ===
    "2025-02-thorwallet-H-01": ("single-file", "input-validation"),
    # === 2025-04 forte ===
    "2025-04-forte-H-01": ("single-file", "math"),
    "2025-04-forte-H-02": ("single-file", "input-validation"),
    "2025-04-forte-H-03": ("single-file", "input-validation"),
    "2025-04-forte-H-04": ("single-file", "math"),
    "2025-04-forte-H-05": ("single-file", "math"),
    # === 2025-04 virtuals ===
    "2025-04-virtuals-H-01": ("single-file", "access-control"),
    "2025-04-virtuals-H-03": ("cross-contract", "access-control"),
    "2025-04-virtuals-H-04": ("cross-contract", "access-control"),
    "2025-04-virtuals-H-05": ("single-file", "logic"),
    # === 2025-05 blackhole ===
    "2025-05-blackhole-H-02": ("single-file", "access-control"),
    # === 2025-06 panoptic ===
    "2025-06-panoptic-H-01": ("single-file", "math"),
    "2025-06-panoptic-H-02": ("single-file", "logic"),
    # === 2025-10 sequence ===
    "2025-10-sequence-H-01": ("cross-contract", "signature-replay"),
    "2025-10-sequence-H-02": ("single-file", "signature-replay"),
    # === 2026-01 tempo ===
    "2026-01-tempo-feeamm-H-01": ("single-file", "reentrancy"),
    "2026-01-tempo-mpp-streams-H-03": ("single-file", "input-validation"),
    "2026-01-tempo-stablecoin-dex-H-02": ("single-file", "access-control"),
    "2026-01-tempo-stablecoin-dex-H-04": ("single-file", "math"),
}
