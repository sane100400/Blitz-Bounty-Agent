// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "forge-std/console.sol";

interface IBeanstalk {
    // Functions that expose fertilizer state
    function getFertilizer(uint128 id) external view returns (uint256);
    function getActiveFertilizer() external view returns (uint256);
    function totalFertilizerBeans() external view returns (uint256);
    function isFertilizing() external view returns (bool);
}

contract Debug_FertSlots is Test {
    address constant L2_BEANSTALK = 0xD1A0060ba708BC4BCD3DA6C37EFa8deDF015FB70;
    
    function setUp() public {
        vm.createSelectFork("https://arb1.arbitrum.io/rpc", 443366112);
    }
    
    function test_findAppStorageSlot() public {
        // The diamond storage slot for Beanstalk AppStorage
        // Standard EIP-2535 uses keccak256("diamond.standard.diamond.storage") - 1
        // But Beanstalk might use a custom slot
        
        // Let's check the storage at various positions to identify the AppStorage base
        // We know fieldCount = 1 (from cast call)
        // And some fertiler variables are non-zero
        
        // Scan for value "1" (fieldCount) in small slots
        for (uint i = 0; i < 20; i++) {
            bytes32 val = vm.load(L2_BEANSTALK, bytes32(i));
            if (uint256(val) != 0) {
                console.log("Non-zero at slot", i, ":", uint256(val));
            }
        }
        
        // Check known Beanstalk diamond storage slot
        // From Beanstalk source, LibAppStorage.sol uses:
        // bytes32 constant STORAGE_SLOT = keccak256("diamond.standard.diamond.storage");
        bytes32 standardSlot = keccak256("diamond.standard.diamond.storage");
        console.log("Standard diamond storage slot:", uint256(standardSlot));
        
        // Check if AppStorage is at this slot
        bytes32 val = vm.load(L2_BEANSTALK, standardSlot);
        console.log("Value at diamond storage slot:", uint256(val));
        
        // Try -1 variant
        bytes32 slotM1 = bytes32(uint256(standardSlot) - 1);
        val = vm.load(L2_BEANSTALK, slotM1);
        console.log("Value at diamond storage slot - 1:", uint256(val));
    }
    
    function test_findFertilizerValues() public {
        // We need to find fertilizedIndex and fertilizedPaidIndex
        // The getTokenEntitlementsAndBalances computes:
        // (fertilizedIndex - fertilizedPaidIndex + leftoverBeans)
        // If fertilizedPaidIndex > fertilizedIndex, it underflows
        
        // Let's try to find these by brute force scanning
        // We know:
        // - isFertilizing() returns true
        // - getActiveFertilizer() returns 17216958
        // - fertilizedAndFarmableBeans() returns some value
        
        // Try to get fertilizedIndex via some getter
        try IBeanstalk(L2_BEANSTALK).totalFertilizerBeans() returns (uint256 v) {
            console.log("totalFertilizerBeans:", v);
        } catch {}
        
        // Check if rinsableSprouts exists
        (bool success, bytes memory data) = L2_BEANSTALK.staticcall(
            abi.encodeWithSignature("rinsableSprouts(address)", address(this))
        );
        if (success) console.log("rinsableSprouts(this):", abi.decode(data, (uint256)));
        else console.log("rinsableSprouts not available");
        
        // Try getMowStatus or claimableSeasons
        (success, data) = L2_BEANSTALK.staticcall(
            abi.encodeWithSignature("fertilizedIndex()(uint256)")
        );
        if (success) console.log("fertilizedIndex:", abi.decode(data, (uint256)));
        else console.log("fertilizedIndex() not available");
    }
}
