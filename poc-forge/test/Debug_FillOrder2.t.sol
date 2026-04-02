// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "forge-std/console.sol";

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
    function plot(address, uint256, uint256) external view returns (uint256);
}
interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function approve(address, uint256) external returns (bool);
}

contract Debug_FillOrder2 is Test {
    address constant L2_BEANSTALK = 0xD1A0060ba708BC4BCD3DA6C37EFa8deDF015FB70;
    address constant BEAN = 0xBEA0005B8599265D41256905A9B3073D397812E4;
    
    IBeanstalk beanstalk = IBeanstalk(L2_BEANSTALK);
    IERC20 bean = IERC20(BEAN);

    uint256 constant FIELD_ID = 0;
    uint256 constant PLOT_IDX = 5_000_000_000;
    
    function setUp() public {
        vm.createSelectFork("https://arb1.arbitrum.io/rpc", 443366112);
    }

    // Test: fill with non-existent plot WITHOUT vm.expectRevert
    // This should fail with "not enough pods" or similar, NOT with overflow
    function test_fillNonExistentPlot_noExpectRevert() public {
        address orderer = makeAddr("orderer");
        deal(BEAN, orderer, 1e6);
        
        IBeanstalk.PodOrder memory order = IBeanstalk.PodOrder({
            orderer: orderer,
            fieldId: FIELD_ID,
            pricePerPod: 1_000_000,
            maxPlaceInLine: type(uint256).max,
            minFillAmount: 1
        });
        
        vm.startPrank(orderer);
        bean.approve(L2_BEANSTALK, 1e6);
        beanstalk.createPodOrder(order, 1e6, IBeanstalk.From.EXTERNAL);
        vm.stopPrank();
        
        // Fill with non-existent plot - should fail
        // The address has no plots, so this should fail with "not enough pods"
        vm.prank(makeAddr("filler"));
        beanstalk.fillPodOrder(order, PLOT_IDX, 0, 1, IBeanstalk.To.EXTERNAL);
        // If we get here, fill succeeded (unexpected)
    }
    
    // Test: check what a NON-EXISTENT plot returns from fundsSafu
    // The key: if oneOutFlow reads ALL balances in pre-check and fundsSafu reads entitlements in post-check
    // then a non-existent plot fill should:
    //   1. Pre-checks pass (just reading balances)
    //   2. Function body reverts (plot not found)
    //   3. No post-checks run (since body reverted)
    //   => The final revert should be "plot not found" type
    //   => NOT overflow
    // This means the overflow in vm.store case is in the FUNCTION BODY or POST-check
}
