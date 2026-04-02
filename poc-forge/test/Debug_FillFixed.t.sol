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
    function totalHarvestable(uint256) external view returns (uint256);
}
interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function approve(address, uint256) external returns (bool);
}

contract Debug_FillFixed is Test {
    address constant L2_BEANSTALK = 0xD1A0060ba708BC4BCD3DA6C37EFa8deDF015FB70;
    address constant BEAN = 0xBEA0005B8599265D41256905A9B3073D397812E4;
    
    IBeanstalk beanstalk = IBeanstalk(L2_BEANSTALK);
    IERC20 bean = IERC20(BEAN);

    uint256 constant FIELD_ID = 0;
    uint256 constant PLOT_IDX = 5_000_000_000;
    
    function setUp() public {
        vm.createSelectFork("https://arb1.arbitrum.io/rpc", 443366112);
    }

    function test_fillWithFixedPlot() public {
        address victim = makeAddr("victim");
        address orderer = makeAddr("orderer");
        deal(BEAN, orderer, 1e6);
        
        _givePlot(victim, FIELD_ID, PLOT_IDX, 1);
        
        console.log("plot:", beanstalk.plot(victim, FIELD_ID, PLOT_IDX));
        
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
        
        console.log("Order created, filling...");
        
        vm.prank(victim);
        beanstalk.fillPodOrder(order, PLOT_IDX, 0, 1, IBeanstalk.To.EXTERNAL);
        
        console.log("FILL SUCCEEDED");
        console.log("victim beans:", bean.balanceOf(victim));
    }
    
    function _givePlot(address account, uint256 fieldId, uint256 plotIdx, uint256 amount) internal {
        bytes32 accountBase = keccak256(abi.encode(account, uint256(0)));
        uint256 fieldsSlot = uint256(accountBase) + 23;
        bytes32 fieldBase = keccak256(abi.encode(fieldId, fieldsSlot));

        // 1. Set plots[plotIdx] (offset 0 within Account.Field)
        bytes32 plotStorageSlot = keccak256(abi.encode(plotIdx, uint256(fieldBase) + 0));
        vm.store(L2_BEANSTALK, plotStorageSlot, bytes32(amount));

        // 2. Set plotIndexes.length = 1 (offset 2 within Account.Field)
        bytes32 plotIndexesLenSlot = bytes32(uint256(fieldBase) + 2);
        vm.store(L2_BEANSTALK, plotIndexesLenSlot, bytes32(uint256(1)));

        // 3. Set plotIndexes[0] = plotIdx
        bytes32 plotIndexesElemsBase = keccak256(abi.encode(plotIndexesLenSlot));
        vm.store(L2_BEANSTALK, plotIndexesElemsBase, bytes32(plotIdx));
        
        // piIndex[plotIdx] = 0 (default value is correct)
    }
}
