// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "forge-std/console.sol";

// forge test --match-contract BalancerERC4626Remove_EthStuck \
//   --fork-url https://1rpc.io/eth -vv

/**
 * G-1: ETH permanently stuck in CompositeLiquidityRouter
 *
 * removeLiquidityProportionalFromERC4626Pool is marked `payable` but
 * the hook (removeLiquidityERC4626PoolProportionalHook) does NOT call
 * _returnEth(params.sender) — unlike both add-liquidity ERC4626 hooks
 * which explicitly call _returnEth at the end.
 *
 * Any ETH sent alongside a remove-liquidity call is permanently lost.
 *
 * Deployed selector: 0x38947f0b
 * Deployed signature: removeLiquidityProportionalFromERC4626Pool(address,uint256,uint256[],bool,bytes)
 * (The current main branch has added bool[] unwrapWrapped; deployed version does not.)
 */

interface IERC20 {
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

// Deployed interface — no bool[] unwrapWrapped parameter
interface ICompositeLiquidityRouter {
    function removeLiquidityProportionalFromERC4626Pool(
        address pool,
        uint256 exactBptAmountIn,
        uint256[] memory minUnderlyingAmountsOut,
        bool wethIsEth,
        bytes memory userData
    ) external payable returns (uint256[] memory);
}

contract BalancerERC4626Remove_EthStuck is Test {

    // Balancer V3 CompositeLiquidityRouter (in-scope asset #7)
    ICompositeLiquidityRouter constant CLR =
        ICompositeLiquidityRouter(0x1CD776897ef4f647bf8241Ec69549e4A9cb1D608);

    // Balancer V3 Vault
    address constant VAULT = 0xbA1333333333a1BA1108E8412f11850A5C319bA9;

    // Balancer V3 ERC4626 pool: waEthUSDT / Aave Prime GHO / waEthUSDC (~$25M TVL)
    address constant POOL = 0x85B2b559bC2D21104C4DEFdd6EFcA8A20343361D;

    // LP who holds BPT directly (484 BPT, ~$25K)
    address constant LP = 0x28F61F0fCeDEaBEF82Af782a800f5De675B78331;

    function setUp() public {
        vm.createSelectFork("https://1rpc.io/eth");
    }

    /// @dev Debug: plain remove (no ETH) to verify the operation itself works
    function test_DebugPlainRemove() public {
        uint256 bpt = IERC20(POOL).balanceOf(LP);
        require(bpt > 0, "LP has no BPT");

        vm.prank(LP);
        IERC20(POOL).approve(address(CLR), bpt);

        uint256[] memory minOut = new uint256[](3);

        vm.prank(LP);
        try CLR.removeLiquidityProportionalFromERC4626Pool(
            POOL, bpt / 2, minOut, false, bytes("")
        ) returns (uint256[] memory amounts) {
            console.log("Plain remove: SUCCESS");
            for (uint i = 0; i < amounts.length; i++) {
                console.log("  amounts[", i, "]:", amounts[i]);
            }
        } catch (bytes memory reason) {
            console.log("Plain remove: REVERTED, reason bytes:");
            console.logBytes(reason);
        }
    }

    /// @dev Main PoC: ETH sent with remove is permanently stuck
    function test_ETHStuckOnERC4626PoolRemove() public {
        uint256 bpt = IERC20(POOL).balanceOf(LP);
        require(bpt > 0, "LP has no BPT");

        console.log("=== PoC: ETH stuck in CompositeLiquidityRouter ===");
        console.log("LP BPT balance:", bpt / 1e18, "BPT");

        // Give LP 1 ETH to send alongside the remove call
        // (simulates frontend bug / multi-call wrapper forwarding msg.value)
        vm.deal(LP, 1 ether);

        uint256 routerEthBefore = address(CLR).balance;

        vm.prank(LP);
        IERC20(POOL).approve(address(CLR), bpt);

        uint256[] memory minOut = new uint256[](3);

        vm.prank(LP);
        CLR.removeLiquidityProportionalFromERC4626Pool{value: 1 ether}(
            POOL,
            bpt / 2,
            minOut,
            false,      // wethIsEth = false
            bytes("")
        );

        uint256 routerEthAfter = address(CLR).balance;
        uint256 stuck = routerEthAfter - routerEthBefore;

        console.log("Router ETH before:", routerEthBefore);
        console.log("Router ETH after :", routerEthAfter);
        console.log("ETH stuck        :", stuck / 1e18, "ETH");
        console.log("Root cause: removeLiquidityERC4626PoolProportionalHook");
        console.log("  missing _returnEth(params.sender) call");

        assertEq(stuck, 1 ether, "1 ETH permanently stuck in CompositeLiquidityRouter");
    }
}
