// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "forge-std/console.sol";

contract Debug_FertSlots2 is Test {
    address constant L2_BEANSTALK = 0xD1A0060ba708BC4BCD3DA6C37EFa8deDF015FB70;
    
    function setUp() public {
        // Use latest block - no pinning
        vm.createSelectFork("https://arb1.arbitrum.io/rpc");
    }
    
    function test_findAppStorageLayout() public {
        // Scan first 30 slots of diamond storage
        for (uint i = 0; i < 30; i++) {
            bytes32 val = vm.load(L2_BEANSTALK, bytes32(i));
            if (uint256(val) != 0) {
                console.log("Slot", i, ":", uint256(val));
            }
        }
        
        console.log("---");
        // Also check if AppStorage uses standard diamond storage slot
        bytes32 standardSlot = keccak256("diamond.standard.diamond.storage");
        console.log("Standard diamond slot:", uint256(standardSlot));
        bytes32 v = vm.load(L2_BEANSTALK, standardSlot);
        console.log("Value:", uint256(v));
    }
}
