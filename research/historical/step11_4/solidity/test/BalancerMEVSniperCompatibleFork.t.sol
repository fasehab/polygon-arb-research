// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

// STEP 8.2: Uses REAL Polygon token, pair, router and Vault code on a pinned fork.
// One experiment DONATES tokens to a real V2 pair to create a CONTROLLED,
// ARTIFICIAL local-fork price difference. This is NOT a historical arbitrage,
// a trade opportunity, or evidence of profit after gas/inclusion.
// No wallet, broadcast or Mainnet deployment.
import {BalancerMEVSniper, IERC20Lite} from "../src/BalancerMEVSniper.sol";

interface VmCompatibleFork {
    function envString(string calldata key) external returns (string memory);
    function envOr(string calldata key, uint256 defaultValue) external returns (uint256);
    function createSelectFork(string calldata rpc, uint256 blockNumber) external returns (uint256);
    function prank(address sender) external;
    function expectRevert(bytes calldata reason) external;
}

interface ICompatibleFactory {
    function getPair(address a, address b) external view returns (address);
}

interface ICompatiblePair {
    function token0() external view returns (address);
    function token1() external view returns (address);
    function getReserves() external view returns (uint112 r0, uint112 r1, uint32 timestamp);
    function sync() external;
}

interface ICompatibleRouter {
    function factory() external view returns (address);
    function getAmountsOut(uint256 amountIn, address[] calldata path) external view returns (uint256[] memory);
}

interface IFeeCollectorLocator {
    function getProtocolFeesCollector() external view returns (address);
}

interface IFlashFeePercentage {
    function getFlashLoanFeePercentage() external view returns (uint256);
}

interface ITokenDecimals {
    function decimals() external view returns (uint8);
}

contract BalancerMEVSniperCompatibleForkTest {
    VmCompatibleFork internal constant vm =
        VmCompatibleFork(address(uint160(uint256(keccak256("hevm cheat code")))));

    address internal constant VAULT = 0xBA12222222228d8Ba445958a75a0704d566BF2C8;
    address internal constant QUICK = 0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff;
    address internal constant SUSHI = 0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506;
    address internal constant QUICK_FACTORY = address(bytes20(hex"5757371414417b8c6caad45baef941abc7d3ab32"));
    address internal constant SUSHI_FACTORY = address(bytes20(hex"c35dadb65012ec5796536bd9864ed8773abc74c4"));
    address internal constant DAI = address(bytes20(hex"8f3cf7ad23cd3cadbd9735aff958023239c6a063"));
    address internal constant WETH = address(bytes20(hex"7ceb23fd6bc0add59e62ac25578270cff1b9f619"));
    uint256 internal constant FORK_BLOCK = 96_179_857;
    uint256 internal constant FLASH_AMOUNT = 5 ether; // 5 DAI; 18 decimals

    BalancerMEVSniper internal executor;
    address internal quickPair;
    address internal sushiPair;

    event HistoricalQuotes(uint256 quickThenSushiDai, uint256 sushiThenQuickDai, uint256 principal);
    event SyntheticForkExecution(
        address manipulatedPair,
        uint256 donatedDai,
        uint256 flashAmountDai,
        uint256 intermediateWeth,
        uint256 returnedDai,
        uint256 realizedDai,
        uint256 localGasUsed
    );

    function setUp() public {
        string memory rpc = vm.envString("POLYGON_RPC_URL");
        uint256 number = vm.envOr("POLYGON_FORK_BLOCK", FORK_BLOCK);
        vm.createSelectFork(rpc, number);
        require(block.chainid == 137, "Not Polygon chain 137");
        require(block.number == number, "Fork not pinned");
        executor = new BalancerMEVSniper(VAULT, QUICK, SUSHI);
        executor.setTokenAllowed(DAI, true);
        executor.setTokenAllowed(WETH, true);
        quickPair = ICompatibleFactory(QUICK_FACTORY).getPair(DAI, WETH);
        sushiPair = ICompatibleFactory(SUSHI_FACTORY).getPair(DAI, WETH);
    }

    function _path(address inToken, address outToken) internal pure returns (address[] memory p) {
        p = new address[](2);
        p[0] = inToken;
        p[1] = outToken;
    }

    function _out(address router, uint256 amount, address from, address to) internal view returns (uint256) {
        uint256[] memory amounts = ICompatibleRouter(router).getAmountsOut(amount, _path(from, to));
        require(amounts.length == 2 && amounts[1] > 0, "Bad router quote");
        return amounts[1];
    }

    function _reserves(address pair) internal view returns (uint112 r0, uint112 r1) {
        (r0, r1,) = ICompatiblePair(pair).getReserves();
    }

    function _daiReserve(address pair) internal view returns (uint256) {
        (uint112 r0, uint112 r1) = _reserves(pair);
        address t0 = ICompatiblePair(pair).token0();
        address t1 = ICompatiblePair(pair).token1();
        require((t0 == DAI && t1 == WETH) || (t0 == WETH && t1 == DAI), "Incorrect pool tokens");
        return t0 == DAI ? uint256(r0) : uint256(r1);
    }

    function _fee(uint256 amount) internal view returns (uint256) {
        address collector = IFeeCollectorLocator(VAULT).getProtocolFeesCollector();
        uint256 fraction = IFlashFeePercentage(collector).getFlashLoanFeePercentage();
        require(fraction <= 1e16, "Unusual flash fee; inspect manually");
        // Balancer fixed-point fee rounds UP, unlike a naive basis-point estimate.
        if (fraction == 0) return 0;
        return (amount * fraction - 1) / 1e18 + 1;
    }

    function testForkHistoricalConventionalPoolsPresent() public view {
        require(DAI.code.length > 0 && WETH.code.length > 0, "Missing conventional token");
        require(ITokenDecimals(DAI).decimals() == 18, "Unexpected DAI decimals");
        require(ITokenDecimals(WETH).decimals() == 18, "Unexpected WETH decimals");
        require(ICompatibleRouter(QUICK).factory() == QUICK_FACTORY, "Quick factory mismatch");
        require(ICompatibleRouter(SUSHI).factory() == SUSHI_FACTORY, "Sushi factory mismatch");
        require(quickPair != address(0) && sushiPair != address(0), "One real V2 pool is missing");
        require(quickPair.code.length > 0 && sushiPair.code.length > 0, "Pool has no code");
        require(_daiReserve(quickPair) > 0 && _daiReserve(sushiPair) > 0, "Pool reserve missing");
        require(IERC20Lite(DAI).balanceOf(VAULT) > FLASH_AMOUNT, "Vault DAI balance too small");
    }

    /// Original historical state, READ ONLY. A quoted positive gross spread is
    /// NOT confirmation the route is executable or profitable after gas.
    function testForkUntouchedHistoricalQuotesRecorded() public {
        require(quickPair != address(0) && sushiPair != address(0), "Missing historic pairs");
        uint256 qFirst = _out(QUICK, FLASH_AMOUNT, DAI, WETH);
        uint256 sFirst = _out(SUSHI, FLASH_AMOUNT, DAI, WETH);
        uint256 quickThenSushi = _out(SUSHI, qFirst, WETH, DAI);
        uint256 sushiThenQuick = _out(QUICK, sFirst, WETH, DAI);
        emit HistoricalQuotes(quickThenSushi, sushiThenQuick, FLASH_AMOUNT);
        // Deliberately make no claim about historic arbitrage profitability.
        require(quickThenSushi > 0 && sushiThenQuick > 0, "Quote unavailable");
    }

    /// This artificially moves ONE real pool's price by DONATING DAI from an
    /// impersonated Vault, solely on the local fork. The donation is not a
    /// transaction that occurred historically, and vault impersonation must
    /// NEVER occur in production. The real executor still calls the real Vault
    /// and both real V2 routers/pools after this fixture is prepared.
    function _syntheticDislocation()
        internal
        returns (BalancerMEVSniper.TradeParams memory p, address donatedPair, uint256 donation, uint256 quotedFinal)
    {
        require(quickPair != address(0) && sushiPair != address(0), "Missing real DAI/WETH V2 pool");
        // Donate to the LOWER-DAI-reserve pool for a visible price displacement.
        bool useQuick = _daiReserve(quickPair) <= _daiReserve(sushiPair);
        donatedPair = useQuick ? quickPair : sushiPair;
        address buyRouter = useQuick ? SUSHI : QUICK;
        address sellRouter = useQuick ? QUICK : SUSHI;
        uint256 vaultDai = IERC20Lite(DAI).balanceOf(VAULT);
        require(vaultDai > FLASH_AMOUNT + 100 ether, "Vault insufficient for controlled fixture");
        uint256 reserveDai = _daiReserve(donatedPair);
        uint256 available = vaultDai - FLASH_AMOUNT - 100 ether;
        uint256 cap = reserveDai / 2;
        donation = available < cap ? available : cap;
        require(donation > reserveDai / 100, "Controlled donation too small for this pinned fork");

        uint256 balanceBefore = IERC20Lite(DAI).balanceOf(donatedPair);
        vm.prank(VAULT); // impersonation happens ONLY inside this local Foundry fork
        require(IERC20Lite(DAI).transfer(donatedPair, donation), "Fork fixture DAI transfer failed");
        require(IERC20Lite(DAI).balanceOf(donatedPair) == balanceBefore + donation,
            "DAI fee-on-transfer detected: exclude token");
        ICompatiblePair(donatedPair).sync();

        uint256 firstOut = _out(buyRouter, FLASH_AMOUNT, DAI, WETH);
        quotedFinal = _out(sellRouter, firstOut, WETH, DAI);
        uint256 flashFee = _fee(FLASH_AMOUNT);
        require(quotedFinal > FLASH_AMOUNT + flashFee + 1,
            "Synthetic fork price spread is insufficient; do not weaken profit check");
        p = BalancerMEVSniper.TradeParams({
            tokenIn: DAI,
            tokenOut: WETH,
            flashAmount: FLASH_AMOUNT,
            routerA: buyRouter,
            routerB: sellRouter,
            minOutA: firstOut,
            minOutB: quotedFinal,
            minProfit: 1,
            maxFlashFee: flashFee,
            deadline: block.timestamp + 600
        });
    }

    /// A genuine two-swap call path against REAL forked contracts, but success
    /// is deliberately induced through an ARTIFICIAL local fork pool donation.
    function testForkSyntheticDislocationAllowsFullLoanSwapsRepayment() public {
        (BalancerMEVSniper.TradeParams memory p, address manipulatedPair, uint256 donation, uint256 expectedFinal) =
            _syntheticDislocation();
        uint256 vaultBefore = IERC20Lite(DAI).balanceOf(VAULT);
        uint256 botBefore = IERC20Lite(DAI).balanceOf(address(executor));
        uint256 firstExpected = _out(p.routerA, FLASH_AMOUNT, DAI, WETH);
        uint256 gasBefore = gasleft();
        executor.executeFlashArbitrage(p);
        uint256 gasUsed = gasBefore - gasleft();
        uint256 botAfter = IERC20Lite(DAI).balanceOf(address(executor));
        require(IERC20Lite(DAI).balanceOf(VAULT) == vaultBefore + p.maxFlashFee,
            "Real Vault was not repaid");
        require(botAfter > botBefore, "No newly realized token profit");
        uint256 expectedProfit = expectedFinal - FLASH_AMOUNT - p.maxFlashFee;
        require(botAfter - botBefore == expectedProfit, "Realized result differs from both actual router quotes");
        require(IERC20Lite(WETH).balanceOf(address(executor)) == 0, "Unexpected residual WETH");
        emit SyntheticForkExecution(manipulatedPair, donation, FLASH_AMOUNT,
            firstExpected, expectedFinal, expectedProfit, gasUsed);
    }

    function testForkSyntheticDislocationHighProfitFloorRollsBack() public {
        (BalancerMEVSniper.TradeParams memory p, , , uint256 expectedFinal) = _syntheticDislocation();
        uint256 earned = expectedFinal - FLASH_AMOUNT - p.maxFlashFee;
        p.minProfit = earned + 1; // impossible by exactly one smallest unit
        uint256 vaultBefore = IERC20Lite(DAI).balanceOf(VAULT);
        (uint112 qr0, uint112 qr1) = _reserves(quickPair);
        (uint112 sr0, uint112 sr1) = _reserves(sushiPair);
        vm.expectRevert(bytes("Insufficient NEW profit"));
        executor.executeFlashArbitrage(p);
        require(IERC20Lite(DAI).balanceOf(VAULT) == vaultBefore, "Vault balance changed on revert");
        (uint112 q0, uint112 q1) = _reserves(quickPair);
        (uint112 s0, uint112 s1) = _reserves(sushiPair);
        require(q0 == qr0 && q1 == qr1 && s0 == sr0 && s1 == sr1,
            "Two leg state changes survived reverted transaction");
    }
}
