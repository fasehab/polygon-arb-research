// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

// Both QuickSwap V2 and SushiSwap V2 on Polygon implement this 5-argument ABI.
// Uniswap SwapRouter02 is NOT a drop-in replacement for this interface.
interface IUniswapV2Router02 {
    function swapExactTokensForTokens(
        uint amountIn,
        uint amountOutMin,
        address[] calldata path,
        address to,
        uint deadline
    ) external returns (uint[] memory amounts);
}

interface IBalancerVault {
    function flashLoan(
        address recipient,
        address[] memory tokens,
        uint256[] memory amounts,
        bytes memory userData
    ) external;
}

interface IFlashLoanRecipient {
    function receiveFlashLoan(
        address[] memory tokens,
        uint256[] memory amounts,
        uint256[] memory feeAmounts,
        bytes memory userData
    ) external;
}

contract BalancerMEVSniper is IFlashLoanRecipient {
    address public owner;

    // Step 1: lock this version to the two V2 venues modeled by the Rust bot.
    // These are Polygon PoS router addresses, NOT pair or factory addresses.
    address public constant QUICKSWAP_V2_ROUTER = 0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff;
    address public constant SUSHISWAP_V2_ROUTER = 0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506;

    function _requireV2Route(address routerA, address routerB) internal pure {
        require(routerA == QUICKSWAP_V2_ROUTER, "Router A must be QuickSwap V2");
        require(routerB == SUSHISWAP_V2_ROUTER, "Router B must be SushiSwap V2");
    }
    
    // Balancer Vault address on Polygon Mainnet
    IBalancerVault public constant vault = IBalancerVault(0xBA12222222228d8Ba445958a75a0704d566BF2C8);

    modifier onlyOwner() {
        require(msg.sender == owner, "Not authorized");
        _;
    }

    constructor() {
        owner = msg.sender;
    }

    // 1. Rust bot calls this to start the sequence
    function executeFlashArbitrage(
        address tokenIn,
        address tokenOut,
        uint256 flashAmount,
        address routerA,
        address routerB,
        uint256 minProfit
    ) external onlyOwner {
        _requireV2Route(routerA, routerB);
        address[] memory tokens = new address[](1);
        tokens[0] = tokenIn;

        uint256[] memory amounts = new uint256[](1);
        amounts[0] = flashAmount;

        // Pack our routing instructions to pass to the callback
        bytes memory userData = abi.encode(
            routerA, 
            routerB, 
            tokenIn, 
            tokenOut, 
            minProfit, 
            flashAmount
        );

        // Request the flash loan from Balancer
        vault.flashLoan(address(this), tokens, amounts, userData);
    }

    // 2. Balancer automatically calls this after sending the tokens
    function receiveFlashLoan(
        address[] memory tokens,
        uint256[] memory amounts,
        uint256[] memory feeAmounts,
        bytes memory userData
    ) external override {
        require(msg.sender == address(vault), "Only Vault can call");

        // Unpack routing instructions
        (
            address routerA,
            address routerB,
            address tokenIn,
            address tokenOut,
            uint256 minProfit,
            uint256 flashAmount
        ) = abi.decode(userData, (address, address, address, address, uint256, uint256));
        // The Balancer Vault can be called by anyone. Guard the callback too:
        // checking only the owner-triggered entry point is not enough.
        _requireV2Route(routerA, routerB);

        // Execute Swap 1
        IERC20(tokenIn).approve(routerA, flashAmount);
        address[] memory pathA = new address[](2);
        pathA[0] = tokenIn;
        pathA[1] = tokenOut;

        uint[] memory amountsA = IUniswapV2Router02(routerA).swapExactTokensForTokens(
            flashAmount,
            0,
            pathA,
            address(this),
            block.timestamp
        );

        // Execute Swap 2
        uint256 receivedTokenOut = amountsA[1];
        IERC20(tokenOut).approve(routerB, receivedTokenOut);
        address[] memory pathB = new address[](2);
        pathB[0] = tokenOut;
        pathB[1] = tokenIn;

        IUniswapV2Router02(routerB).swapExactTokensForTokens(
            receivedTokenOut,
            0,
            pathB,
            address(this),
            block.timestamp
        );

        // Profit Validation & Repayment (Balancer has 0 fees on Polygon)
        uint256 finalBalance = IERC20(tokenIn).balanceOf(address(this));
        
        require(finalBalance >= flashAmount + minProfit, "Flash Arb Unprofitable");

        // Repay the loan to the Vault
        IERC20(tokenIn).transfer(address(vault), flashAmount);
    }

    // Withdraw accumulated profits
    function withdraw(address token) external onlyOwner {
        uint256 balance = IERC20(token).balanceOf(address(this));
        IERC20(token).transfer(owner, balance);
    }
}