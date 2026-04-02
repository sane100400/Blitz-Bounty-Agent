// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "forge-std/console.sol";

contract Debug_StorageCollision is Test {
    address constant L2_BEANSTALK = 0xD1A0060ba708BC4BCD3DA6C37EFa8deDF015FB70;
    
    function setUp() public {
        vm.createSelectFork("https://arb1.arbitrum.io/rpc", 443366112);
    }
    
    function test_checkStorageSlots() public {
        address victim = makeAddr("victim");
        console.log("victim address:", victim);
        
        uint256 FIELD_ID = 0;
        uint256 PLOT_IDX = 5_000_000_000;
        
        // Compute the storage slot we write to
        bytes32 accountBase = keccak256(abi.encode(victim, uint256(0)));
        uint256 fieldsSlot = uint256(accountBase) + 23;
        bytes32 fieldBase = keccak256(abi.encode(FIELD_ID, fieldsSlot));
        uint256 plotsSlot = uint256(fieldBase);
        bytes32 plotStorageSlot = keccak256(abi.encode(PLOT_IDX, plotsSlot));
        
        console.log("accountBase:", uint256(accountBase));
        console.log("plotStorageSlot:", uint256(plotStorageSlot));
        
        // Read this slot BEFORE writing
        bytes32 before = vm.load(L2_BEANSTALK, plotStorageSlot);
        console.log("Value at slot BEFORE:", uint256(before));
        
        // Write to it
        vm.store(L2_BEANSTALK, plotStorageSlot, bytes32(uint256(1)));
        
        // Read AFTER
        bytes32 after_ = vm.load(L2_BEANSTALK, plotStorageSlot);
        console.log("Value at slot AFTER:", uint256(after_));
        
        // Now check known critical slots for fertilizedIndex and fertilizedPaidIndex
        // In Beanstalk AppStorage, the storage starts at what slot?
        // Let's check Beanstalk's diamondStorage approach
        // For LibAppStorage, it might use slot keccak256("diamond.standard.diamond.storage") - 1 
        // or a different approach
        
        // Check slot 0 of diamond - usually the facet mappings for diamond
        bytes32 slot0 = vm.load(L2_BEANSTALK, bytes32(uint256(0)));
        console.log("Diamond slot 0:", uint256(slot0));
        
        // Try to find fertilizedIndex by reading slot 1 area
        // AppStorage might be at slot 0 directly for Beanstalk (non-standard)
        for (uint i = 0; i < 5; i++) {
            bytes32 val = vm.load(L2_BEANSTALK, bytes32(uint256(i)));
            console.log("Slot", i, ":", uint256(val));
        }
    }
    
    function test_isolateOverflow() public {
        address victim = makeAddr("victim");
        uint256 FIELD_ID = 0;
        uint256 PLOT_IDX = 5_000_000_000;
        
        bytes32 accountBase = keccak256(abi.encode(victim, uint256(0)));
        uint256 fieldsSlot = uint256(accountBase) + 23;
        bytes32 fieldBase = keccak256(abi.encode(FIELD_ID, fieldsSlot));
        uint256 plotsSlot = uint256(fieldBase);
        bytes32 plotStorageSlot = keccak256(abi.encode(PLOT_IDX, plotsSlot));
        
        // What is the value at plotStorageSlot before writing?
        bytes32 beforeVal = vm.load(L2_BEANSTALK, plotStorageSlot);
        console.log("Plot slot before:", uint256(beforeVal));
        
        // Read some surrounding slots to see if this is in a sensitive area
        for (int i = -2; i <= 2; i++) {
            bytes32 surroundSlot = bytes32(uint256(plotStorageSlot) + uint256(int256(i)));
            bytes32 val = vm.load(L2_BEANSTALK, surroundSlot);
            if (uint256(val) != 0) {
                console.log("NEARBY NON-ZERO at offset", i);
                console.log("  Value:", uint256(val));
            }
        }
    }
}
