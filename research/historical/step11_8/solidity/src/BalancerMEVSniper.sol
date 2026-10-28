// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

// Step 5: security-focused, UNDEPLOYED development implementation.
// The Rust Step 4 process remains observation-only. Not audited.

interface IERC20Lite {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
    function approve(address spender, uint256 amount) external returns (bool);
}

interface IV2RouterLite {
    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts);
}

// ABI-compatible with Balancer V2 Vault flashLoan: IERC20[] encodes as address[].
interface IBalancerV2VaultLite {
    function flashLoan(
        address recipient,
        address[] memory tokens,
        uint256[] memory amounts,
        bytes memory userData
    ) external;
}

interface IBalancerFlashRecipientLite {
    function receiveFlashLoan(
        address[] memory tokens,
        uint256[] memory amounts,
        uint256[] memory feeAmounts,
        bytes memory userData
    ) external;
}

// Minimal self-contained safe token calls to avoid unpinned package dependencies.
// Replace with pinned, audited OpenZeppelin SafeERC20 before production review.
library SafeTokenLite {
    function _call(IERC20Lite token, bytes memory data) private {
        require(address(token).code.length != 0, "Token not contract");
        (bool ok, bytes memory result) = address(token).call(data);
        require(ok, "Token call reverted");
        require(result.length == 0 || (result.length == 32 && abi.decode(result, (bool))), "Token operation failed");
    }

    function safeTransfer(IERC20Lite token, address to, uint256 amount) internal {
        _call(token, abi.encodeWithSelector(IERC20Lite.transfer.selector, to, amount));
    }

    // Zero-first allowances accommodate tokens such as USDT. An exact allowance
    // is cleared again after a successful router interaction.
    function forceApprove(IERC20Lite token, address spender, uint256 amount) internal {
        _call(token, abi.encodeWithSelector(IERC20Lite.approve.selector, spender, 0));
        if (amount != 0) {
            _call(token, abi.encodeWithSelector(IERC20Lite.approve.selector, spender, amount));
        }
    }
}

contract BalancerMEVSniper is IBalancerFlashRecipientLite {
    using SafeTokenLite for IERC20Lite;

    address public constant POLYGON_BALANCER_V2_VAULT = 0xBA12222222228d8Ba445958a75a0704d566BF2C8;
    address public constant POLYGON_QUICKSWAP_V2_ROUTER = 0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff;
    address public constant POLYGON_SUSHISWAP_V2_ROUTER = 0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506;

    address public immutable owner;
    address public immutable vault;
    address public immutable quickswapRouter;
    address public immutable sushiswapRouter;
    bool public paused;
    mapping(address => bool) public allowedToken;

    enum Status { IDLE, REQUESTED, IN_CALLBACK }
    Status private status;
    bytes32 private expectedPayloadHash;
    uint256 private startingBalance;

    struct TradeParams {
        address tokenIn;
        address tokenOut;
        uint256 flashAmount;
        address routerA;
        address routerB;
        uint256 minOutA;
        uint256 minOutB;
        uint256 minProfit;
        uint256 maxFlashFee; // denominated in tokenIn's smallest units
        uint256 deadline;
    }

    event TokenAllowed(address indexed token, bool allowed);
    event PauseChanged(bool paused);
    event FlashArbitrageExecuted(
        address indexed tokenIn,
        address indexed tokenOut,
        address indexed routerA,
        address routerB,
        uint256 principal,
        uint256 flashFee,
        uint256 receivedIntermediate,
        uint256 realizedProfit // tokenIn smallest units; EXCLUDES external gas expenses
    );
    event ProfitWithdrawn(address indexed token, address indexed recipient, uint256 amount);

    modifier onlyOwner() {
        require(msg.sender == owner, "Only owner");
        _;
    }

    modifier whenIdle() {
        require(status == Status.IDLE, "Flash loan in progress");
        _;
    }

    constructor(address vault_, address quickswap_, address sushiswap_) {
        require(vault_ != address(0) && quickswap_ != address(0) && sushiswap_ != address(0), "Zero dependency");
        require(vault_.code.length > 0 && quickswap_.code.length > 0 && sushiswap_.code.length > 0, "Dependency has no code");
        require(vault_ != quickswap_ && vault_ != sushiswap_ && quickswap_ != sushiswap_, "Duplicate dependency");
        // Polygon production deployments must use the known Step 1 verified contracts.
        // Local development networks (e.g. Anvil chainId 31337) may inject mocks.
        if (block.chainid == 137) {
            require(vault_ == POLYGON_BALANCER_V2_VAULT, "Wrong Polygon Vault");
            require(quickswap_ == POLYGON_QUICKSWAP_V2_ROUTER, "Wrong QuickSwap router");
            require(sushiswap_ == POLYGON_SUSHISWAP_V2_ROUTER, "Wrong SushiSwap router");
        }
        owner = msg.sender;
        vault = vault_;
        quickswapRouter = quickswap_;
        sushiswapRouter = sushiswap_;
    }

    function setTokenAllowed(address token, bool allowed) external onlyOwner whenIdle {
        require(token != address(0) && token.code.length > 0, "Invalid token");
        allowedToken[token] = allowed;
        emit TokenAllowed(token, allowed);
    }

    function setPaused(bool value) external onlyOwner whenIdle {
        paused = value;
        emit PauseChanged(value);
    }

    function _validateTrade(TradeParams memory p) private view {
        require(!paused, "Paused");
        require(p.tokenIn != p.tokenOut && p.tokenIn != address(0) && p.tokenOut != address(0), "Invalid token pair");
        require(allowedToken[p.tokenIn] && allowedToken[p.tokenOut], "Token not allowed");
        require(
            (p.routerA == quickswapRouter && p.routerB == sushiswapRouter) ||
            (p.routerA == sushiswapRouter && p.routerB == quickswapRouter),
            "Router route not allowed"
        );
        require(p.flashAmount > 0 && p.minOutA > 0 && p.minOutB > 0 && p.minProfit > 0, "Zero trade limit");
        require(p.deadline >= block.timestamp, "Trade expired");
    }

    // BREAKING ABI CHANGE from Step 1: accepts one TradeParams tuple, NOT 6 scalars.
    // Neither this function nor this contract is called by the read-only Step 4 Rust binary.
    function executeFlashArbitrage(TradeParams calldata p) external onlyOwner whenIdle {
        _validateTrade(p);
        startingBalance = IERC20Lite(p.tokenIn).balanceOf(address(this));
        bytes memory payload = abi.encode(p);
        expectedPayloadHash = keccak256(payload);
        status = Status.REQUESTED;

        address[] memory tokens = new address[](1);
        tokens[0] = p.tokenIn;
        uint256[] memory amounts = new uint256[](1);
        amounts[0] = p.flashAmount;
        IBalancerV2VaultLite(vault).flashLoan(address(this), tokens, amounts, payload);
        require(status == Status.IDLE, "Vault did not complete callback");
    }

    function receiveFlashLoan(
        address[] memory tokens,
        uint256[] memory amounts,
        uint256[] memory feeAmounts,
        bytes memory userData
    ) external override {
        require(msg.sender == vault, "Only Vault");
        require(status == Status.REQUESTED, "Unexpected callback");
        require(keccak256(userData) == expectedPayloadHash, "Payload mismatch");
        require(tokens.length == 1 && amounts.length == 1 && feeAmounts.length == 1, "Invalid loan arrays");

        TradeParams memory p = abi.decode(userData, (TradeParams));
        require(tokens[0] == p.tokenIn && amounts[0] == p.flashAmount, "Loan mismatch");
        require(feeAmounts[0] <= p.maxFlashFee, "Flash fee exceeds cap");
        status = Status.IN_CALLBACK; // disallow nested/replayed callbacks
        _executeAndRepay(p, feeAmounts[0]);
    }

    function _executeAndRepay(TradeParams memory p, uint256 flashFee) private {
        IERC20Lite tokenIn = IERC20Lite(p.tokenIn);
        // Reject fee-on-transfer/misaligned loan receipt rather than using old balance.
        require(tokenIn.balanceOf(address(this)) == startingBalance + p.flashAmount, "Incorrect flash receipt");

        uint256 intermediate = _swap(p.routerA, p.tokenIn, p.tokenOut, p.flashAmount, p.minOutA, p.deadline);
        _swap(p.routerB, p.tokenOut, p.tokenIn, intermediate, p.minOutB, p.deadline);

        uint256 repayment = p.flashAmount + flashFee;
        uint256 beforeRepayment = tokenIn.balanceOf(address(this));
        // Preexisting profits cannot subsidize a new losing trade.
        require(beforeRepayment >= startingBalance + repayment + p.minProfit, "Insufficient NEW profit");
        tokenIn.safeTransfer(vault, repayment);
        uint256 afterRepayment = tokenIn.balanceOf(address(this));
        require(afterRepayment >= startingBalance + p.minProfit, "Repayment reduced old profits");
        uint256 realizedProfit = afterRepayment - startingBalance;

        status = Status.IDLE;
        expectedPayloadHash = bytes32(0);
        startingBalance = 0;
        emit FlashArbitrageExecuted(p.tokenIn, p.tokenOut, p.routerA, p.routerB,
            p.flashAmount, flashFee, intermediate, realizedProfit);
    }

    function _swap(
        address router, address inputToken, address outputToken,
        uint256 amountIn, uint256 minOut, uint256 deadline
    ) private returns (uint256 received) {
        IERC20Lite input = IERC20Lite(inputToken);
        IERC20Lite output = IERC20Lite(outputToken);
        uint256 initialInput = input.balanceOf(address(this));
        uint256 initialOutput = output.balanceOf(address(this));
        require(initialInput >= amountIn, "Insufficient input");

        input.forceApprove(router, amountIn);
        address[] memory path = new address[](2);
        path[0] = inputToken;
        path[1] = outputToken;
        uint256[] memory quoted = IV2RouterLite(router).swapExactTokensForTokens(
            amountIn, minOut, path, address(this), deadline);
        input.forceApprove(router, 0);

        require(quoted.length == 2 && quoted[0] == amountIn, "Malformed swap receipt");
        require(input.balanceOf(address(this)) == initialInput - amountIn, "Unexpected input transfer");
        uint256 finalOutput = output.balanceOf(address(this));
        require(finalOutput >= initialOutput, "Output balance decreased");
        received = finalOutput - initialOutput;
        // Only conventional, non-taxed token swaps are in scope for this phase.
        require(received == quoted[1] && received >= minOut, "Unexpected swap output");
    }

    function withdraw(address token) external onlyOwner whenIdle {
        require(token != address(0), "Invalid token");
        IERC20Lite erc20 = IERC20Lite(token);
        uint256 balance = erc20.balanceOf(address(this));
        require(balance > 0, "Nothing to withdraw");
        erc20.safeTransfer(owner, balance);
        emit ProfitWithdrawn(token, owner, balance);
    }
}
