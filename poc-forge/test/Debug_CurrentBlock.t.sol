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
    function totalHarvestable(uint256) external view returns (uint256);
}

interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function approve(address, uint256) external returns (bool);
}

contract Debug_CurrentBlock is Test {
    address constant L2_BEANSTALK = 0xD1A0060ba708BC4BCD3DA6C37EFa8deDF015FB70;
    address constant BEAN = 0xBEA0005B8599265D41256905A9B3073D397812E4;
    
    IBeanstalk beanstalk = IBeanstalk(L2_BEANSTALK);
    IERC20 bean = IERC20(BEAN);

    uint256 FIELD_ID = 0;
    uint256 PLOT_IDX;
    
    function setUp() public {
        vm.createSelectFork("https://arb1.arbitrum.io/rpc");
        PLOT_IDX = beanstalk.totalHarvestable(0) + 10_000_000; // well beyond harvestable
        console.log("Using plot index:", PLOT_IDX);
        console.log("Block number:", block.number);
    }
    
    // Test with current block - no pinning
    function test_fillWithStorePlot_currentBlock() public {
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
        
        vm.prank(victim);
        beanstalk.fillPodOrder(order, PLOT_IDX, 0, 1, IBeanstalk.To.EXTERNAL);
        console.log("FILL SUCCEEDED at block", block.number);
        console.log("victim bean after:", bean.balanceOf(victim));
    }
    
    function _givePlot(address account, uint256 fieldId, uint256 plotIdx, uint256 amount) internal {
        bytes32 accountBase = keccak256(abi.encode(account, uint256(0)));
        uint256 fieldsSlot = uint256(accountBase) + 23;
        bytes32 fieldBase = keccak256(abi.encode(fieldId, fieldsSlot));
        bytes32 plotStorageSlot = keccak256(abi.encode(plotIdx, fieldBase));
        vm.store(L2_BEANSTALK, plotStorageSlot, bytes32(amount));
    }
}
