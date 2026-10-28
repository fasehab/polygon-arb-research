// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

// Minimal interface for ERC20 Tokens (USDC, WMATIC, etc.)
interface IERC20 {
    function balanceOf(address account) external view returns (uint256);
    function approve(address spender, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
}

// Minimal interface for Uniswap V2 / QuickSwap Routers
interface IUniswapV2Router {
    function swapExactTokensForTokens(
        uint amountIn,
        uint amountOutMin,
        address[] calldata path,
        address to,
        uint deadline
    ) external returns (uint[] memory amounts);
}

contract MEVSniper {
    address public owner;

    constructor() {
        owner = msg.sender;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "Only the bot can call this");
        _;
    }

    /// @notice Executes the atomic arbitrage trade
    function executeArbitrage(
        address routerA,      // e.g., QuickSwap
        address routerB,      // e.g., SushiSwap or another V2 clone
        address tokenIn,      // e.g., WMATIC (Your starting capital)
        address tokenOut,     // e.g., USDC (The target token)
        uint256 amountIn,     // How much WMATIC to trade
        uint256 minProfit     // Minimum WMATIC profit to accept
    ) external onlyOwner {
        
        // 1. Record starting balance
        uint256 startBalance = IERC20(tokenIn).balanceOf(address(this));
        require(startBalance >= amountIn, "Not enough capital in contract");

        // 2. Buy on Exchange A (QuickSwap)
        IERC20(tokenIn).approve(routerA, amountIn);
        address[] memory pathA = new address[](2);
        pathA[0] = tokenIn;
        pathA[1] = tokenOut;
        
        IUniswapV2Router(routerA).swapExactTokensForTokens(
            amountIn,
            0, // We rely on the final atomic check for safety, not the router slippage
            pathA,
            address(this),
            block.timestamp
        );

        // 3. Sell on Exchange B (The other DEX)
        uint256 tokenOutBalance = IERC20(tokenOut).balanceOf(address(this));
        IERC20(tokenOut).approve(routerB, tokenOutBalance);
        address[] memory pathB = new address[](2);
        pathB[0] = tokenOut;
        pathB[1] = tokenIn;
        
        IUniswapV2Router(routerB).swapExactTokensForTokens(
            tokenOutBalance,
            0, 
            pathB,
            address(this),
            block.timestamp
        );

        // 4. THE ATOMIC CHECK
        // If the balance isn't higher than what we started with + profit margin, turn back time.
        uint256 endBalance = IERC20(tokenIn).balanceOf(address(this));
        require(endBalance >= startBalance + minProfit, "Arbitrage not profitable! REVERTING.");
    }

    // Allows you to withdraw your initial capital and profits back to your wallet
    function withdraw(address token) external onlyOwner {
        uint256 balance = IERC20(token).balanceOf(address(this));
        IERC20(token).transfer(owner, balance);
    }
}