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
    function getOrderId(PodOrder calldata) external pure returns (bytes32);
    function plot(address, uint256, uint256) external view returns (uint256);
}
interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function approve(address, uint256) external returns (bool);
}

contract Debug_FillOrder is Test {
    address constant L2_BEANSTALK = 0xD1A0060ba708BC4BCD3DA6C37EFa8deDF015FB70;
    address constant BEAN = 0xBEA0005B8599265D41256905A9B3073D397812E4;
    address constant REAL_HOLDER = 0x2E5120b6aAb3742E3A9c8dCE46e79192bbee3Bf3;
    
    IBeanstalk beanstalk = IBeanstalk(L2_BEANSTALK);
    IERC20 bean = IERC20(BEAN);

    uint256 constant FIELD_ID = 0;
    uint256 constant PLOT_IDX = 5_000_000_000;
    
    function setUp() public {
        vm.createSelectFork("https://arb1.arbitrum.io/rpc", 443366112);
    }

    // Test: create order, then try to fill with a non-existent plot
    // This should fail BEFORE the function body succeeds (in the function body itself)
    // If it fails with overflow rather than "Invalid plot", the overflow is in a PRE-check
    function test_fillWithNonExistentPlot() public {
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
        console.log("Order created");
        
        // Try to fill with a non-existent plot
        // This should revert with "Marketplace: Plot does not exist" or similar
        // NOT with arithmetic overflow
        vm.expectRevert();
        vm.prank(makeAddr("filler"));
        beanstalk.fillPodOrder(order, PLOT_IDX, 0, 1, IBeanstalk.To.EXTERNAL);
        console.log("fillPodOrder reverted (expected)");
    }
    
    // Test: create order, then fill with a vm.store plot - same as original
    function test_fillWithStorePlot() public {
        address victim = makeAddr("victim");
        address orderer = makeAddr("orderer");
        deal(BEAN, orderer, 1e6);
        
        _givePlot(victim, FIELD_ID, PLOT_IDX, 1);
        console.log("Plot created:", beanstalk.plot(victim, FIELD_ID, PLOT_IDX));
        
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
        console.log("Order created");
        
        // Try fill
        vm.prank(victim);
        beanstalk.fillPodOrder(order, PLOT_IDX, 0, 1, IBeanstalk.To.EXTERNAL);
        console.log("FILL SUCCEEDED");
    }
    
    function _givePlot(address account, uint256 fieldId, uint256 plotIdx, uint256 amount) internal {
        bytes32 accountBase = keccak256(abi.encode(account, uint256(0)));
        uint256 fieldsSlot = uint256(accountBase) + 23;
        bytes32 fieldBase = keccak256(abi.encode(fieldId, fieldsSlot));
        uint256 plotsSlot = uint256(fieldBase);
        bytes32 plotStorageSlot = keccak256(abi.encode(plotIdx, plotsSlot));
        vm.store(L2_BEANSTALK, plotStorageSlot, bytes32(amount));
    }
}
