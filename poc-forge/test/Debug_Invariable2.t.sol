// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "forge-std/console.sol";

// Diagnose why fundsSafu overflows in our test

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
    function totalHarvestable(uint256) external view returns (uint256);
    function plot(address, uint256, uint256) external view returns (uint256);
}

interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function approve(address, uint256) external returns (bool);
    function totalSupply() external view returns (uint256);
}

contract Debug_Invariable2 is Test {
    address constant L2_BEANSTALK = 0xD1A0060ba708BC4BCD3DA6C37EFa8deDF015FB70;
    address constant BEAN = 0xBEA0005B8599265D41256905A9B3073D397812E4;
    // Real BEAN holder found from transfer events
    address constant REAL_HOLDER = 0x2E5120b6aAb3742E3A9c8dCE46e79192bbee3Bf3;
    
    IBeanstalk beanstalk = IBeanstalk(L2_BEANSTALK);
    IERC20 bean = IERC20(BEAN);

    uint256 constant FIELD_ID = 0;
    uint256 constant PLOT_IDX = 5_000_000_000;

    function setUp() public {
        vm.createSelectFork("https://arb1.arbitrum.io/rpc", 443366112);
    }

    // Test 1: use a real holder (no deal) to create an order
    // If this passes, deal() is the problem
    function test_realHolderCreateOrder() public {
        uint256 holderBalance = bean.balanceOf(REAL_HOLDER);
        console.log("REAL_HOLDER BEAN balance:", holderBalance);
        require(holderBalance > 1e6, "holder needs > 1 BEAN");

        IBeanstalk.PodOrder memory order = IBeanstalk.PodOrder({
            orderer: REAL_HOLDER,
            fieldId: FIELD_ID,
            pricePerPod: 999_999,
            maxPlaceInLine: type(uint256).max,
            minFillAmount: 1
        });

        vm.startPrank(REAL_HOLDER);
        bean.approve(L2_BEANSTALK, 1e6);
        beanstalk.createPodOrder(order, 1e6, IBeanstalk.From.EXTERNAL);
        vm.stopPrank();
        console.log("createPodOrder PASSED with real holder");
    }

    // Test 2: use deal but with adjustTotalSupply=false to avoid supply change
    function test_dealNoSupplyChange() public {
        uint256 supplyBefore = bean.totalSupply();
        address attacker = makeAddr("attacker");
        
        // deal with stdCheats - this increases supply
        deal(BEAN, attacker, 1e6);
        
        uint256 supplyAfter = bean.totalSupply();
        console.log("Supply before deal:", supplyBefore);
        console.log("Supply after deal:", supplyAfter);
        console.log("Supply change:", supplyAfter - supplyBefore);

        IBeanstalk.PodOrder memory order = IBeanstalk.PodOrder({
            orderer: attacker,
            fieldId: FIELD_ID,
            pricePerPod: 999_999,
            maxPlaceInLine: type(uint256).max,
            minFillAmount: 1
        });

        vm.startPrank(attacker);
        bean.approve(L2_BEANSTALK, 1e6);
        beanstalk.createPodOrder(order, 1e6, IBeanstalk.From.EXTERNAL);
        vm.stopPrank();
        console.log("createPodOrder PASSED with deal");
    }

    // Test 3: give real holder a plot and have them fill an order
    function test_realHolderFillOrder() public {
        uint256 holderBalance = bean.balanceOf(REAL_HOLDER);
        require(holderBalance > 1e6, "holder needs > 1 BEAN");

        // Give REAL_HOLDER a plot via vm.store
        _givePlot(REAL_HOLDER, FIELD_ID, PLOT_IDX, 1);
        require(beanstalk.plot(REAL_HOLDER, FIELD_ID, PLOT_IDX) == 1, "plot setup failed");

        address orderer = makeAddr("orderer");
        deal(BEAN, orderer, 1e6);

        IBeanstalk.PodOrder memory order = IBeanstalk.PodOrder({
            orderer: orderer,
            fieldId: FIELD_ID,
            pricePerPod: 1_000_000,  // fair price - 1 bean per pod
            maxPlaceInLine: type(uint256).max,
            minFillAmount: 1
        });

        vm.startPrank(orderer);
        bean.approve(L2_BEANSTALK, 1e6);
        beanstalk.createPodOrder(order, 1e6, IBeanstalk.From.EXTERNAL);
        vm.stopPrank();

        console.log("createPodOrder done, trying fill...");

        vm.prank(REAL_HOLDER);
        beanstalk.fillPodOrder(order, PLOT_IDX, 0, 1, IBeanstalk.To.EXTERNAL);
        console.log("fillPodOrder PASSED");
        console.log("REAL_HOLDER bean after:", bean.balanceOf(REAL_HOLDER));
    }

    function _givePlot(address account, uint256 fieldId, uint256 plotIdx, uint256 amount) internal {
        bytes32 accountBase = keccak256(abi.encode(account, uint256(0)));
        uint256 fieldsSlot = uint256(accountBase) + 23;
        bytes32 fieldBase = keccak256(abi.encode(fieldId, fieldsSlot));
        uint256 plotsSlot = uint256(fieldBase) + 0;
        bytes32 plotStorageSlot = keccak256(abi.encode(plotIdx, plotsSlot));
        vm.store(L2_BEANSTALK, plotStorageSlot, bytes32(amount));
    }
}
