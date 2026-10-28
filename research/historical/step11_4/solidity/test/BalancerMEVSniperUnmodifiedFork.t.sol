// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

// STEP 10 — ORIGINAL, UNMODIFIED POLYGON HISTORICAL STATE.
// No prank, deal, mint, pair-reserve synchronization, reserve override or artificial donation.
// All attempted arbitrage paths must revert and restore balances atomically
// for the explicitly pinned fork + principal sizes used in this test.
// No wallet/private key, no Mainnet broadcasts, no deployment outside the fork.
import {BalancerMEVSniper, IERC20Lite} from "../src/BalancerMEVSniper.sol";

interface VmUnmodified {
    function envString(string calldata key) external returns (string memory);
    function createSelectFork(string calldata url, uint256 number) external returns (uint256);
    function expectRevert(bytes calldata reason) external;
}
interface IV2F {
    function getPair(address, address) external view returns (address);
}
interface IV2P {
    function getReserves() external view returns (uint112, uint112, uint32);
    function token0() external view returns (address);
    function token1() external view returns (address);
}
interface IV2R {
    function getAmountsOut(uint256 amountIn, address[] calldata path) external view returns (uint256[] memory);
}
interface IFeeLookup {
    function getProtocolFeesCollector() external view returns (address);
}
interface IFeeWad {
    function getFlashLoanFeePercentage() external view returns (uint256);
}

contract BalancerMEVSniperUnmodifiedForkTest {
    VmUnmodified internal constant vm=VmUnmodified(address(uint160(uint256(keccak256("hevm cheat code")))));
    address internal constant VAULT=0xBA12222222228d8Ba445958a75a0704d566BF2C8;
    address internal constant QUICK=0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff;
    address internal constant SUSHI=0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506;
    address internal constant DAI=address(bytes20(hex"8f3cf7ad23cd3cadbd9735aff958023239c6a063"));
    address internal constant WETH=address(bytes20(hex"7ceb23fd6bc0add59e62ac25578270cff1b9f619"));
    address internal constant QUICK_FACTORY=address(bytes20(hex"5757371414417b8c6caad45baef941abc7d3ab32"));
    address internal constant SUSHI_FACTORY=address(bytes20(hex"c35dadb65012ec5796536bd9864ed8773abc74c4"));
    uint256 internal constant PINNED_BLOCK=96179857;
    BalancerMEVSniper internal executor;
    address internal quickPair;
    address internal sushiPair;

    event UnmodifiedHistoricalRejection(address firstRouter, uint256 principal, uint256 firstOutWeth,
        uint256 finalOutDai, uint256 flashFee, string reason);

    function setUp() public {
        vm.createSelectFork(vm.envString("POLYGON_RPC_URL"), PINNED_BLOCK);
        require(block.chainid==137 && block.number==PINNED_BLOCK, "Wrong Polygon historical fork");
        executor=new BalancerMEVSniper(VAULT,QUICK,SUSHI);
        executor.setTokenAllowed(DAI,true);
        executor.setTokenAllowed(WETH,true);
        quickPair=IV2F(QUICK_FACTORY).getPair(DAI,WETH);
        sushiPair=IV2F(SUSHI_FACTORY).getPair(DAI,WETH);
        require(quickPair!=address(0) && sushiPair!=address(0), "Missing historical pools");
    }

    function _path(address a,address b) internal pure returns(address[] memory p) {
        p=new address[](2);p[0]=a;p[1]=b;
    }
    function _quote(address router,uint256 amount,address a,address b) internal view returns(uint256) {
        uint256[] memory amounts=IV2R(router).getAmountsOut(amount,_path(a,b));
        require(amounts.length==2 && amounts[0]==amount && amounts[1]>0,"Bad historical quote");
        return amounts[1];
    }
    function _fee(uint256 principal) internal view returns(uint256) {
        address collector=IFeeLookup(VAULT).getProtocolFeesCollector();
        uint256 wad=IFeeWad(collector).getFlashLoanFeePercentage();
        require(wad<=1e16,"Unexpected Vault fee");
        if(wad==0)return 0;
        return (principal*wad-1)/1e18+1;
    }
    function _reserve(address pair) internal view returns(uint112,uint112) {
        (uint112 r0,uint112 r1,)=IV2P(pair).getReserves();return(r0,r1);
    }
    function _balance(address token,address holder) internal view returns(uint256) {
        return IERC20Lite(token).balanceOf(holder);
    }
    function _assertPoolTokens(address pair) internal view {
        address a=IV2P(pair).token0();address b=IV2P(pair).token1();
        require((a==DAI&&b==WETH)||(a==WETH&&b==DAI),"Wrong pool token ordering");
    }
    function testPinnedUnmodifiedPoolsAndVaultLiquidity() public view {
        _assertPoolTokens(quickPair);_assertPoolTokens(sushiPair);
        require(quickPair!=sushiPair,"Identical pools");
        require(_balance(DAI,VAULT)>25 ether,"Vault insufficient DAI");
        (uint112 q0,uint112 q1)=_reserve(quickPair);
        (uint112 s0,uint112 s1)=_reserve(sushiPair);
        require(q0>0&&q1>0&&s0>0&&s1>0,"Historical liquidity missing");
    }
    function _rejectAndCheckAtomic(address first,uint256 amount) internal {
        address second=first==QUICK ? SUSHI : QUICK;
        require(first==QUICK || first==SUSHI,"Bad router");
        uint256 firstOut=_quote(first,amount,DAI,WETH);
        uint256 secondOut=_quote(second,firstOut,WETH,DAI);
        uint256 fee=_fee(amount);
        // This assertion pins the research conclusion specifically to this
        // old historical snapshot and amount; it is not a general market rule.
        require(secondOut<=amount+fee,"Historical gross-positive path: must investigate, do not force expected revert");
        uint256 vaultBefore=_balance(DAI,VAULT);
        uint256 executorBefore=_balance(DAI,address(executor));
        uint256 quickDaiBefore=_balance(DAI,quickPair);
        uint256 quickWethBefore=_balance(WETH,quickPair);
        uint256 sushiDaiBefore=_balance(DAI,sushiPair);
        uint256 sushiWethBefore=_balance(WETH,sushiPair);
        (uint112 q0,uint112 q1)=_reserve(quickPair);
        (uint112 s0,uint112 s1)=_reserve(sushiPair);
        BalancerMEVSniper.TradeParams memory p=BalancerMEVSniper.TradeParams({
            tokenIn:DAI, tokenOut:WETH, flashAmount:amount, routerA:first, routerB:second,
            minOutA:firstOut, minOutB:1, minProfit:1, maxFlashFee:fee, deadline:block.timestamp+600
        });
        // Both swaps run on real forked contracts before the no-profit guard
        // forces the entire flash loan and pool mutations to revert atomically.
        vm.expectRevert(bytes("Insufficient NEW profit"));
        executor.executeFlashArbitrage(p);
        require(_balance(DAI,VAULT)==vaultBefore,"Vault changed after revert");
        require(_balance(DAI,address(executor))==executorBefore,"Executor retained debt or profit");
        require(_balance(DAI,quickPair)==quickDaiBefore && _balance(WETH,quickPair)==quickWethBefore,
            "QuickSwap balances changed");
        require(_balance(DAI,sushiPair)==sushiDaiBefore && _balance(WETH,sushiPair)==sushiWethBefore,
            "SushiSwap balances changed");
        (uint112 qa,uint112 qb)=_reserve(quickPair);
        (uint112 sa,uint112 sb)=_reserve(sushiPair);
        require(qa==q0&&qb==q1&&sa==s0&&sb==s1,"Historical reserves changed");
        emit UnmodifiedHistoricalRejection(first,amount,firstOut,secondOut,fee,"Insufficient NEW profit");
    }
    function testOriginalQuickThenSushiFiveDaiRevertsAtomically() public { _rejectAndCheckAtomic(QUICK,5 ether); }
    function testOriginalSushiThenQuickFiveDaiRevertsAtomically() public { _rejectAndCheckAtomic(SUSHI,5 ether); }
    function testOriginalQuickThenSushiTwentyFiveDaiRevertsAtomically() public { _rejectAndCheckAtomic(QUICK,25 ether); }
    function testOriginalSushiThenQuickTwentyFiveDaiRevertsAtomically() public { _rejectAndCheckAtomic(SUSHI,25 ether); }
}
