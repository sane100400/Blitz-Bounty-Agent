// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "forge-std/console.sol";

// Try to reproduce the fundsSafu computation to find which token overflows

interface IBeanstalk {
    struct SiloBalances {
        uint128 deposited;
        uint128 depositedBdv;
    }
    struct Amount {
        uint128 amount;
        uint128 bdv;
    }
    
    function getSiloTokens() external view returns (address[] memory);
    function getWhitelistedTokens() external view returns (address[] memory);
    function getWhitelistedWellLpTokens() external view returns (address[] memory);
}

interface IERC20 { function balanceOf(address) external view returns (uint256); }

contract Debug_Entitlements is Test {
    address constant L2_BEANSTALK = 0xD1A0060ba708BC4BCD3DA6C37EFa8deDF015FB70;
    IBeanstalk beanstalk = IBeanstalk(L2_BEANSTALK);
    
    function setUp() public {
        vm.createSelectFork("https://arb1.arbitrum.io/rpc", 443366112);
    }
    
    function test_readWhitelistedTokens() public view {
        // Try to get whitelisted tokens
        try beanstalk.getWhitelistedTokens() returns (address[] memory tokens) {
            console.log("Whitelisted tokens count:", tokens.length);
            for (uint i = 0; i < tokens.length; i++) {
                uint256 bal = IERC20(tokens[i]).balanceOf(L2_BEANSTALK);
                console.log("Token:", tokens[i]);
                console.log("  Balance:", bal);
            }
        } catch {
            console.log("getWhitelistedTokens() failed");
        }
    }
    
    function test_readSiloTokens() public view {
        try beanstalk.getSiloTokens() returns (address[] memory tokens) {
            console.log("Silo tokens count:", tokens.length);
            for (uint i = 0; i < tokens.length; i++) {
                console.log("Token:", tokens[i]);
            }
        } catch {
            console.log("getSiloTokens() failed");
        }
    }
}
