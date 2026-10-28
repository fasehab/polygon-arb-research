// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import {BalancerMEVSniper, IERC20Lite, IBalancerFlashRecipientLite} from "../src/BalancerMEVSniper.sol";

// No forge-std dependency: these tests can run in a minimal local Foundry project.
contract MockToken is IERC20Lite {
    mapping(address => uint256) public override balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function mint(address to, uint256 amount) external { balanceOf[to] += amount; }

    function approve(address spender, uint256 amount) external override returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transfer(address to, uint256 amount) external override returns (bool) {
        _transfer(msg.sender, to, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        uint256 approved = allowance[from][msg.sender];
        require(approved >= amount, "Allowance low");
        allowance[from][msg.sender] = approved - amount;
        _transfer(from, to, amount);
        return true;
    }

    function _transfer(address from, address to, uint256 amount) internal {
        require(balanceOf[from] >= amount, "Balance low");
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
    }
}

contract MockRouter {
    struct Ratio { uint256 numerator; uint256 denominator; }
    mapping(address => mapping(address => Ratio)) public ratios;
    bool public shortOutput;

    function setRatio(address from, address to, uint256 numerator, uint256 denominator) external {
        require(denominator != 0, "Denominator zero");
        ratios[from][to] = Ratio(numerator, denominator);
    }

    function setShortOutput(bool value) external { shortOutput = value; }

    function swapExactTokensForTokens(
        uint256 amountIn, uint256 amountOutMin, address[] calldata path,
        address to, uint256 deadline
    ) external returns (uint256[] memory amounts) {
        require(deadline >= block.timestamp, "Expired");
        require(path.length == 2, "Two tokens required");
        Ratio memory r = ratios[path[0]][path[1]];
        require(r.denominator != 0, "No rate");
        uint256 outAmount = amountIn * r.numerator / r.denominator;
        require(outAmount >= amountOutMin, "Router slippage");
        MockToken(path[0]).transferFrom(msg.sender, address(this), amountIn);
        // Test a token/router combination which returns a nominal output larger than transfer.
        MockToken(path[1]).transfer(to, shortOutput ? outAmount - 1 : outAmount);
        amounts = new uint256[](2);
        amounts[0] = amountIn;
        amounts[1] = outAmount;
    }
}

contract MockVault {
    uint256 public feeBps;
    bool public tamperPayload;
    bool public skipCallback;

    function setFeeBps(uint256 bps) external { feeBps = bps; }
    function setTamperPayload(bool value) external { tamperPayload = value; }
    function setSkipCallback(bool value) external { skipCallback = value; }

    function flashLoan(
        address recipient, address[] memory tokens,
        uint256[] memory amounts, bytes memory userData
    ) external {
        require(tokens.length == 1 && amounts.length == 1, "Mock supports one token");
        MockToken token = MockToken(tokens[0]);
        uint256 beforeLoan = token.balanceOf(address(this));
        uint256 feeAmount = amounts[0] * feeBps / 10_000;
        token.transfer(recipient, amounts[0]);

        if (!skipCallback) {
            uint256[] memory fees = new uint256[](1);
            fees[0] = feeAmount;
            if (tamperPayload) userData = abi.encodePacked(userData, bytes1(0x99));
            IBalancerFlashRecipientLite(recipient).receiveFlashLoan(tokens, amounts, fees, userData);
        }
        require(token.balanceOf(address(this)) >= beforeLoan + feeAmount, "Vault underpaid");
    }
}

contract NonOwnerCaller {
    function attempt(BalancerMEVSniper sniper, BalancerMEVSniper.TradeParams calldata p) external {
        sniper.executeFlashArbitrage(p);
    }
}

contract BalancerMEVSniperTest {
    MockToken internal a;
    MockToken internal b;
    MockVault internal vault;
    MockRouter internal quick;
    MockRouter internal sushi;
    BalancerMEVSniper internal bot;

    uint256 internal constant LOAN = 1_000 ether;

    function setUp() public {
        a = new MockToken();
        b = new MockToken();
        vault = new MockVault();
        quick = new MockRouter();
        sushi = new MockRouter();
        bot = new BalancerMEVSniper(address(vault), address(quick), address(sushi));
        bot.setTokenAllowed(address(a), true);
        bot.setTokenAllowed(address(b), true);

        a.mint(address(vault), 1_000_000 ether);
        b.mint(address(quick), 1_000_000 ether);
        a.mint(address(sushi), 1_000_000 ether);
        quick.setRatio(address(a), address(b), 11, 10); // 1000 A -> 1100 B
        sushi.setRatio(address(b), address(a), 1, 1);  // 1100 B -> 1100 A
    }

    function _trade() internal view returns (BalancerMEVSniper.TradeParams memory p) {
        p = BalancerMEVSniper.TradeParams({
            tokenIn: address(a), tokenOut: address(b), flashAmount: LOAN,
            routerA: address(quick), routerB: address(sushi),
            minOutA: 1_090 ether, minOutB: 1_050 ether,
            minProfit: 50 ether, maxFlashFee: 20 ether,
            deadline: block.timestamp + 60
        });
    }

    function _mustRevert(BalancerMEVSniper.TradeParams memory p) internal {
        (bool ok,) = address(bot).call(abi.encodeCall(BalancerMEVSniper.executeFlashArbitrage, (p)));
        require(!ok, "Expected revert");
    }

    function testProfitableArbitrageAndRepayment() public {
        bot.executeFlashArbitrage(_trade());
        require(a.balanceOf(address(bot)) == 100 ether, "Profit mismatch");
        require(a.balanceOf(address(vault)) == 1_000_000 ether, "Loan not repaid");
    }

    function testFlashFeeIsRepaidAndDeductedFromProfit() public {
        vault.setFeeBps(100); // 1% of 1,000 A = 10 A
        bot.executeFlashArbitrage(_trade());
        require(a.balanceOf(address(bot)) == 90 ether, "Net token profit mismatch");
        require(a.balanceOf(address(vault)) == 1_000_010 ether, "Fee not repaid");
    }

    function testRejectsFeeAboveOwnerCap() public {
        vault.setFeeBps(300); // 30 A > 20 A max
        _mustRevert(_trade());
        require(a.balanceOf(address(bot)) == 0, "Unexpected retained funds");
    }

    function testExistingBalanceCannotSubsidizeLosingTrade() public {
        a.mint(address(bot), 500 ether);
        sushi.setRatio(address(b), address(a), 4, 5); // 1100 B -> 880 A; new trade loses 120
        _mustRevert(_trade());
        require(a.balanceOf(address(bot)) == 500 ether, "Existing profit was spent");
        require(a.balanceOf(address(vault)) == 1_000_000 ether, "Vault balance changed");
    }

    function testRejectsInsufficientOutputAtFirstLeg() public {
        BalancerMEVSniper.TradeParams memory p = _trade();
        p.minOutA = 1_200 ether;
        _mustRevert(p);
    }

    function testRejectsInsufficientOutputAtSecondLeg() public {
        BalancerMEVSniper.TradeParams memory p = _trade();
        p.minOutB = 1_200 ether;
        _mustRevert(p);
    }

    function testRejectsRouterMismatches() public {
        BalancerMEVSniper.TradeParams memory p = _trade();
        p.routerB = address(quick);
        _mustRevert(p);
    }

    function testRejectsUnapprovedTokens() public {
        bot.setTokenAllowed(address(b), false);
        _mustRevert(_trade());
    }

    function testRejectsExpiredTrade() public {
        BalancerMEVSniper.TradeParams memory p = _trade();
        p.deadline = 0;
        _mustRevert(p);
    }

    function testOnlyOwnerCanExecute() public {
        NonOwnerCaller outsider = new NonOwnerCaller();
        BalancerMEVSniper.TradeParams memory p = _trade();
        (bool ok,) = address(outsider).call(abi.encodeCall(NonOwnerCaller.attempt, (bot, p)));
        require(!ok, "Nonowner able to trade");
    }

    function testRejectsForgedCallback() public {
        address[] memory tokens = new address[](1);
        tokens[0] = address(a);
        uint256[] memory amounts = new uint256[](1);
        amounts[0] = LOAN;
        uint256[] memory fees = new uint256[](1);
        (bool ok,) = address(bot).call(abi.encodeCall(BalancerMEVSniper.receiveFlashLoan,
            (tokens, amounts, fees, abi.encode(_trade()))));
        require(!ok, "Forged callback succeeded");
    }

    function testRejectsTamperedPayload() public {
        vault.setTamperPayload(true);
        _mustRevert(_trade());
    }

    function testRejectsVaultThatSkipsCallback() public {
        vault.setSkipCallback(true);
        _mustRevert(_trade());
    }

    function testDetectsUnexpectedTokenOutput() public {
        quick.setShortOutput(true);
        _mustRevert(_trade());
    }

    function testPausingAndUnpausing() public {
        bot.setPaused(true);
        _mustRevert(_trade());
        bot.setPaused(false);
        bot.executeFlashArbitrage(_trade());
        require(a.balanceOf(address(bot)) == 100 ether, "Restart failed");
    }

    function testReverseRouterOrderIsAllowed() public {
        // Profit with SushiSwap first, QuickSwap second (the other supported direction).
        b.mint(address(sushi), 1_000_000 ether);
        a.mint(address(quick), 1_000_000 ether);
        sushi.setRatio(address(a), address(b), 12, 10);
        quick.setRatio(address(b), address(a), 1, 1);
        BalancerMEVSniper.TradeParams memory p = _trade();
        p.routerA = address(sushi);
        p.routerB = address(quick);
        bot.executeFlashArbitrage(p);
        require(a.balanceOf(address(bot)) == 200 ether, "Reverse route returned wrong amount");
    }

    function testWithdrawOnlyOwnerWhenIdle() public {
        bot.executeFlashArbitrage(_trade());
        uint256 before = a.balanceOf(address(this));
        bot.withdraw(address(a));
        require(a.balanceOf(address(bot)) == 0, "Balance not withdrawn");
        require(a.balanceOf(address(this)) == before + 100 ether, "Owner did not receive profit");
    }
}
