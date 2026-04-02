// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import "forge-std/Test.sol";
import {OmniVaultManager} from "src_vaults/vaults/OmniVaultManager.sol";
import {IOmniVaultManager} from "src_vaults/interfaces/IOmniVaultManager.sol";
import {IPortfolio} from "src_vaults/interfaces/IPortfolio.sol";

/**
 * @title OmniVaultManager_FinalizeBatchUnderflow
 * @notice PoC: finalizeBatch always panics (arithmetic underflow) when currentBatchId == 0.
 *
 * ROOT CAUSE:
 *   Line 133 evaluates `completedBatches[batchId - 1].status` unconditionally.
 *   When batchId == 0, `batchId - 1` underflows to type(uint256).max in Solidity 0.8,
 *   triggering a panic revert (0x11) BEFORE the `require(batchId == 0 || ...)` guard
 *   on line 135 can short-circuit.
 *
 *   Introduced by commit "allow finalization to progress after unwound batch" (Mar 3),
 *   which extracted the storage read out of the require condition:
 *
 *     BEFORE (safe):
 *       require(batchId == 0 || completedBatches[batchId - 1].status == ..., "VM-PBNS-01");
 *
 *     AFTER (broken):
 *       BatchStatus prevBatchStatus = completedBatches[batchId - 1].status; // panics when batchId=0
 *       require(batchId == 0 || prevBatchStatus == ..., "VM-PBNS-01");
 *
 * IMPACT:
 *   On every fresh deployment currentBatchId starts at 0.
 *   finalizeBatch is permanently broken for the first batch:
 *     - Settler calls finalizeBatch → panic revert every time
 *     - Batch 0 can never be FINALIZED
 *     - Batch 0 deposits can never be settled → users receive no vault shares
 *     - Only recovery: wait 24h, call unwindBatch to refund tokens (shares never issued)
 *   Additionally, finalizeBatch for batch N+1 is blocked until batch N is SETTLED or UNWOUND,
 *   so the entire settlement pipeline is stalled at launch.
 *
 * RUN: FOUNDRY_PROFILE=vaults forge test --match-contract OmniVaultManager_FinalizeBatchUnderflow -vv
 */

// ── Stub contracts ───────────────────────────────────────────────────────────

contract StubPortfolio {
    function getTokenDetails(bytes32 symbol) external pure returns (IPortfolio.TokenDetails memory td) {
        td.decimals        = 6;
        td.tokenAddress    = address(0xBEEF);
        td.srcChainId      = 43114;
        td.l1Decimals      = 6;
        td.symbol          = symbol;
        td.symbolId        = symbol;
        td.sourceChainSymbol = symbol;
        td.isVirtual       = false;
    }
    function bulkTransferTokens(address, address, bytes32[] calldata, uint256[] calldata) external {}
}

contract StubShareToken {
    function mint(uint256, address, uint256) external {}
    function burn(uint256, uint256) external {}
    function totalSupply() external pure returns (uint256) { return 2000e18; }
    function transfer(address, uint256) external pure returns (bool) { return true; }
    function transferFrom(address, address, uint256) external pure returns (bool) { return true; }
    function allowance(address, address) external pure returns (uint256) { return type(uint256).max; }
    function balanceOf(address) external pure returns (uint256) { return 2000e18; }
    function approve(address, uint256) external pure returns (bool) { return true; }
}

// ── Test ────────────────────────────────────────────────────────────────────

contract OmniVaultManager_FinalizeBatchUnderflow is Test {
    OmniVaultManager internal manager;
    StubPortfolio    internal portfolio;
    StubShareToken   internal shareToken;

    bytes32 internal constant USDC     = bytes32("USDC");
    uint16  internal constant TOKEN_ID = 0;
    uint16  internal constant VAULT_ID = 0;

    uint16[]  internal tokenIds;
    uint256[] internal prices;
    IOmniVaultManager.VaultState[] internal vaultStates;

    function setUp() public {
        portfolio  = new StubPortfolio();
        shareToken = new StubShareToken();

        // Test contract is admin AND settler
        manager = new OmniVaultManager();
        manager.initialize(address(this), address(this));
        manager.setPortfolio(address(portfolio));

        manager.addTokenDetails(IOmniVaultManager.AssetInfo({
            symbol: USDC, tokenType: IOmniVaultManager.AssetType.QUOTE,
            precision: 0, minPerDeposit: 1, maxPerDeposit: type(uint32).max
        }));

        uint16[] memory vaultTokens = new uint16[](1);
        vaultTokens[0] = TOKEN_ID;
        uint32[] memory chainIds = new uint32[](1);
        chainIds[0] = uint32(block.chainid);

        manager.registerVault(VAULT_ID,
            IOmniVaultManager.VaultDetails({
                name: "TestVault", proposer: address(this), omniTrader: address(0),
                status: IOmniVaultManager.VaultStatus.ACTIVE,
                executor: address(0xDEAD), shareToken: address(shareToken),
                dexalotRFQ: address(0), chainIds: chainIds, tokens: vaultTokens
            }),
            new uint16[](0), new uint256[](0), uint208(2000e18)
        );

        tokenIds    = new uint16[](1);
        tokenIds[0] = TOKEN_ID;

        prices    = new uint256[](1);
        prices[0] = 1e18;

        vaultStates    = new IOmniVaultManager.VaultState[](1);
        vaultStates[0] = IOmniVaultManager.VaultState({
            vaultId: VAULT_ID, tokenIds: tokenIds, balances: new uint256[](1)
        });
    }

    // ── CONTROL ──────────────────────────────────────────────────────────────

    /// @notice After advancing past batch 0 via unwindBatch, finalizeBatch works correctly.
    function test_control_finalizeBatchWorksAfterBatchZero() public {
        // Manually advance past batch 0 (workaround for the bug)
        vm.warp(block.timestamp + manager.RECLAIM_DELAY() + 1);
        manager.unwindBatch(
            new IOmniVaultManager.DepositFufillment[](0),
            new IOmniVaultManager.WithdrawalFufillment[](0)
        );
        assertEq(manager.currentBatchId(), 1, "now at batch 1");

        // finalizeBatch succeeds for batch 1
        manager.finalizeBatch(prices, vaultStates);

        (, IOmniVaultManager.BatchStatus status, , , ) = manager.completedBatches(1);
        assertEq(uint8(status), uint8(IOmniVaultManager.BatchStatus.FINALIZED), "batch 1 FINALIZED");
    }

    // ── EXPLOIT ──────────────────────────────────────────────────────────────

    /// @notice finalizeBatch always panics when currentBatchId == 0 (fresh deployment).
    function test_exploit_finalizeBatchPanicsAtBatchZero() public {
        // Confirm fresh state: currentBatchId == 0
        assertEq(manager.currentBatchId(), 0, "fresh deployment: batchId=0");

        // finalizeBatch must revert with arithmetic underflow (panic 0x11)
        // Line 133: completedBatches[batchId - 1].status  →  completedBatches[0-1]  →  PANIC
        bool panicked;
        try manager.finalizeBatch(prices, vaultStates) {
            panicked = false;
        } catch Panic(uint256 code) {
            assertEq(code, 0x11, "arithmetic underflow/overflow panic");
            panicked = true;
        }
        assertTrue(panicked, "finalizeBatch must panic when currentBatchId == 0");

        // Confirm batch 0 is still NONE — it was never finalized
        (, IOmniVaultManager.BatchStatus status, , , ) = manager.completedBatches(0);
        assertEq(uint8(status), uint8(IOmniVaultManager.BatchStatus.NONE),
            "batch 0 stuck at NONE forever");

        // currentBatchId is still 0 — the entire pipeline is stalled
        assertEq(manager.currentBatchId(), 0, "pipeline stalled at batchId=0");
    }
}
