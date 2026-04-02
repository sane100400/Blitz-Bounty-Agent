// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "forge-std/console.sol";

// Reproduce getTokenEntitlementsAndBalances manually to find overflow

interface IBeanstalk {
    function getWhitelistedTokens() external view returns (address[] memory);
    function getSopTokens() external view returns (address[] memory);
    function totalHarvestable(uint256 fieldId) external view returns (uint256);
    function totalHarvested(uint256 fieldId) external view returns (uint256);
    function fieldCount() external view returns (uint256);
    function getTotalDeposited(address token) external view returns (uint256);
    function getTotalDepositedBdv(address token) external view returns (uint256);
    function getInternalBalance(address user, address token) external view returns (uint256);
    function getTotalGerminatingForToken(address token) external view returns (uint256, uint256);
    function orderLockedBeans() external view returns (uint256);
}

interface IERC20 { function balanceOf(address) external view returns (uint256); }

contract Debug_FundsSafu is Test {
    address constant L2_BEANSTALK = 0xD1A0060ba708BC4BCD3DA6C37EFa8deDF015FB70;
    IBeanstalk beanstalk = IBeanstalk(L2_BEANSTALK);
    
    function setUp() public {
        vm.createSelectFork("https://arb1.arbitrum.io/rpc", 443366112);
    }
    
    function test_checkFertilizerValues() public {
        // Try to get fertilizer-related values
        // These are read-only functions on the fertilizer facet
        try IBeanstalk(L2_BEANSTALK).totalHarvestable(0) returns (uint256 v) {
            console.log("totalHarvestable(0):", v);
        } catch {}
        try IBeanstalk(L2_BEANSTALK).totalHarvested(0) returns (uint256 v) {
            console.log("totalHarvested(0):", v);
        } catch { console.log("totalHarvested not available"); }
        try IBeanstalk(L2_BEANSTALK).fieldCount() returns (uint256 v) {
            console.log("fieldCount:", v);
        } catch {}
        try IBeanstalk(L2_BEANSTALK).orderLockedBeans() returns (uint256 v) {
            console.log("orderLockedBeans:", v);
        } catch { console.log("orderLockedBeans not available"); }
    }
    
    function test_checkDepositedAmounts() public {
        address[] memory tokens = beanstalk.getWhitelistedTokens();
        console.log("=== Deposited amounts ===");
        for (uint i = 0; i < tokens.length; i++) {
            try beanstalk.getTotalDeposited(tokens[i]) returns (uint256 deposited) {
                console.log("Token", i, "deposited:", deposited);
                console.log("  address:", tokens[i]);
            } catch {
                console.log("Token", i, "deposited: FAILED");
            }
        }
    }
    
    function test_checkSopTokenValues() public {
        // SOP tokens
        try beanstalk.getSopTokens() returns (address[] memory sopTokens) {
            console.log("SOP tokens count:", sopTokens.length);
            for (uint i = 0; i < sopTokens.length; i++) {
                console.log("SOP token:", sopTokens[i]);
                uint256 bal = IERC20(sopTokens[i]).balanceOf(L2_BEANSTALK);
                console.log("  balance at Diamond:", bal);
                try beanstalk.getTotalDeposited(sopTokens[i]) returns (uint256 deposited) {
                    console.log("  deposited:", deposited);
                } catch { console.log("  deposited: N/A"); }
            }
        } catch { console.log("getSopTokens failed"); }
    }
}
