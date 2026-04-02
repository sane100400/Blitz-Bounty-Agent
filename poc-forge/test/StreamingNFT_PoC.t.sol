// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "forge-std/Test.sol";
import "forge-std/console.sol";

// forge test --match-contract StreamingNFT_PoC \
//   --fork-url https://berachain-rpc.publicnode.com -vv

interface IStreamingNFT {
    function claimRewards(uint256 streamId) external;
    function createStream(uint256 tokenId) external;
    function getClaimableRewards(uint256 streamId) external view returns (uint256);
    function claimedTimestamp(uint256) external view returns (uint256);
    function cliffUnlockAmount() external view returns (uint256);
    function vestedRewards() external view returns (uint256);
    function cliffEndTimestamp() external view returns (uint256);
    function isPayMaster(address) external view returns (bool);
    error InvalidOrigin(address origin);
}

interface IERC721 {
    function ownerOf(uint256 tokenId) external view returns (address);
    function totalSupply() external view returns (uint256);
    function tokenByIndex(uint256 index) external view returns (uint256);
}

contract StreamingNFT_PoC is Test {

    IStreamingNFT constant BAND_STREAMING = IStreamingNFT(0xaf30baa667Ce52c1fE5702A0F8CE9A31f0d751B6);
    IStreamingNFT constant BIT_STREAMING  = IStreamingNFT(0x979EFC29797884c3342143eA7b91E55342F2f408);
    IERC721       constant BAND_NFT       = IERC721(0x7711B2Eb2451259dbF211e30157ceB7CFeb79a19);
    IERC721       constant BIT_NFT        = IERC721(0x72D876D9cdf4001b836f8E47254d0551EdA2eebB);

    // confirmed contract holders (mainnet, 2026-03-18)
    address constant NFT_VAULT   = 0x05113720A7AbC229124b8682DFDB2521E49608C4; // 8 BandBears + 15 BitBears
    address constant GNOSIS_SAFE = 0xcE71670294a9101406941F2cC943e6B042573736; // 3 BitBears

    function findTokenOf(IERC721 nft, address target) internal view returns (bool, uint256) {
        uint256 total = nft.totalSupply();
        for (uint256 i = 0; i < total; i++) {
            uint256 tid = nft.tokenByIndex(i);
            if (nft.ownerOf(tid) == target) return (true, tid);
        }
        return (false, 0);
    }

    // Primary PoC: NFT_VAULT (contract) holds BandBear #966 with an existing stream.
    // claimRewards() always reverts because tx.origin (EOA) != beneficiary (NFT_VAULT).
    // No vm.prank — tx.origin is the Forge default EOA (0x1804...1f38).
    function test_ContractWalletCannotClaim() public {
        assertGt(block.timestamp, BAND_STREAMING.cliffEndTimestamp(), "cliff not passed");
        assertTrue(NFT_VAULT.code.length > 0, "NFT_VAULT must be a contract");

        (bool found, uint256 tokenId) = findTokenOf(BAND_NFT, NFT_VAULT);
        assertTrue(found, "NFT_VAULT holds no BandBears");

        console.log("NFT_VAULT BandBear tokenId:", tokenId);
        console.log("tx.origin:", tx.origin);

        bool streamExists = BAND_STREAMING.claimedTimestamp(tokenId) != 0;

        if (streamExists) {
            uint256 claimable = BAND_STREAMING.getClaimableRewards(tokenId);
            assertGt(claimable, 0);
            console.log("claimable (wei):", claimable);

            vm.expectRevert(abi.encodeWithSelector(IStreamingNFT.InvalidOrigin.selector, tx.origin));
            BAND_STREAMING.claimRewards(tokenId);
            console.log("[PASS] claimRewards reverted: InvalidOrigin(tx.origin)");
        } else {
            console.log("no stream yet - createStream also blocked:");
            vm.expectRevert(abi.encodeWithSelector(IStreamingNFT.InvalidOrigin.selector, tx.origin));
            BAND_STREAMING.createStream(tokenId);
            console.log("[PASS] createStream reverted: InvalidOrigin(tx.origin)");
        }

        uint256 locked = BAND_STREAMING.cliffUnlockAmount() + BAND_STREAMING.vestedRewards();
        console.log("locked per NFT (cliff+vested, wei):", locked);
    }

    // Control: EOA holder can claim normally.
    // vm.prank(eoaOwner, eoaOwner) sets both msg.sender and tx.origin to the same EOA — valid.
    function test_EOAHolderCanClaim() public {
        assertGt(block.timestamp, BAND_STREAMING.cliffEndTimestamp(), "cliff not passed");

        uint256 total = BAND_NFT.totalSupply();
        address eoaOwner;
        uint256 eoaTokenId;

        for (uint256 i = 0; i < total; i++) {
            uint256 tid = BAND_NFT.tokenByIndex(i);
            address owner = BAND_NFT.ownerOf(tid);
            if (owner.code.length == 0 && BAND_STREAMING.claimedTimestamp(tid) != 0) {
                eoaOwner   = owner;
                eoaTokenId = tid;
                break;
            }
        }

        if (eoaOwner == address(0)) { console.log("no EOA stream found, skip"); return; }

        uint256 claimable = BAND_STREAMING.getClaimableRewards(eoaTokenId);
        if (claimable == 0) { console.log("nothing claimable, skip"); return; }

        console.log("EOA:", eoaOwner);
        console.log("tokenId:", eoaTokenId, "claimable (wei):", claimable);

        vm.prank(eoaOwner, eoaOwner);
        BAND_STREAMING.claimRewards(eoaTokenId);
        console.log("[PASS] EOA claimed successfully");
    }

    // Financial impact summary.
    function test_FinancialImpact() public view {
        uint256 bandPerNFT = BAND_STREAMING.cliffUnlockAmount() + BAND_STREAMING.vestedRewards();
        uint256 bitPerNFT  = BIT_STREAMING.cliffUnlockAmount()  + BIT_STREAMING.vestedRewards();

        uint256 total = (8 + 2) * bandPerNFT + (15 + 3 + 2) * bitPerNFT;

        console.log("BandBear cliff+vested per NFT:", bandPerNFT / 1e18, "BERA");
        console.log("BitBear  cliff+vested per NFT:", bitPerNFT  / 1e18, "BERA");
        console.log("Total locked (30 NFTs, 2 of 6 collections):", total / 1e18, "BERA");
    }
}
