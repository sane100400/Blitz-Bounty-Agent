# Smart Contract Vulnerability Examples

Real CVE diffs and vulnerable/fixed pairs from public audits. Use as reference patterns when analyzing code — if your candidate doesn't structurally resemble these, reconsider.

---

## 1. Reentrancy

### CVE Diff: Read-only reentrancy via Curve pool (2023)

Vyper pools with raw_call before state update allowed attackers to re-enter view functions during callback, reading stale virtual_price.

```solidity
// VULNERABLE — balance read during callback returns stale value
function get_virtual_price() external view returns (uint256) {
    return _get_virtual_price();  // reads balances mid-reentrancy
}

function remove_liquidity() external {
-   raw_call(msg.sender, ...)      // callback BEFORE state update
-   self.balances[i] -= amount
+   self.balances[i] -= amount     // state update FIRST
+   raw_call(msg.sender, ...)      // callback AFTER
}
```

**Root cause:** External call before state update allowed view functions to return stale values, enabling arbitrage via integrating protocols.

### Vulnerable vs Fixed: ERC777 callback reentrancy

```solidity
// VULNERABLE
function withdraw(uint256 amount) external {
    token.safeTransfer(msg.sender, amount);  // ERC777 tokensReceived callback
    balances[msg.sender] -= amount;          // updated AFTER external call
}

// FIXED (CEI pattern)
function withdraw(uint256 amount) external {
    balances[msg.sender] -= amount;          // state update FIRST
    token.safeTransfer(msg.sender, amount);  // external call LAST
}
```

---

## 2. Oracle Manipulation

### CVE Diff: Spot price as oracle — Euler (2023-03)

Euler Finance used Uniswap V3 spot price for liquidation thresholds. Attacker flash-loaned to move the price in a single block.

```solidity
// VULNERABLE — spot price manipulable via flash loan
function getPrice(address token) external view returns (uint256) {
-   (uint160 sqrtPriceX96,,,,,,) = pool.slot0();  // instantaneous
-   return _derivePriceFromSqrt(sqrtPriceX96);
+   (int24 arithmeticMeanTick,) = OracleLibrary.consult(pool, TWAP_WINDOW);
+   return OracleLibrary.getQuoteAtTick(arithmeticMeanTick, ...);
}
```

**Root cause:** `slot0()` returns manipulable spot price. TWAP with sufficient window resists single-block manipulation.

### Vulnerable vs Fixed: Missing staleness check

```solidity
// VULNERABLE — stale price accepted silently
(, int256 price,, uint256 updatedAt,) = feed.latestRoundData();
return uint256(price);

// FIXED
(, int256 price,, uint256 updatedAt,) = feed.latestRoundData();
require(price > 0, "invalid price");
require(block.timestamp - updatedAt < MAX_STALENESS, "stale price");
return uint256(price);
```

---

## 3. Accounting / Precision

### CVE Diff: ERC4626 first depositor inflation (multiple, 2023-2024)

First depositor mints shares, donates tokens to inflate share price, making subsequent deposits round down to 0 shares.

```solidity
// VULNERABLE — no protection against share price manipulation
function convertToShares(uint256 assets) public view returns (uint256) {
-   return totalSupply() == 0
-       ? assets
-       : assets * totalSupply() / totalAssets();  // attacker inflates totalAssets
+   return totalSupply() == 0
+       ? assets
+       : assets * (totalSupply() + OFFSET) / (totalAssets() + OFFSET);  // virtual offset
}
```

**Root cause:** Without virtual offset or minimum deposit, attacker inflates assets-per-share to steal rounding dust from all future depositors.

### Vulnerable vs Fixed: Division before multiplication

```solidity
// VULNERABLE — precision loss from wrong order
uint256 fee = amount / TOTAL * feeRate;  // integer division truncates first

// FIXED — multiply first to preserve precision
uint256 fee = amount * feeRate / TOTAL;
```

---

## 4. Access Control

### CVE Diff: Unprotected initializer (multiple proxy vulns, 2022-2024)

Proxy implementation contracts left `initialize()` callable by anyone after deployment.

```solidity
// VULNERABLE — anyone can call initialize on implementation
function initialize(address admin) external {
-   _admin = admin;
+   require(!_initialized, "already initialized");
+   _initialized = true;
+   _admin = admin;
}
// Better: use OpenZeppelin's `initializer` modifier
```

**Root cause:** Implementation contract's `initialize()` not protected, allowing attacker to take ownership.

### Vulnerable vs Fixed: ecrecover returns address(0)

```solidity
// VULNERABLE — invalid signature silently returns address(0)
address signer = ecrecover(hash, v, r, s);
require(signer == authorized, "unauthorized");  // passes if authorized == address(0)

// FIXED
address signer = ecrecover(hash, v, r, s);
require(signer != address(0), "invalid signature");
require(signer == authorized, "unauthorized");
```

---

## 5. Flash Loan

### CVE Diff: Balance-based accounting manipulation (bZx 2020, variants through 2024)

Using `balanceOf(address(this))` as canonical balance allows attackers to inflate it via flash loan + direct transfer.

```solidity
// VULNERABLE — reads actual balance (inflatable via donation/flash loan)
function getReserves() public view returns (uint256) {
-   return token.balanceOf(address(this));  // attacker can inflate with direct transfer
+   return _internalReserve;                // use tracked accounting only
}
```

**Root cause:** `balanceOf()` reflects actual token holdings including donations. Internal accounting variable cannot be manipulated externally.

### Vulnerable vs Fixed: Donation attack on share price

```solidity
// VULNERABLE — total assets from balance (donatable)
function totalAssets() public view returns (uint256) {
    return token.balanceOf(address(this));  // inflatable via direct transfer
}

// FIXED — use internal tracking
function totalAssets() public view returns (uint256) {
    return _totalDeposited + _totalYieldAccrued;  // only contract-tracked values
}
```

---

## 6. Token Edge Cases

### CVE Diff: Fee-on-transfer assumption (multiple, 2023-2024)

Protocol assumed `transferFrom(sender, address(this), amount)` delivers exactly `amount`, but fee-on-transfer tokens deliver less.

```solidity
// VULNERABLE — assumes full amount received
function deposit(uint256 amount) external {
-   token.transferFrom(msg.sender, address(this), amount);
-   balances[msg.sender] += amount;      // credits more than received
+   uint256 before = token.balanceOf(address(this));
+   token.transferFrom(msg.sender, address(this), amount);
+   uint256 received = token.balanceOf(address(this)) - before;
+   balances[msg.sender] += received;    // credits actual received amount
}
```

**Root cause:** Fee-on-transfer tokens deduct a fee during transfer. Without before/after balance check, protocol credits phantom tokens.

### Vulnerable vs Fixed: Blacklist token DoS in batch loop

```solidity
// VULNERABLE — one blacklisted recipient blocks entire batch
function batchTransfer(address[] calldata to, uint256[] calldata amounts) external {
    for (uint i = 0; i < to.length; i++) {
        USDC.transfer(to[i], amounts[i]);  // reverts if to[i] is blacklisted
    }
}

// FIXED — pull pattern or try/catch
function claimable(address user) external view returns (uint256) {
    return _pendingAmounts[user];
}
function claim() external {
    uint256 amount = _pendingAmounts[msg.sender];
    _pendingAmounts[msg.sender] = 0;
    USDC.transfer(msg.sender, amount);  // only caller affected
}
```
