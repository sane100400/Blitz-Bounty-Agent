// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "forge-std/console.sol";

// forge test --match-contract Debug_Invariable --fork-url https://arb1.arbitrum.io/rpc -vvv

interface IBeanstalk {
    enum From { EXTERNAL, INTERNAL, EXTERNAL_OR_INTERNAL, EXTERNAL_TOLERANT }
    enum To { EXTERNAL, INTERNAL, EXTERNAL_OR_INTERNAL, INTERNAL_TOLERANT }

    struct PodOrder {
        address orderer;
        uint256 fieldId;
        uint24  pricePerPod;
        uint256 maxPlaceInLine;
        uint256 minFillAmount;
    }

    function createPodOrder(PodOrder calldata, uint256, From) external payable returns (bytes32);
    function fillPodOrder(PodOrder calldata, uint256, uint256, uint256, To) external payable;
    function getOrderId(PodOrder calldata) external pure returns (bytes32);
    function plot(address, uint256, uint256) external view returns (uint256);
    function totalHarvestable(uint256) external view returns (uint256);
}

interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function approve(address, uint256) external returns (bool);
}

contract Debug_Invariable is Test {
    address constant L2_BEANSTALK = 0xD1A0060ba708BC4BCD3DA6C37EFa8deDF015FB70;
    address constant BEAN = 0xBEA0005B8599265D41256905A9B3073D397812E4;

    IBeanstalk beanstalk = IBeanstalk(L2_BEANSTALK);
    IERC20 bean = IERC20(BEAN);

    // Real BEAN holder from Arbitrum - look at transfer events
    // The Beanstalk Diamond itself holds BEAN, let's find a whale
    address attacker;
    address victim;

    uint256 constant FIELD_ID = 0;
    uint256 constant PLOT_IDX = 5_000_000_000;

    function setUp() public {
        vm.createSelectFork("https://arb1.arbitrum.io/rpc", 443366112);
        // Find real BEAN holders to avoid deal() messing up invariants
        // For now just check what totalHarvestable is
        uint256 harvestable = beanstalk.totalHarvestable(FIELD_ID);
        console.log("totalHarvestable(0):", harvestable);
        console.log("BEAN.balanceOf(Diamond):", bean.balanceOf(L2_BEANSTALK));
    }

    // Test if a simple call to fillPodOrder on a real existing order works
    // without any fake setup
    function test_readState() public view {
        uint256 harvestable = beanstalk.totalHarvestable(FIELD_ID);
        console.log("totalHarvestable(0):", harvestable);
        assertGt(harvestable, 2_000_000_000);
    }
}
