// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

// STEP 8: Only runs when explicitly selected by --match-contract.
// Forked state is LOCAL, no actual Polygon broadcast or wallet key needed.
// This file deliberately needs no forge-std download.
import {BalancerMEVSniper, IERC20Lite, IBalancerV2VaultLite, IBalancerFlashRecipientLite} from
    "../src/BalancerMEVSniper.sol";

interface VmFork {
    function envString(string calldata name) external returns (string memory value);
    function envOr(string calldata name, uint256 defaultValue) external returns (uint256 value);
    function createSelectFork(string calldata urlOrAlias, uint256 blockNumber) external returns (uint256 forkId);
    function expectRevert(bytes calldata revertData) external;
}

interface IV2FactoryFork {
    function getPair(address tokenA, address tokenB) external view returns (address pair);
}

interface IV2PairFork {
    function getReserves() external view returns (uint112 r0, uint112 r1, uint32 updatedAt);
}

interface IV2RouterFork {
    function factory() external view returns (address);
    function getAmountsOut(uint256 amountIn, address[] calldata path) external view returns (uint256[] memory amounts);
}

interface IVaultProtocolFeeReader {
    function getProtocolFeesCollector() external view returns (address);
}

interface IFlashFeeReader {
    function getFlashLoanFeePercentage() external view returns (uint256);
}

/// Real callback probe, deliberately borrows ZERO tokens so it works with nonzero fee schedules.
/// It proves the live Vault's callback ABI/caller and the local fork call flow,
/// NOT meaningful loan liquidity or trade profitability.
contract VaultCallbackProbe is IBalancerFlashRecipientLite {
    address public immutable vault;
    address public immutable token;
    bool public callbackSeen;
    uint256 public observedPrincipal;
    uint256 public observedFee;

    constructor(address vault_, address token_) {
        vault = vault_;
        token = token_;
    }

    function runZeroAmount() external {
        address[] memory tokens = new address[](1);
        tokens[0] = token;
        uint256[] memory amounts = new uint256[](1);
        IBalancerV2VaultLite(vault).flashLoan(address(this), tokens, amounts, bytes("STEP8_LOCAL_PROBE"));
        require(callbackSeen, "Live Vault did not callback");
    }

    function receiveFlashLoan(
        address[] memory tokens,
        uint256[] memory amounts,
        uint256[] memory feeAmounts,
        bytes memory data
    ) external override {
        require(msg.sender == vault, "Callback not Vault");
        require(tokens.length == 1 && tokens[0] == token, "Wrong token");
        require(amounts.length == 1 && feeAmounts.length == 1, "Wrong arrays");
        require(keccak256(data) == keccak256(bytes("STEP8_LOCAL_PROBE")), "Wrong payload");
        require(amounts[0] == 0 && feeAmounts[0] == 0, "Nonzero probe amount");
        observedPrincipal = amounts[0];
        observedFee = feeAmounts[0];
        callbackSeen = true;
    }
}

/// Proves a nonzero flash loan against the actual Balancer Vault on a local fork.
/// This is NOT an arbitrage-success test. No swap or DEX leg is executed.
contract VaultNonzeroRepaymentProbe is IBalancerFlashRecipientLite {
    address public immutable vault;
    address public immutable token;
    bool public callbackSeen;
    uint256 public principalSeen;
    uint256 public feeSeen;

    constructor(address vault_, address token_) {
        vault = vault_;
        token = token_;
    }

    function run(uint256 amount) external {
        require(amount > 0, "Nonzero principal required");
        address[] memory tokens = new address[](1);
        tokens[0] = token;
        uint256[] memory amounts = new uint256[](1);
        amounts[0] = amount;
        IBalancerV2VaultLite(vault).flashLoan(address(this), tokens, amounts, bytes("STEP8_NONZERO_LOAN"));
        require(callbackSeen, "Nonzero loan callback not seen");
    }

    function receiveFlashLoan(
        address[] memory tokens,
        uint256[] memory amounts,
        uint256[] memory fees,
        bytes memory data
    ) external override {
        require(msg.sender == vault, "Callback not Vault");
        require(tokens.length == 1 && tokens[0] == token, "Unexpected token");
        require(amounts.length == 1 && fees.length == 1, "Malformed loan arrays");
        require(keccak256(data) == keccak256(bytes("STEP8_NONZERO_LOAN")), "Wrong payload");
        require(amounts[0] > 0, "Expected nonzero loan");
        require(IERC20Lite(token).balanceOf(address(this)) >= amounts[0] + fees[0], "Cannot repay loan");
        callbackSeen = true;
        principalSeen = amounts[0];
        feeSeen = fees[0];
        require(IERC20Lite(token).transfer(vault, amounts[0] + fees[0]), "Repayment transfer failed");
    }
}

contract BalancerMEVSniperForkTest {
    VmFork internal constant vm = VmFork(address(uint160(uint256(keccak256("hevm cheat code")))));

    // From user's verified live Polygon pairs at block 96,179,857.
    address internal constant VAULT = 0xBA12222222228d8Ba445958a75a0704d566BF2C8;
    address internal constant QUICK = 0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff;
    address internal constant SUSHI = 0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506;
    address internal constant QUICK_FACTORY = address(bytes20(hex"5757371414417b8c6caad45baef941abc7d3ab32"));
    address internal constant SUSHI_FACTORY = address(bytes20(hex"c35dadb65012ec5796536bd9864ed8773abc74c4"));
    address internal constant TOKEN_A = address(bytes20(hex"eb51d9a39ad5eef215dc0bf39a8821ff804a0f01")); // 9 decimals (observed)
    address internal constant TOKEN_B = address(bytes20(hex"8f3cf7ad23cd3cadbd9735aff958023239c6a063")); // 18 decimals (observed)
    address internal constant QUICK_PAIR = address(bytes20(hex"882df4b0fb50a229c3b4124eb18c759911485bfb"));
    address internal constant SUSHI_PAIR = address(bytes20(hex"032890efd9cbc5488b9a77a081173fdc604a07e0"));
    uint256 internal constant DEFAULT_FORK_BLOCK = 96_179_857;
    uint256 internal constant LOAN_AMOUNT = 0.5 ether; // 0.5 TOKEN_B, NOT native POL

    BalancerMEVSniper internal bot;
    event ForkGasEstimate(string scenario, uint256 gasUsedInTest);

    function setUp() public {
        string memory rpcUrl = vm.envString("POLYGON_RPC_URL");
        uint256 forkBlock = vm.envOr("POLYGON_FORK_BLOCK", DEFAULT_FORK_BLOCK);
        vm.createSelectFork(rpcUrl, forkBlock);
        require(block.chainid == 137, "RPC is not Polygon mainnet");
        require(block.number == forkBlock, "Fork block not pinned");
        bot = new BalancerMEVSniper(VAULT, QUICK, SUSHI);
        bot.setTokenAllowed(TOKEN_A, true);
        bot.setTokenAllowed(TOKEN_B, true);
    }

    function _assert(bool condition, string memory message) internal pure {
        require(condition, message);
    }

    function _getFlashFee(uint256 principal) internal view returns (uint256) {
        address feeCollector = IVaultProtocolFeeReader(VAULT).getProtocolFeesCollector();
        _assert(feeCollector.code.length > 0, "Missing live fee collector");
        uint256 pct = IFlashFeeReader(feeCollector).getFlashLoanFeePercentage();
        // Balancer's on-chain fee is a fraction in 1e18 fixed-point units.
        _assert(pct <= 1e16, "Unexpected fee > documented 1% cap");
        uint256 numerator = principal * pct;
        return numerator == 0 ? 0 : (numerator - 1) / 1e18 + 1; // round UP
    }

    function _path(address a, address b) internal pure returns (address[] memory path) {
        path = new address[](2);
        path[0] = a;
        path[1] = b;
    }

    function _trade() internal view returns (BalancerMEVSniper.TradeParams memory p) {
        // The historic snapshot modeled this direction as gross-positive near 0.5 TOKEN_B.
        // Read BOTH quotes directly from live fork contracts to avoid assumptions about fees.
        uint256[] memory first = IV2RouterFork(SUSHI).getAmountsOut(LOAN_AMOUNT, _path(TOKEN_B, TOKEN_A));
        uint256[] memory second = IV2RouterFork(QUICK).getAmountsOut(first[1], _path(TOKEN_A, TOKEN_B));
        _assert(first.length == 2 && second.length == 2, "Unexpected router quotes");
        _assert(first[1] > 0 && second[1] > 0, "Zero leg quote");
        uint256 flashFee = _getFlashFee(LOAN_AMOUNT);
        _assert(second[1] > LOAN_AMOUNT + flashFee + 1, "Pinned fork is not gross-positive after flash fee");

        p = BalancerMEVSniper.TradeParams({
            tokenIn: TOKEN_B,
            tokenOut: TOKEN_A,
            flashAmount: LOAN_AMOUNT,
            routerA: SUSHI,
            routerB: QUICK,
            minOutA: first[1] * 999 / 1000, // 0.1% tolerance, NOT a production slippage policy
            minOutB: second[1] * 999 / 1000,
            minProfit: 1,
            maxFlashFee: flashFee,
            deadline: block.timestamp + 600
        });
    }

    function _reserves(address pair) internal view returns (uint112 r0, uint112 r1) {
        (r0, r1,) = IV2PairFork(pair).getReserves();
    }

    function testForkRealAddressesFactoriesAndPools() public view {
        _assert(VAULT.code.length > 0 && QUICK.code.length > 0 && SUSHI.code.length > 0, "Missing real contracts");
        _assert(IV2RouterFork(QUICK).factory() == QUICK_FACTORY, "Quick factory mismatch");
        _assert(IV2RouterFork(SUSHI).factory() == SUSHI_FACTORY, "Sushi factory mismatch");
        _assert(IV2FactoryFork(QUICK_FACTORY).getPair(TOKEN_A, TOKEN_B) == QUICK_PAIR, "Quick pair mismatch");
        _assert(IV2FactoryFork(SUSHI_FACTORY).getPair(TOKEN_A, TOKEN_B) == SUSHI_PAIR, "Sushi pair mismatch");
        (uint112 q0, uint112 q1) = _reserves(QUICK_PAIR);
        (uint112 s0, uint112 s1) = _reserves(SUSHI_PAIR);
        _assert(q0 > 0 && q1 > 0 && s0 > 0 && s1 > 0, "Empty fork pool");
    }

    function testForkRealVaultFeeRead() public view {
        _assert(_getFlashFee(LOAN_AMOUNT) <= LOAN_AMOUNT / 100, "Flash fee greater than 1%");
        _assert(IERC20Lite(TOKEN_B).balanceOf(VAULT) >= LOAN_AMOUNT, "Insufficient Vault liquidity");
    }

    function testForkRealVaultZeroAmountCallback() public {
        VaultCallbackProbe probe = new VaultCallbackProbe(VAULT, TOKEN_B);
        probe.runZeroAmount();
        _assert(probe.callbackSeen(), "Callback not observed");
    }

    /// Historical token A is fee-on-transfer (5% was observed in the fork trace).
    /// The ordinary Uniswap V2 swap is deliberately incompatible and MUST revert.
    /// This negative test is not evidence of successful arbitrage.
    function testForkTaxedTokenRouteRevertsAndRollsBack() public {
        BalancerMEVSniper.TradeParams memory p = _trade();
        uint256 vaultBefore = IERC20Lite(TOKEN_B).balanceOf(VAULT);
        uint256 botBefore = IERC20Lite(TOKEN_B).balanceOf(address(bot));
        (uint112 q0, uint112 q1) = _reserves(QUICK_PAIR);
        (uint112 s0, uint112 s1) = _reserves(SUSHI_PAIR);

        vm.expectRevert(bytes("UniswapV2: K"));
        bot.executeFlashArbitrage(p);

        _assert(IERC20Lite(TOKEN_B).balanceOf(VAULT) == vaultBefore, "Vault changed after taxed-token revert");
        _assert(IERC20Lite(TOKEN_B).balanceOf(address(bot)) == botBefore, "Bot changed after taxed-token revert");
        (uint112 q0After, uint112 q1After) = _reserves(QUICK_PAIR);
        (uint112 s0After, uint112 s1After) = _reserves(SUSHI_PAIR);
        _assert(q0After == q0 && q1After == q1, "Quick reserves persisted after taxed-token revert");
        _assert(s0After == s0 && s1After == s1, "Sushi reserves persisted after taxed-token revert");
    }

    /// Independently checks nonzero REAL Vault flash-loan principal/repayment.
    /// This confirms Vault integration, not the two-leg arbitrage executor.
    function testForkNonzeroRealVaultLoanIsRepaid() public {
        uint256 fee = _getFlashFee(LOAN_AMOUNT);
        _assert(fee == 0, "Pinned fee changed; update test to pre-fund fee");
        uint256 vaultBefore = IERC20Lite(TOKEN_B).balanceOf(VAULT);
        _assert(vaultBefore >= LOAN_AMOUNT, "Insufficient Vault liquidity");

        VaultNonzeroRepaymentProbe probe = new VaultNonzeroRepaymentProbe(VAULT, TOKEN_B);
        probe.run(LOAN_AMOUNT);

        _assert(probe.callbackSeen(), "Missing nonzero Vault callback");
        _assert(probe.principalSeen() == LOAN_AMOUNT, "Wrong principal in callback");
        _assert(probe.feeSeen() == fee, "Unexpected fee in callback");
        _assert(IERC20Lite(TOKEN_B).balanceOf(VAULT) == vaultBefore, "Vault did not recover principal");
        _assert(IERC20Lite(TOKEN_B).balanceOf(address(probe)) == 0, "Probe has residual tokens");
    }

    function testForkOvertightFirstLegSlippageRevertsAtomically() public {
        BalancerMEVSniper.TradeParams memory p = _trade();
        p.minOutA = type(uint256).max; // Router cannot satisfy this bound.
        uint256 vaultBefore = IERC20Lite(TOKEN_B).balanceOf(VAULT);
        (uint112 s0, uint112 s1) = _reserves(SUSHI_PAIR);
        (bool ok,) = address(bot).call(abi.encodeCall(BalancerMEVSniper.executeFlashArbitrage, (p)));
        _assert(!ok, "Impossible slippage incorrectly succeeded");
        _assert(IERC20Lite(TOKEN_B).balanceOf(VAULT) == vaultBefore, "Vault changed after slippage revert");
        (uint112 s0After, uint112 s1After) = _reserves(SUSHI_PAIR);
        _assert(s0After == s0 && s1After == s1, "Sushi state persisted after revert");
    }

    function testForkUnauthorizedTokenFailsBeforeLoan() public {
        bot.setTokenAllowed(TOKEN_A, false);
        BalancerMEVSniper.TradeParams memory p = _trade();
        vm.expectRevert(bytes("Token not allowed"));
        bot.executeFlashArbitrage(p);
    }
}
