// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// forge test --match-contract Debug_PlotStorage --fork-url https://arb1.arbitrum.io/rpc -vvv

import "forge-std/Test.sol";
import "forge-std/console.sol";

interface IBeanstalk {
    function plot(address account, uint256 fieldId, uint256 index) external view returns (uint256);
    function totalHarvestable(uint256 fieldId) external view returns (uint256);
}

contract Debug_PlotStorage is Test {
    address constant L2_BEANSTALK = 0xD1A0060ba708BC4BCD3DA6C37EFa8deDF015FB70;
    IBeanstalk beanstalk = IBeanstalk(L2_BEANSTALK);

    address victim = makeAddr("victim");
    uint256 constant FIELD_ID = 0;
    uint256 constant PLOT_IDX = 5_000_000_000;

    function setUp() public {
        vm.createSelectFork("https://arb1.arbitrum.io/rpc");
    }

    function test_verifyStorageSlots() public {
        // Compute all slots
        bytes32 accountBase = keccak256(abi.encode(victim, uint256(0)));
        uint256 fieldsSlot = uint256(accountBase) + 23;
        bytes32 fieldBase = keccak256(abi.encode(FIELD_ID, fieldsSlot));

        // plots[PLOT_IDX] slot
        bytes32 plotStorageSlot = keccak256(abi.encode(PLOT_IDX, uint256(fieldBase) + 0));
        // plotIndexes length slot (offset 2)
        bytes32 plotIndexesLenSlot = bytes32(uint256(fieldBase) + 2);
        // plotIndexes[0] slot
        bytes32 plotIndexesElemsBase = keccak256(abi.encode(plotIndexesLenSlot));

        // Read before
        bytes32 plotBefore = vm.load(L2_BEANSTALK, plotStorageSlot);
        bytes32 lenBefore = vm.load(L2_BEANSTALK, plotIndexesLenSlot);
        bytes32 elem0Before = vm.load(L2_BEANSTALK, plotIndexesElemsBase);

        console.log("=== BEFORE ===");
        console.log("plots[PLOT_IDX]:", uint256(plotBefore));
        console.log("plotIndexes.length:", uint256(lenBefore));
        console.log("plotIndexes[0]:", uint256(elem0Before));
        console.log("beanstalk.plot() before:", beanstalk.plot(victim, FIELD_ID, PLOT_IDX));

        // Write
        vm.store(L2_BEANSTALK, plotStorageSlot, bytes32(uint256(1)));
        vm.store(L2_BEANSTALK, plotIndexesLenSlot, bytes32(uint256(1)));
        vm.store(L2_BEANSTALK, plotIndexesElemsBase, bytes32(PLOT_IDX));

        // Read after
        bytes32 plotAfter = vm.load(L2_BEANSTALK, plotStorageSlot);
        bytes32 lenAfter = vm.load(L2_BEANSTALK, plotIndexesLenSlot);
        bytes32 elem0After = vm.load(L2_BEANSTALK, plotIndexesElemsBase);

        console.log("=== AFTER ===");
        console.log("plots[PLOT_IDX]:", uint256(plotAfter));
        console.log("plotIndexes.length:", uint256(lenAfter));
        console.log("plotIndexes[0]:", uint256(elem0After));
        console.log("beanstalk.plot() after:", beanstalk.plot(victim, FIELD_ID, PLOT_IDX));

        assertEq(uint256(plotAfter), 1, "plots[PLOT_IDX] = 1");
        assertEq(uint256(lenAfter), 1, "plotIndexes.length = 1");
        assertEq(uint256(elem0After), PLOT_IDX, "plotIndexes[0] = PLOT_IDX");
        assertEq(beanstalk.plot(victim, FIELD_ID, PLOT_IDX), 1, "plot() returns 1");
    }

    // Try different offsets for fields within Account
    // Maybe offset is not 23?
    function test_findFieldsOffset() public {
        bytes32 accountBase = keccak256(abi.encode(victim, uint256(0)));

        // Try offsets 20-30 for the fields mapping
        for (uint256 offset = 20; offset <= 30; offset++) {
            uint256 fieldsSlot = uint256(accountBase) + offset;
            bytes32 fieldBase = keccak256(abi.encode(FIELD_ID, fieldsSlot));
            bytes32 plotStorageSlot = keccak256(abi.encode(PLOT_IDX, uint256(fieldBase)));
            vm.store(L2_BEANSTALK, plotStorageSlot, bytes32(uint256(42)));
            uint256 result = beanstalk.plot(victim, FIELD_ID, PLOT_IDX);
            if (result == 42) {
                console.log("FOUND fields mapping at Account offset:", offset);
            }
            vm.store(L2_BEANSTALK, plotStorageSlot, bytes32(uint256(0))); // reset
        }
    }

    // Try different offsets for plotIndexes within Account.Field
    // Maybe offset is not 2?
    function test_findPlotIndexesOffset() public {
        // First establish the correct accountBase + fieldsSlot
        bytes32 accountBase = keccak256(abi.encode(victim, uint256(0)));

        // Find correct offset first (expected 23 from previous debugging)
        uint256 correctOffset = 23;
        {
            bytes32 accountBase2 = keccak256(abi.encode(victim, uint256(0)));
            for (uint256 off = 20; off <= 30; off++) {
                uint256 fSlot = uint256(accountBase2) + off;
                bytes32 fBase = keccak256(abi.encode(FIELD_ID, fSlot));
                bytes32 pSlot = keccak256(abi.encode(PLOT_IDX, uint256(fBase)));
                vm.store(L2_BEANSTALK, pSlot, bytes32(uint256(42)));
                if (beanstalk.plot(victim, FIELD_ID, PLOT_IDX) == 42) {
                    correctOffset = off;
                    console.log("fields offset:", off);
                }
                vm.store(L2_BEANSTALK, pSlot, bytes32(uint256(0)));
            }
        }

        uint256 fieldsSlot = uint256(accountBase) + correctOffset;
        bytes32 fieldBase = keccak256(abi.encode(FIELD_ID, fieldsSlot));

        // Set plots[PLOT_IDX] = 1 for real
        bytes32 plotStorageSlot = keccak256(abi.encode(PLOT_IDX, uint256(fieldBase)));
        vm.store(L2_BEANSTALK, plotStorageSlot, bytes32(uint256(1)));

        console.log("plot() returns:", beanstalk.plot(victim, FIELD_ID, PLOT_IDX));

        // Now try different offsets for plotIndexes within Field
        // We need to figure out where plotIndexes.length lives
        for (uint256 piOffset = 0; piOffset <= 8; piOffset++) {
            bytes32 lenSlot = bytes32(uint256(fieldBase) + piOffset);
            // Read current value
            bytes32 currentVal = vm.load(L2_BEANSTALK, lenSlot);
            console.log("Field offset", piOffset, "current value:", uint256(currentVal));
        }
    }
}
