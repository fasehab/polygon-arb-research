//! Step 7: FILTERED READ-ONLY V2 pool discovery, baseline + hypothetical pending-state
//! scenarios, bounded trade-size search, flash-loan fee and gas budget estimates.
//! This does NOT simulate an actual transaction or guarantee inclusion/order,
//! profit, token semantics, fee tier, gas usage, flash-loan availability, etc.
//! NO PRIVATE KEY, NO SIGNER, NO BROADCASTING.

use ethers::abi::{ParamType, decode};
use ethers::prelude::abigen;
use ethers::providers::{Middleware, Provider, StreamExt, Ws};
use ethers::types::{Address, BlockId, BlockNumber, H256, U256, U512};
use std::cmp::{Ordering, min};
use std::env;
use std::error::Error;
use std::str::FromStr;
use std::sync::atomic::{AtomicU64, Ordering as AtomicOrdering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tokio::sync::Semaphore;
use tokio::task::JoinSet;
use tokio::time::timeout;

mod observer;
mod optimizer;
mod preflight;
use observer::{PairGate, SeenHashes, filtered_pending_params};
use optimizer::{V2Route, gas_budget_wei, gas_cost_at_spot_ceil, mul_div_ceil, optimize};
use preflight::{Evidence as PreflightEvidence, Scenario as PreflightScenario};

type AppError = Box<dyn Error + Send + Sync>;
type WsProvider = Arc<Provider<Ws>>;

const QUICKSWAP_V2_ROUTER: &str = "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff";
const QUICKSWAP_V2_FACTORY: &str = "0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32";
const SUSHISWAP_V2_ROUTER: &str = "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506";
const SUSHISWAP_V2_FACTORY: &str = "0xc35DADB65012eC5796536bD9864eD8773aBc74C4";
// Drop observations during backpressure instead of growing an unbounded queue.
const MAX_IN_FLIGHT: usize = 4;
const TASK_TIMEOUT: Duration = Duration::from_secs(20);
const CONNECTION_TIMEOUT: Duration = Duration::from_secs(25);
const SUBSCRIBE_TIMEOUT: Duration = Duration::from_secs(20);
const HEARTBEAT_PERIOD: Duration = Duration::from_secs(30);
const HEALTH_RPC_TIMEOUT: Duration = Duration::from_secs(10);
const SEEN_TTL: Duration = Duration::from_secs(90);
const SEEN_CAPACITY: usize = 8_192;
const BACKOFF_MAX_SECONDS: u64 = 30;

#[derive(Default)]
struct ObservationStats {
    // All incoming hashes here are sent by Alchemy's filtered subscription.
    observed: AtomicU64,
    deduplicated: AtomicU64,
    overloaded: AtomicU64,
    fetched: AtomicU64,
    unavailable: AtomicU64,
    wrong_router: AtomicU64,
    unsupported: AtomicU64,
    direct_swaps: AtomicU64,
    busy_pair: AtomicU64,
    analyzed: AtomicU64,
    errors: AtomicU64,
}
impl ObservationStats {
    fn print(&self) {
        println!(
            "OBSERVER counters (cumulative): filtered_hashes={} duplicates={} capacity_drops={} fetched={} unavailable={} wrong_router={} unsupported={} direct_swaps={} pair_busy={} analyses={} errors={}",
            self.observed.load(AtomicOrdering::Relaxed),
            self.deduplicated.load(AtomicOrdering::Relaxed),
            self.overloaded.load(AtomicOrdering::Relaxed),
            self.fetched.load(AtomicOrdering::Relaxed),
            self.unavailable.load(AtomicOrdering::Relaxed),
            self.wrong_router.load(AtomicOrdering::Relaxed),
            self.unsupported.load(AtomicOrdering::Relaxed),
            self.direct_swaps.load(AtomicOrdering::Relaxed),
            self.busy_pair.load(AtomicOrdering::Relaxed),
            self.analyzed.load(AtomicOrdering::Relaxed),
            self.errors.load(AtomicOrdering::Relaxed),
        );
    }
}
// Both venues' standard V2 0.30% fee. Verify assumptions per actual pool/token.
const V2_FEE_NUMERATOR: u64 = 997;
const V2_FEE_DENOMINATOR: u64 = 1000;
const PRICE_DECIMAL_PLACES: usize = 18;
const MAX_TOKEN_DECIMALS: u8 = 36;
// Polygon wrapped-native (historical WMATIC / WPOL). Native gas is POL.
const WRAPPED_NATIVE: &str = "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270";

#[derive(Clone, Copy)]
struct CostConfig {
    // No fee default is ASSUMED: unknown flash fees block net assessment.
    flash_fee_bps: Option<u64>,
    // Does NOT represent actual execution gas until measured on fork.
    gas_units: u64,
    gas_multiplier_bps: u64,
    min_net_profit_bps: u64,
}
fn optional_u64_env(name: &str) -> Result<Option<u64>, AppError> {
    match env::var(name) {
        Ok(value) => {
            Ok(Some(value.parse::<u64>().map_err(|_| {
                format!("Invalid {name}: expected unsigned integer")
            })?))
        }
        Err(env::VarError::NotPresent) => Ok(None),
        Err(other) => Err(format!("Invalid environment variable {name}: {other}").into()),
    }
}
impl CostConfig {
    fn from_env() -> Result<Self, AppError> {
        let flash_fee_bps = optional_u64_env("FLASH_FEE_BPS")?;
        let gas_units = optional_u64_env("GAS_UNITS_ESTIMATE")?.unwrap_or(600_000);
        let gas_multiplier_bps = optional_u64_env("GAS_PRICE_MULTIPLIER_BPS")?.unwrap_or(15_000);
        let min_net_profit_bps = optional_u64_env("MIN_NET_PROFIT_BPS")?.unwrap_or(20);
        if flash_fee_bps.is_some_and(|f| f > 10_000)
            || gas_units == 0
            || gas_units > 5_000_000
            || min_net_profit_bps > 10_000
            || !(10_000..=100_000).contains(&gas_multiplier_bps)
        {
            return Err("Invalid gas/fee configuration; check README limits".into());
        }
        Ok(Self {
            flash_fee_bps,
            gas_units,
            gas_multiplier_bps,
            min_net_profit_bps,
        })
    }
}

abigen!(
    IV2Router,
    r#"[function factory() external view returns (address)]"#
);
abigen!(
    IV2Factory,
    r#"[function getPair(address tokenA, address tokenB) external view returns (address)]"#
);
abigen!(
    IERC20Metadata,
    r#"[function decimals() external view returns (uint8)]"#
);
abigen!(
    IV2Pair,
    r#"[
        function factory() external view returns (address)
        function token0() external view returns (address)
        function token1() external view returns (address)
        function getReserves() external view returns (uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast)
    ]"#
);

#[derive(Clone, Debug)]
struct PoolSnapshot {
    venue: &'static str,
    pair: Address,
    factory: Address,
    token0: Address,
    token1: Address,
    reserve0: U256,
    reserve1: U256,
    last_updated: u32,
}

#[derive(Clone, Copy, Debug)]
struct TokenDecimals {
    a: u8,
    b: u8,
}

fn valid_tokens(a: Address, b: Address) -> bool {
    a != Address::zero() && b != Address::zero() && a != b
}

fn matches_pair(token0: Address, token1: Address, a: Address, b: Address) -> bool {
    token0.as_bytes() < token1.as_bytes()
        && ((token0 == a && token1 == b) || (token0 == b && token1 == a))
}

// Decode only the V2 exact-token-input swap methods. A two-token path is
// mandatory: multi-hop input paths cannot be treated as one pair and the
// Step 1 Solidity executor supports only two-token swaps.
#[derive(Clone, Copy, Debug)]
struct SwapDetails {
    a: Address,
    b: Address,
    amount_in: U256,
    min_out: U256,
    deadline: U256,
}
fn decode_direct_swap_details(input: &[u8]) -> Option<SwapDetails> {
    if input.len() < 4 {
        return None;
    }
    if &input[..4] != [0x38, 0xed, 0x17, 0x39] {
        return None;
    }
    let params = [
        ParamType::Uint(256),
        ParamType::Uint(256),
        ParamType::Array(Box::new(ParamType::Address)),
        ParamType::Address,
        ParamType::Uint(256),
    ];
    let values = decode(&params, &input[4..]).ok()?;
    let amount_in = values.first()?.clone().into_uint()?;
    let min_out = values.get(1)?.clone().into_uint()?;
    let path = values.get(2)?.clone().into_array()?;
    let deadline = values.get(4)?.clone().into_uint()?;
    if path.len() != 2 || amount_in.is_zero() {
        return None;
    }
    let a = path[0].clone().into_address()?;
    let b = path[1].clone().into_address()?;
    if !valid_tokens(a, b) {
        return None;
    }
    Some(SwapDetails {
        a,
        b,
        amount_in,
        min_out,
        deadline,
    })
}
#[cfg(test)]
fn decode_direct_swap(input: &[u8]) -> Option<(Address, Address, U256)> {
    decode_direct_swap_details(input).map(|d| (d.a, d.b, d.amount_in))
}

// This models one direct, exact-input V2 swap on the verified QuickSwap pool,
// but it does NOT establish pending-tx inclusion, order or transfer behaviour.
fn hypothetical_quickswap_after(
    pool: &PoolSnapshot,
    swap: SwapDetails,
    block_timestamp: U256,
) -> Option<(PoolSnapshot, U256)> {
    if block_timestamp > swap.deadline {
        return None;
    }
    let (reserve_in, reserve_out) = oriented_reserves(pool, swap.a, swap.b)?;
    let output = get_amount_out(swap.amount_in, reserve_in, reserve_out)?;
    if output < swap.min_out || output.is_zero() || output >= reserve_out {
        return None;
    }
    let mut projected = pool.clone();
    if pool.token0 == swap.a {
        projected.reserve0 = projected.reserve0.checked_add(swap.amount_in)?;
        projected.reserve1 = projected.reserve1.checked_sub(output)?;
    } else {
        projected.reserve1 = projected.reserve1.checked_add(swap.amount_in)?;
        projected.reserve0 = projected.reserve0.checked_sub(output)?;
    }
    Some((projected, output))
}

async fn check_router(
    provider: &WsProvider,
    venue: &'static str,
    router: Address,
    expected_factory: Address,
) -> Result<(), AppError> {
    if provider.get_code(router, None).await?.0.is_empty() {
        return Err(format!("{venue}: router has no contract code").into());
    }
    if provider
        .get_code(expected_factory, None)
        .await?
        .0
        .is_empty()
    {
        return Err(format!("{venue}: factory has no contract code").into());
    }
    let actual_factory = IV2Router::new(router, Arc::clone(provider))
        .factory()
        .call()
        .await?;
    if actual_factory != expected_factory {
        return Err(format!(
            "{venue}: router factory mismatch expected {expected_factory:?}, found {actual_factory:?}"
        )
        .into());
    }
    println!("PASS {venue}: router={router:?} factory={expected_factory:?}");
    Ok(())
}

// All reads for both venues are pinned to the same observed latest block number.
// They are not predictions of the pending transaction's future state.
async fn read_pair(
    provider: &WsProvider,
    venue: &'static str,
    factory: Address,
    a: Address,
    b: Address,
    at: BlockId,
) -> Result<Option<PoolSnapshot>, AppError> {
    let pair_address = IV2Factory::new(factory, Arc::clone(provider))
        .get_pair(a, b)
        .block(at)
        .call()
        .await?;
    if pair_address == Address::zero() {
        return Ok(None);
    }
    if provider
        .get_code(pair_address, Some(at))
        .await?
        .0
        .is_empty()
    {
        return Err(
            format!("{venue}: factory returned an address with no code: {pair_address:?}").into(),
        );
    }
    let pair = IV2Pair::new(pair_address, Arc::clone(provider));
    let actual_factory = pair.factory().block(at).call().await?;
    if actual_factory != factory {
        return Err(format!("{venue}: pair factory mismatch for {pair_address:?}").into());
    }
    let token0 = pair.token_0().block(at).call().await?;
    let token1 = pair.token_1().block(at).call().await?;
    if !matches_pair(token0, token1, a, b) {
        return Err(format!(
            "{venue}: pair token mismatch at {pair_address:?}: token0={token0:?}, token1={token1:?}"
        )
        .into());
    }
    let (r0, r1, updated) = pair.get_reserves().block(at).call().await?;
    let reserve0 = U256::from(r0);
    let reserve1 = U256::from(r1);
    if reserve0.is_zero() || reserve1.is_zero() {
        return Ok(None);
    }
    Ok(Some(PoolSnapshot {
        venue,
        pair: pair_address,
        factory,
        token0,
        token1,
        reserve0,
        reserve1,
        last_updated: updated,
    }))
}

// This helper orients canonical token0/token1 reserves for ANY input token.
fn oriented_reserves(
    pool: &PoolSnapshot,
    token_in: Address,
    token_out: Address,
) -> Option<(U256, U256)> {
    if token_in == pool.token0 && token_out == pool.token1 {
        Some((pool.reserve0, pool.reserve1))
    } else if token_in == pool.token1 && token_out == pool.token0 {
        Some((pool.reserve1, pool.reserve0))
    } else {
        None
    }
}

fn pow10(exponent: u8) -> Option<U256> {
    if exponent > MAX_TOKEN_DECIMALS {
        return None;
    }
    let mut value = U256::one();
    for _ in 0..exponent {
        value = value.checked_mul(U256::from(10u64))?;
    }
    Some(value)
}

// Human-readable values for LOGGING ONLY. No floating point used in decisions.
fn format_units(raw: U256, decimals: u8) -> String {
    let digits = raw.to_string();
    if decimals == 0 {
        return digits;
    }
    let d = decimals as usize;
    let expanded = if digits.len() <= d {
        format!("0.{}{}", "0".repeat(d - digits.len()), digits)
    } else {
        format!(
            "{}.{}",
            &digits[..digits.len() - d],
            &digits[digits.len() - d..]
        )
    };
    let value = expanded.trim_end_matches('0').trim_end_matches('.');
    value.to_owned()
}

fn format_fixed_decimal(scaled: U512, places: usize) -> String {
    if scaled.is_zero() {
        return format!("<0.{}1", "0".repeat(places - 1));
    }
    let digits = scaled.to_string();
    let raw = if digits.len() <= places {
        format!("0.{}{}", "0".repeat(places - digits.len()), digits)
    } else {
        format!(
            "{}.{}",
            &digits[..digits.len() - places],
            &digits[digits.len() - places..]
        )
    };
    raw.trim_end_matches('0').trim_end_matches('.').to_owned()
}

// Display price of 1 whole token_in denominated in whole token_out.
// (reserve_out / 10^dec_out) / (reserve_in / 10^dec_in)
// WIDE arithmetic prevents overflow from multiplying raw reserves by powers of ten.
fn spot_price(
    pool: &PoolSnapshot,
    token_in: Address,
    token_out: Address,
    dec_in: u8,
    dec_out: u8,
) -> Option<String> {
    let (r_in, r_out) = oriented_reserves(pool, token_in, token_out)?;
    if r_in.is_zero() {
        return None;
    }
    let num = U512::from(r_out)
        * U512::from(pow10(dec_in)?)
        * U512::from(U256::exp10(PRICE_DECIMAL_PLACES));
    let den = U512::from(r_in) * U512::from(pow10(dec_out)?);
    Some(format_fixed_decimal(num / den, PRICE_DECIMAL_PLACES))
}

// Models the classic V2 amount-out formula using 512-bit intermediates.
// No floating-point precision loss and no unchecked conversion back to U256.
fn get_amount_out(input: U256, reserve_in: U256, reserve_out: U256) -> Option<U256> {
    if input.is_zero() || reserve_in.is_zero() || reserve_out.is_zero() {
        return None;
    }
    let fee_adjusted = U512::from(input) * U512::from(V2_FEE_NUMERATOR);
    let numerator = fee_adjusted * U512::from(reserve_out);
    let denominator = U512::from(reserve_in) * U512::from(V2_FEE_DENOMINATOR) + fee_adjusted;
    let out = numerator / denominator;
    let mut bytes = [0u8; 64];
    out.to_big_endian(&mut bytes);
    if bytes[..32].iter().any(|b| *b != 0) {
        return None;
    }
    Some(U256::from_big_endian(&bytes[32..]))
}

fn relative_spot_direction(
    quick: &PoolSnapshot,
    sushi: &PoolSnapshot,
    a: Address,
    b: Address,
) -> Option<Ordering> {
    let (q_a, q_b) = oriented_reserves(quick, a, b)?;
    let (s_a, s_b) = oriented_reserves(sushi, a, b)?;
    // Compare B-per-A without division or decimal rounding.
    Some(q_b.full_mul(s_a).cmp(&s_b.full_mul(q_a)))
}

fn print_route_quotes(
    first: &PoolSnapshot,
    second: &PoolSnapshot,
    token_in: Address,
    token_mid: Address,
    decimals_in: u8,
) {
    let Some((first_in, first_out)) = oriented_reserves(first, token_in, token_mid) else {
        return;
    };
    let Some((second_in, second_out)) = oriented_reserves(second, token_mid, token_in) else {
        return;
    };
    // Choose illustrative sizes relative to the smaller available input-token reserve.
    // This is NOT optimization or sizing advice. It avoids absurdly huge probes
    // when one venue's pool is much shallower than the other's.
    let reference_reserve = min(first_in, second_out);
    println!(
        "    {} -> {} | start={token_in:?} via={token_mid:?}",
        first.venue, second.venue
    );
    for divisor in [10_000u64, 1_000, 100] {
        // 0.01%, 0.1%, 1%
        let amount_in = reference_reserve / U256::from(divisor);
        if amount_in.is_zero() {
            println!("      sample 1/{divisor}: too small; skipped");
            continue;
        }
        let amount_mid = match get_amount_out(amount_in, first_in, first_out) {
            Some(n) if !n.is_zero() => n,
            _ => {
                println!("      sample 1/{divisor}: cannot quote first leg");
                continue;
            }
        };
        let result = match get_amount_out(amount_mid, second_in, second_out) {
            Some(n) => n,
            None => {
                println!("      sample 1/{divisor}: cannot quote second leg");
                continue;
            }
        };
        let (sign, difference) = if result >= amount_in {
            ("+", result - amount_in)
        } else {
            ("-", amount_in - result)
        };
        println!(
            "      sample 1/{divisor}: input={} output={} gross_model_delta={sign}{} tokenIn (RAW delta={sign}{difference}; NOT gas/flash-inclusive)",
            format_units(amount_in, decimals_in),
            format_units(result, decimals_in),
            format_units(difference, decimals_in),
        );
    }
}

async fn get_token_decimals(
    provider: &WsProvider,
    address: Address,
    at: BlockId,
) -> Result<u8, AppError> {
    if provider.get_code(address, Some(at)).await?.0.is_empty() {
        return Err(format!("Token {address:?} has no code at selected block").into());
    }
    let decimals = IERC20Metadata::new(address, Arc::clone(provider))
        .decimals()
        .block(at)
        .call()
        .await?;
    if decimals > MAX_TOKEN_DECIMALS {
        return Err(format!("Unsupported token decimals={decimals} for token {address:?}").into());
    }
    Ok(decimals)
}

// For each starting token, convert an estimated native POL gas budget into
// start-token RAW units at the HIGHER of the two verified WPOL spot rates.
// No valid direct pool => UNKNOWN => no net-profit assessment. Manipulable pool
// prices mean this is only a rough economic gate, not an oracle-grade quote.
async fn estimated_token_gas_cost(
    provider: &WsProvider,
    start: Address,
    wrapped_native: Address,
    q_factory: Address,
    s_factory: Address,
    at: BlockId,
    native_wei: U256,
) -> Result<Option<U256>, AppError> {
    if start == wrapped_native {
        return Ok(Some(native_wei));
    }
    let q = read_pair(
        provider,
        "QuickSwap V2 gas conversion",
        q_factory,
        wrapped_native,
        start,
        at,
    )
    .await?;
    let s = read_pair(
        provider,
        "SushiSwap V2 gas conversion",
        s_factory,
        wrapped_native,
        start,
        at,
    )
    .await?;
    // Require both independent pool references; do not invent gas costs.
    let (Some(q), Some(s)) = (q, s) else {
        return Ok(None);
    };
    let (qi, qo) =
        oriented_reserves(&q, wrapped_native, start).ok_or("Gas quote pool orientation invalid")?;
    let (si, so) =
        oriented_reserves(&s, wrapped_native, start).ok_or("Gas quote pool orientation invalid")?;
    // Decline illiquid reference markets: the candidate gas budget must be
    // less than 1% of each pool's wrapped-native reserve.
    if qi / U256::from(100u64) <= native_wei || si / U256::from(100u64) <= native_wei {
        return Ok(None);
    }
    let q_cost = gas_cost_at_spot_ceil(native_wei, qi, qo);
    let s_cost = gas_cost_at_spot_ceil(native_wei, si, so);
    Ok(match (q_cost, s_cost) {
        (Some(a), Some(b)) if !a.is_zero() && !b.is_zero() => {
            let (high, low) = (a.max(b), a.min(b));
            // Two venues' spot valuations must agree within 10%; otherwise
            // the gas conversion is not defensible as even an estimate.
            if high.full_mul(U256::from(10_000u64)) <= low.full_mul(U256::from(11_000u64)) {
                Some(high)
            } else {
                None
            }
        }
        _ => None,
    })
}

async fn print_optimized_routes(
    provider: &WsProvider,
    quick: &PoolSnapshot,
    sushi: &PoolSnapshot,
    a: Address,
    b: Address,
    dec: TokenDecimals,
    at: BlockId,
    q_factory: Address,
    s_factory: Address,
    cfg: CostConfig,
    scenario: PreflightScenario,
) -> Result<(), AppError> {
    println!(
        "  STEP 4 READ-ONLY SEARCH: maximum input = 10% of smaller relevant reserve; bounded numerical search, not guaranteed global optimum."
    );
    let gas_price = provider.get_gas_price().await?;
    let native_budget = gas_budget_wei(cfg.gas_units, gas_price, cfg.gas_multiplier_bps)
        .ok_or("Gas estimate overflow")?;
    println!(
        "    gas estimate: {} units at RPC gasPrice={} wei; {} bps budget multiplier => native_budget_wei={} (ESTIMATE ONLY)",
        cfg.gas_units, gas_price, cfg.gas_multiplier_bps, native_budget
    );
    let wrapped_native = Address::from_str(WRAPPED_NATIVE)?;
    // Save RPC work for completely gross-unprofitable opportunities.
    for (first, second) in [(quick, sushi), (sushi, quick)] {
        for (start, mid, decimals_in) in [(a, b, dec.a), (b, a, dec.b)] {
            let (f_in, f_out) = oriented_reserves(first, start, mid).expect("validated pair");
            let (s_in, s_out) = oriented_reserves(second, mid, start).expect("validated pair");
            let route = V2Route {
                first_in: f_in,
                first_out: f_out,
                second_in: s_in,
                second_out: s_out,
            };
            let fee_for_search = cfg.flash_fee_bps.unwrap_or(0);
            let Some(best) = optimize(route, fee_for_search) else {
                println!(
                    "    {} -> {} start={start:?}: SKIP no testable sizes",
                    first.venue, second.venue
                );
                continue;
            };
            println!(
                "    {} -> {} start={start:?} via={mid:?} best_tested_input={} best_tested_output={} modeled_flash_fee={} pre_gas_delta={}{} (tokenIn)",
                first.venue,
                second.venue,
                format_units(best.input, decimals_in),
                format_units(best.received, decimals_in),
                format_units(best.fee, decimals_in),
                best.before_gas.sign(),
                format_units(best.before_gas.magnitude, decimals_in)
            );
            // STEP 7: Fail-closed preflight. This produces DRAFT limits only.
            // It neither makes a contract eth_call nor verifies relative inclusion.
            let draft = cfg.flash_fee_bps.and_then(|fee_bps| {
                preflight::draft_limits(route, best.input, fee_bps, 100, cfg.min_net_profit_bps)
            });
            let (verdict, missing) = preflight::assess(PreflightEvidence {
                scenario,
                pre_gas: best.before_gas,
                flash_fee_verified: false, // config is NOT independent fee verification
                gas_token_cost_verified: false, // spot-budget != receipt-calibrated execution gas
                actual_loan_liquidity_verified: false,
                allowed_tokens_verified: false, // contract not deployed
                same_state_full_eth_call_passed: false,
                execution_order_verified: false,
            });
            println!(
                "      STEP 7 PREFLIGHT [{}]: {} | read-only; no execution authorization",
                scenario.label(),
                verdict.label()
            );
            if let Some(d) = draft {
                println!(
                    "      DRAFT TradeParams raw: flashAmount={} expectedFirst={} expectedSecond={} minOutA={} minOutB={} maxFlashFee={} minProfit={} (not executable calldata)",
                    d.principal,
                    d.expected_first,
                    d.expected_second,
                    d.min_out_a,
                    d.min_out_b,
                    d.maximum_flash_fee,
                    d.minimum_profit
                );
            } else if cfg.flash_fee_bps.is_none() {
                println!("      DRAFT TradeParams: unavailable (flash-loan fee not configured)");
            } else {
                println!("      DRAFT TradeParams: unavailable (quote, rounding, or invalid size)");
            }
            for reason in missing {
                println!("      BLOCKER: {reason}");
            }
            if !best.before_gas.strictly_positive() {
                println!("      REJECT: no positive modeled pre-gas profit inside search bound");
                continue;
            }
            if cfg.flash_fee_bps.is_none() {
                println!(
                    "      UNASSESSED: FLASH_FEE_BPS is unset (fee MUST be independently verified); no net profit stated"
                );
                continue;
            }
            let gas_token = match estimated_token_gas_cost(
                provider,
                start,
                wrapped_native,
                q_factory,
                s_factory,
                at,
                native_budget,
            )
            .await
            {
                Ok(value) => value,
                Err(e) => {
                    eprintln!("      Gas conversion unavailable: {e}");
                    None
                }
            };
            if let Some(gas_token) = gas_token {
                let net = best.before_gas.subtract(gas_token);
                println!(
                    "      gas_budget_token_raw={gas_token} ({}) net_ESTIMATE={}{} tokenIn",
                    format_units(gas_token, decimals_in),
                    net.sign(),
                    format_units(net.magnitude, decimals_in)
                );
                let threshold = mul_div_ceil(
                    best.input,
                    U256::from(cfg.min_net_profit_bps),
                    U256::from(10_000u64),
                )
                .ok_or("Minimum profit threshold overflow")?;
                if net.strictly_positive() && net.magnitude >= threshold {
                    println!(
                        "      CANDIDATE ONLY: modeled net exceeds configured minimum profit ({} bps). NOT transaction-simulated or executable.",
                        cfg.min_net_profit_bps
                    );
                } else {
                    println!(
                        "      REJECT: below minimum modeled net-profit requirement ({} bps of principal)",
                        cfg.min_net_profit_bps
                    );
                }
            } else {
                println!(
                    "      UNASSESSED: no verified dual-DEX direct WPOL conversion to start token; gas cost in tokenIn unknown"
                );
            }
        }
    }
    Ok(())
}

fn print_pair_analysis(
    quick: &PoolSnapshot,
    sushi: &PoolSnapshot,
    a: Address,
    b: Address,
    dec: TokenDecimals,
) {
    println!(
        "  Token decimals: A={}; B={} (read on-chain at snapshot block)",
        dec.a, dec.b
    );
    for pool in [quick, sushi] {
        let (a_raw, b_raw) =
            oriented_reserves(pool, a, b).expect("validated pair must contain A and B");
        println!(
            "  {} normalized reserve A={} B={} | spot B per A={} | spot A per B={} (spot prices are fee-free)",
            pool.venue,
            format_units(a_raw, dec.a),
            format_units(b_raw, dec.b),
            spot_price(pool, a, b, dec.a, dec.b).unwrap_or_else(|| "N/A".into()),
            spot_price(pool, b, a, dec.b, dec.a).unwrap_or_else(|| "N/A".into()),
        );
    }
    match relative_spot_direction(quick, sushi, a, b) {
        Some(Ordering::Greater) => println!(
            "  SPOT DIRECTION: A->B is cheaper on QuickSwap (B/A higher); reverse sale on SushiSwap; before fees."
        ),
        Some(Ordering::Less) => println!(
            "  SPOT DIRECTION: A->B is cheaper on SushiSwap (B/A higher); reverse sale on QuickSwap; before fees."
        ),
        Some(Ordering::Equal) => {
            println!("  SPOT DIRECTION: identical mid prices before swap fees.")
        }
        None => println!("  SPOT DIRECTION: invalid token orientation; skip."),
    }
    println!(
        "  INDICATIVE TWO-LEG QUOTES: 0.30% assumed on EACH V2 leg; same current-block reserves; no gas, flash fees, ordering or pending-state projection."
    );
    for (first, second) in [(quick, sushi), (sushi, quick)] {
        print_route_quotes(first, second, a, b, dec.a);
        print_route_quotes(first, second, b, a, dec.b);
    }
    println!(
        "  STATUS: READ-ONLY DIAGNOSTICS ONLY. Positive gross delta is NOT executable net profit."
    );
}

fn print_pool(pool: &Option<PoolSnapshot>, venue: &str) {
    match pool {
        Some(s) => println!(
            "  {} PAIR={:?} factory={:?} token0={:?} token1={:?} reserve0={} reserve1={} block_timestamp_last={} (RAW, NOT PRICE)",
            s.venue, s.pair, s.factory, s.token0, s.token1, s.reserve0, s.reserve1, s.last_updated
        ),
        None => println!("  {venue}: no usable V2 pool (missing or zero liquidity); skipped"),
    }
}

async fn discover(
    provider: &WsProvider,
    a: Address,
    b: Address,
    q_factory: Address,
    s_factory: Address,
    cfg: CostConfig,
    pending: Option<(H256, SwapDetails)>,
) -> Result<(), AppError> {
    if !valid_tokens(a, b) {
        return Err("Invalid token pair".into());
    }
    let number = provider.get_block_number().await?;
    let at = BlockId::Number(BlockNumber::Number(number));
    let quick = read_pair(provider, "QuickSwap V2", q_factory, a, b, at).await?;
    let sushi = read_pair(provider, "SushiSwap V2", s_factory, a, b, at).await?;
    println!("PAIR DISCOVERY block={number} tokenA={a:?} tokenB={b:?}");
    print_pool(&quick, "QuickSwap V2");
    print_pool(&sushi, "SushiSwap V2");
    if let (Some(q), Some(s)) = (&quick, &sushi) {
        let decimals = TokenDecimals {
            a: get_token_decimals(provider, a, at).await?,
            b: get_token_decimals(provider, b, at).await?,
        };
        println!("  SCENARIO BASELINE: both pools at observed confirmed block");
        print_pair_analysis(q, s, a, b, decimals);
        print_optimized_routes(
            provider,
            q,
            s,
            a,
            b,
            decimals,
            at,
            q_factory,
            s_factory,
            cfg,
            PreflightScenario::ConfirmedSnapshot,
        )
        .await?;
        if let Some((tx_hash, swap)) = pending {
            // Only create a hypothetical projected state if we know the exact
            // block timestamp. This is not a transaction inclusion prediction.
            let block = provider.get_block(number).await?;
            if let Some(block) = block {
                if let Some((q_projected, output)) =
                    hypothetical_quickswap_after(q, swap, block.timestamp)
                {
                    println!(
                        "  SCENARIO HYPOTHETICAL POST-TARGET: hash={tx_hash:?} targetInput={} targetOutput={} (raw), ASSUMES target executes first successfully with no intervening state changes",
                        swap.amount_in, output
                    );
                    print_pair_analysis(&q_projected, s, a, b, decimals);
                    print_optimized_routes(
                        provider,
                        &q_projected,
                        s,
                        a,
                        b,
                        decimals,
                        at,
                        q_factory,
                        s_factory,
                        cfg,
                        PreflightScenario::HypotheticalPostPending,
                    )
                    .await?;
                } else {
                    println!(
                        "  PROJECTED SCENARIO SKIPPED: deadline, minOut, liquidity or arithmetic check failed"
                    );
                }
            } else {
                println!("  PROJECTED SCENARIO SKIPPED: block timestamp unavailable");
            }
        }
    } else {
        println!("  STATUS: NOT ELIGIBLE FOR TWO-VENUE ANALYSIS");
    }
    Ok(())
}

// Defense-in-depth: Alchemy's toAddress filter is still checked locally.
// A successfully fetched transaction may not match our supported ABI.
#[derive(Debug)]
enum ClassifiedSwap {
    WrongRouter,
    Unsupported,
    Direct(SwapDetails),
}
fn classify_quickswap_tx(to: Option<Address>, router: Address, input: &[u8]) -> ClassifiedSwap {
    if to != Some(router) {
        return ClassifiedSwap::WrongRouter;
    }
    match decode_direct_swap_details(input) {
        Some(swap) => ClassifiedSwap::Direct(swap),
        None => ClassifiedSwap::Unsupported,
    }
}

async fn inspect_pending(
    provider: WsProvider,
    hash: H256,
    router: Address,
    q_factory: Address,
    s_factory: Address,
    cfg: CostConfig,
    pair_gate: PairGate,
    stats: Arc<ObservationStats>,
) -> Result<(), AppError> {
    let tx = match provider.get_transaction(hash).await? {
        Some(tx) => tx,
        None => {
            // A pending hash can be received before its transaction body is
            // queryable. Do not count it as a successful analysis.
            stats.unavailable.fetch_add(1, AtomicOrdering::Relaxed);
            return Ok(());
        }
    };
    stats.fetched.fetch_add(1, AtomicOrdering::Relaxed);
    let swap = match classify_quickswap_tx(tx.to, router, tx.input.as_ref()) {
        ClassifiedSwap::WrongRouter => {
            stats.wrong_router.fetch_add(1, AtomicOrdering::Relaxed);
            return Ok(());
        }
        ClassifiedSwap::Unsupported => {
            stats.unsupported.fetch_add(1, AtomicOrdering::Relaxed);
            return Ok(());
        }
        ClassifiedSwap::Direct(swap) => swap,
    };
    stats.direct_swaps.fetch_add(1, AtomicOrdering::Relaxed);
    // The direction-independent lease is released on timeout/cancellation.
    let _lease = match pair_gate.try_enter(swap.a, swap.b) {
        Some(lease) => lease,
        None => {
            stats.busy_pair.fetch_add(1, AtomicOrdering::Relaxed);
            return Ok(());
        }
    };
    println!(
        "Observed QuickSwap direct swap hash={hash:?} amountInRaw={}",
        swap.amount_in
    );
    discover(
        &provider,
        swap.a,
        swap.b,
        q_factory,
        s_factory,
        cfg,
        Some((hash, swap)),
    )
    .await?;
    stats.analyzed.fetch_add(1, AtomicOrdering::Relaxed);
    Ok(())
}

fn parse_pair_args(args: &[String]) -> Result<Option<(Address, Address)>, AppError> {
    if args.is_empty() {
        return Ok(None);
    }
    if args.len() != 3 || args[0] != "--pair" {
        return Err("Usage: polygon_sniper [--pair 0xTOKEN_A 0xTOKEN_B]".into());
    }
    let a = Address::from_str(&args[1])?;
    let b = Address::from_str(&args[2])?;
    if !valid_tokens(a, b) {
        return Err("Provide two distinct, nonzero ERC-20 addresses".into());
    }
    Ok(Some((a, b)))
}

/// Rechecks router/factory correctness on each new WebSocket connection.
async fn connect_verified(
    ws_url: &str,
    quick_router: Address,
    quick_factory: Address,
    sushi_router: Address,
    sushi_factory: Address,
) -> Result<WsProvider, AppError> {
    let provider: WsProvider = Arc::new(Provider::<Ws>::connect(ws_url).await?);
    let chain = provider.get_chainid().await?;
    if chain != U256::from(137u64) {
        return Err(format!("Wrong chain: expected Polygon 137, got {chain}").into());
    }
    check_router(&provider, "QuickSwap V2", quick_router, quick_factory).await?;
    check_router(&provider, "SushiSwap V2", sushi_router, sushi_factory).await?;
    Ok(provider)
}

/// One subscription session. The JoinSet cancels unfinished read-only tasks
/// upon failure/disconnect, preventing orphaned background RPC work.
async fn monitor_session(
    provider: WsProvider,
    quick_router: Address,
    quick_factory: Address,
    sushi_factory: Address,
    cfg: CostConfig,
    seen: Arc<Mutex<SeenHashes>>,
    pair_gate: PairGate,
    stats: Arc<ObservationStats>,
) -> Result<(), AppError> {
    let permits = Arc::new(Semaphore::new(MAX_IN_FLIGHT));
    let mut tasks = JoinSet::new();
    // Ethers' generic subscription lets us invoke Alchemy's supported
    // eth_subscribe variant directly instead of subscribing to ALL pending txs.
    // hashesOnly=true minimizes socket payloads; get_transaction() follows
    // only for hashes admitted by the bounded worker pool.
    let params = filtered_pending_params(quick_router);
    let mut stream = timeout(SUBSCRIBE_TIMEOUT, provider.subscribe::<_, H256>(params))
        .await
        .map_err(|_| "Filtered Alchemy pending subscription timed out")??;
    println!(
        "OBSERVER CONNECTED: Alchemy toAddress={quick_router:#x} hashesOnly=true dedupe_ttl={}s cap={} max_in_flight={MAX_IN_FLIGHT}; read-only.",
        SEEN_TTL.as_secs(),
        SEEN_CAPACITY
    );
    let mut heartbeat = tokio::time::interval(HEARTBEAT_PERIOD);
    heartbeat.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    loop {
        tokio::select! {
            next_hash = stream.next() => {
                let Some(hash) = next_hash else {
                    return Err("Filtered pending subscription ended; reconnect required".into());
                };
                stats.observed.fetch_add(1, AtomicOrdering::Relaxed);
                // Capacity check first; dropped hashes are NOT marked seen.
                let permit = match Arc::clone(&permits).try_acquire_owned() {
                    Ok(p) => p,
                    Err(_) => {
                        stats.overloaded.fetch_add(1, AtomicOrdering::Relaxed);
                        continue;
                    }
                };
                let is_new = seen.lock().unwrap_or_else(|e| e.into_inner())
                    .mark_if_new(hash, Instant::now());
                if !is_new {
                    stats.deduplicated.fetch_add(1, AtomicOrdering::Relaxed);
                    continue;
                }
                let reader = Arc::clone(&provider);
                let gate = pair_gate.clone();
                let worker_stats = Arc::clone(&stats);
                tasks.spawn(async move {
                    let _permit = permit;
                    match timeout(TASK_TIMEOUT, inspect_pending(
                        reader, hash, quick_router, quick_factory, sushi_factory,
                        cfg, gate, Arc::clone(&worker_stats)
                    )).await {
                        Ok(Err(err)) => {
                            worker_stats.errors.fetch_add(1, AtomicOrdering::Relaxed);
                            eprintln!("Read-only discovery error for {hash:?}: {err}");
                        },
                        Err(_) => {
                            worker_stats.errors.fetch_add(1, AtomicOrdering::Relaxed);
                            eprintln!("Read-only discovery timeout for {hash:?}");
                        },
                        Ok(Ok(())) => {},
                    }
                });
            }
            joined = tasks.join_next(), if !tasks.is_empty() => {
                if let Some(Err(err)) = joined {
                    stats.errors.fetch_add(1, AtomicOrdering::Relaxed);
                    eprintln!("Read-only worker join error: {err}");
                }
            }
            _ = heartbeat.tick() => {
                // A dead WebSocket may stop delivering pending events without
                // ending the stream. Validate RPC liveness periodically.
                match timeout(HEALTH_RPC_TIMEOUT, provider.get_block_number()).await {
                    Ok(Ok(block)) => println!("OBSERVER heartbeat Polygon block={block}"),
                    Ok(Err(err)) => return Err(format!("WebSocket heartbeat RPC failed: {err}").into()),
                    Err(_) => return Err("WebSocket heartbeat RPC timed out".into()),
                }
                stats.print();
            }
        }
    }
}

async fn monitor_forever(
    ws_url: &str,
    quick_router: Address,
    quick_factory: Address,
    sushi_router: Address,
    sushi_factory: Address,
    cfg: CostConfig,
) -> Result<(), AppError> {
    let stats = Arc::new(ObservationStats::default());
    let seen = Arc::new(Mutex::new(SeenHashes::new(SEEN_TTL, SEEN_CAPACITY)));
    let pair_gate = PairGate::default();
    let mut backoff_seconds = 2u64;
    loop {
        let connected = timeout(
            CONNECTION_TIMEOUT,
            connect_verified(
                ws_url,
                quick_router,
                quick_factory,
                sushi_router,
                sushi_factory,
            ),
        )
        .await;
        match connected {
            Ok(Ok(provider)) => {
                let start = Instant::now();
                if let Err(err) = monitor_session(
                    provider,
                    quick_router,
                    quick_factory,
                    sushi_factory,
                    cfg,
                    Arc::clone(&seen),
                    pair_gate.clone(),
                    Arc::clone(&stats),
                )
                .await
                {
                    eprintln!("OBSERVATION DISCONNECTED: {err}");
                }
                if start.elapsed() >= Duration::from_secs(60) {
                    backoff_seconds = 2;
                } else {
                    backoff_seconds = (backoff_seconds * 2).min(BACKOFF_MAX_SECONDS);
                }
            }
            Ok(Err(err)) => {
                eprintln!("WebSocket setup failed (check endpoint/network): {err}");
                backoff_seconds = (backoff_seconds * 2).min(BACKOFF_MAX_SECONDS);
            }
            Err(_) => {
                eprintln!("WebSocket setup timed out");
                backoff_seconds = (backoff_seconds * 2).min(BACKOFF_MAX_SECONDS);
            }
        }
        println!("Reconnecting read-only observer in {backoff_seconds}s...");
        tokio::time::sleep(Duration::from_secs(backoff_seconds)).await;
    }
}

#[tokio::main]
async fn main() -> Result<(), AppError> {
    let args: Vec<String> = env::args().skip(1).collect();
    let pair_args = parse_pair_args(&args)?;
    let cfg = CostConfig::from_env()?;
    let ws_url = env::var("ALCHEMY_WS_URL")?;
    if !ws_url.starts_with("wss://") {
        return Err("ALCHEMY_WS_URL must be a secure wss:// WebSocket endpoint".into());
    }
    let quick_router = Address::from_str(QUICKSWAP_V2_ROUTER)?;
    let quick_factory = Address::from_str(QUICKSWAP_V2_FACTORY)?;
    let sushi_router = Address::from_str(SUSHISWAP_V2_ROUTER)?;
    let sushi_factory = Address::from_str(SUSHISWAP_V2_FACTORY)?;
    println!(
        "READ-ONLY STEP 7: no wallet, no transaction signing, no broadcasting, no nonce allocation."
    );
    println!(
        "Flash fee bps={:?} (None means UNKNOWN); estimated gas units={}, gas multiplier bps={}, minimum net margin bps={}. Gas is not measured.",
        cfg.flash_fee_bps, cfg.gas_units, cfg.gas_multiplier_bps, cfg.min_net_profit_bps
    );

    if let Some((a, b)) = pair_args {
        // One-shot mode fails quickly instead of retrying forever.
        let provider = timeout(
            CONNECTION_TIMEOUT,
            connect_verified(
                &ws_url,
                quick_router,
                quick_factory,
                sushi_router,
                sushi_factory,
            ),
        )
        .await
        .map_err(|_| "Timed out connecting to Polygon for --pair")??;
        discover(&provider, a, b, quick_factory, sushi_factory, cfg, None).await?;
        return Ok(());
    }
    println!(
        "Alchemy subscription filters to the QuickSwap V2 router. Only supported direct two-token V2 swaps are analyzed; others are counted. Filtered mempool visibility is incomplete."
    );
    tokio::select! {
        outcome = monitor_forever(&ws_url, quick_router, quick_factory, sushi_router, sushi_factory, cfg) => outcome,
        signal = tokio::signal::ctrl_c() => {
            signal?;
            println!("Ctrl-C: stopping read-only observer; outstanding work cancelled.");
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ethers::abi::{Token, encode};

    fn addr(x: u64) -> Address {
        Address::from_low_u64_be(x)
    }

    fn example_tx(path: Vec<Address>) -> Vec<u8> {
        let mut bytes = vec![0x38, 0xed, 0x17, 0x39];
        let args = vec![
            Token::Uint(U256::from(1_000_000u64)),
            Token::Uint(U256::from(900_000u64)),
            Token::Array(path.into_iter().map(Token::Address).collect()),
            Token::Address(addr(123)),
            Token::Uint(U256::from(1_999_999_999u64)),
        ];
        bytes.extend(encode(&args));
        bytes
    }

    #[test]
    fn parses_direct_exact_input_swap() {
        let tx = example_tx(vec![addr(1), addr(2)]);
        assert_eq!(
            decode_direct_swap(&tx),
            Some((addr(1), addr(2), U256::from(1_000_000u64)))
        );
    }

    #[test]
    fn ignores_multihop_and_invalid_input() {
        assert!(decode_direct_swap(&example_tx(vec![addr(1), addr(2), addr(3)])).is_none());
        assert!(decode_direct_swap(&example_tx(vec![addr(1), addr(1)])).is_none());
        assert!(decode_direct_swap(&example_tx(vec![addr(0), addr(1)])).is_none());
        assert!(decode_direct_swap(&[0xde, 0xad, 0xbe, 0xef]).is_none());
    }

    fn pool(
        venue: &'static str,
        token0: Address,
        token1: Address,
        r0: U256,
        r1: U256,
    ) -> PoolSnapshot {
        PoolSnapshot {
            venue,
            pair: addr(50),
            factory: addr(51),
            token0,
            token1,
            reserve0: r0,
            reserve1: r1,
            last_updated: 0,
        }
    }

    #[test]
    fn rejects_fee_on_transfer_selector() {
        let mut tx = example_tx(vec![addr(1), addr(2)]);
        tx[..4].copy_from_slice(&[0x5c, 0x11, 0xd7, 0x95]);
        assert!(decode_direct_swap(&tx).is_none());
    }

    #[test]
    fn reserve_orientation_is_correct_both_ways() {
        let p = pool("A", addr(1), addr(2), U256::from(10u8), U256::from(20u8));
        assert_eq!(
            oriented_reserves(&p, addr(1), addr(2)),
            Some((10.into(), 20.into()))
        );
        assert_eq!(
            oriented_reserves(&p, addr(2), addr(1)),
            Some((20.into(), 10.into()))
        );
        assert_eq!(oriented_reserves(&p, addr(1), addr(3)), None);
    }

    #[test]
    fn formats_units_and_prices_with_mixed_decimals() {
        assert_eq!(format_units(U256::from(12345678u64), 6), "12.345678");
        assert_eq!(format_units(U256::from(1000000u64), 6), "1");
        assert_eq!(format_units(U256::from(1u8), 18), "0.000000000000000001");
        let p = pool(
            "A",
            addr(1),
            addr(2),
            U256::from(2_000_000u64),
            U256::from(3_000_000_000_000_000_000u128),
        );
        assert_eq!(spot_price(&p, addr(1), addr(2), 6, 18).unwrap(), "1.5");
        assert_eq!(
            spot_price(&p, addr(2), addr(1), 18, 6)
                .unwrap()
                .starts_with("0.66666666"),
            true
        );
    }

    #[test]
    fn exact_v2_quote_matches_reference_case() {
        // 1000 into 10_000/10_000, with 0.3% fee, floors to 906.
        assert_eq!(
            get_amount_out(1000.into(), 10000.into(), 10000.into()),
            Some(906.into())
        );
        assert_eq!(get_amount_out(0.into(), 10000.into(), 10000.into()), None);
        assert_eq!(get_amount_out(100.into(), 0.into(), 10000.into()), None);
    }

    #[test]
    fn larger_b_per_a_pool_sets_correct_buy_direction() {
        let q = pool("Q", addr(1), addr(2), 1000.into(), 2000.into());
        let s = pool("S", addr(1), addr(2), 1000.into(), 1000.into());
        assert_eq!(
            relative_spot_direction(&q, &s, addr(1), addr(2)),
            Some(Ordering::Greater)
        );
        assert_eq!(
            relative_spot_direction(&q, &s, addr(2), addr(1)),
            Some(Ordering::Less)
        );
        let y = get_amount_out(10.into(), q.reserve0, q.reserve1).unwrap();
        let z = get_amount_out(y, s.reserve1, s.reserve0).unwrap();
        assert!(z > U256::from(10u8)); // Gross-positive before gas/flash fees
    }

    #[test]
    fn equal_pools_cannot_yield_positive_gross_round_trip() {
        let p = pool("A", addr(1), addr(2), 1_000_000.into(), 1_000_000.into());
        let x = U256::from(1_000u64);
        let y = get_amount_out(x, p.reserve0, p.reserve1).unwrap();
        let z = get_amount_out(y, p.reserve1, p.reserve0).unwrap();
        assert!(z < x);
    }

    #[test]
    fn captured_polygon_pool_snapshot_has_sub_fee_spread() {
        // Real observed snapshot from user's Step 2 run at block 96,172,923.
        // token0 is 0x8f3... token1 is 0xeb5...
        let a = Address::from_str("0xeb51d9a39ad5eef215dc0bf39a8821ff804a0f01").unwrap();
        let b = Address::from_str("0x8f3cf7ad23cd3cadbd9735aff958023239c6a063").unwrap();
        let q = pool(
            "Q",
            b,
            a,
            U256::from_dec_str("10913185704203366399785217").unwrap(),
            U256::from_dec_str("37853612822196485").unwrap(),
        );
        let s = pool(
            "S",
            b,
            a,
            U256::from_dec_str("153713508343666003194").unwrap(),
            U256::from_dec_str("534538600482").unwrap(),
        );
        assert_eq!(
            relative_spot_direction(&q, &s, a, b),
            Some(Ordering::Greater)
        );
        assert_eq!(relative_spot_direction(&q, &s, b, a), Some(Ordering::Less));
        for (first, second) in [(&q, &s), (&s, &q)] {
            for (start, mid) in [(a, b), (b, a)] {
                let (r_in, r_out) = oriented_reserves(first, start, mid).unwrap();
                let (s_in, s_out) = oriented_reserves(second, mid, start).unwrap();
                let x = min(r_in, s_out) / U256::from(10_000u64);
                let y = get_amount_out(x, r_in, r_out).unwrap();
                let z = get_amount_out(y, s_in, s_out).unwrap();
                assert!(z < x, "snapshot is not gross-profitable at small test size");
            }
        }
    }

    #[test]
    fn pending_state_projection_is_correct_and_checks_min_out() {
        let p = pool("Quick", addr(1), addr(2), 10_000.into(), 20_000.into());
        let details = SwapDetails {
            a: addr(1),
            b: addr(2),
            amount_in: 1000.into(),
            min_out: 1000.into(),
            deadline: 200.into(),
        };
        let (proj, output) = hypothetical_quickswap_after(&p, details, 100.into()).unwrap();
        assert_eq!(output, 1813.into());
        assert_eq!(proj.reserve0, U256::from(11_000u64));
        assert_eq!(proj.reserve1, U256::from(18_187u64));
        let too_much = SwapDetails {
            min_out: output + U256::one(),
            ..details
        };
        assert!(hypothetical_quickswap_after(&p, too_much, 100.into()).is_none());
        assert!(hypothetical_quickswap_after(&p, details, 201.into()).is_none());
    }

    #[test]
    fn validates_canonical_pool_tokens_both_directions() {
        assert!(matches_pair(addr(1), addr(2), addr(1), addr(2)));
        assert!(matches_pair(addr(1), addr(2), addr(2), addr(1)));
        assert!(!matches_pair(addr(2), addr(1), addr(1), addr(2)));
        assert!(!matches_pair(addr(1), addr(3), addr(1), addr(2)));
    }
    #[test]
    fn classifies_router_and_unsupported_swap_input() {
        let router = addr(11);
        let token_a = addr(12);
        let token_b = addr(13);
        let input = example_tx(vec![token_a, token_b]);
        assert!(matches!(
            classify_quickswap_tx(Some(addr(99)), router, &input),
            ClassifiedSwap::WrongRouter
        ));
        assert!(matches!(
            classify_quickswap_tx(Some(router), router, &[]),
            ClassifiedSwap::Unsupported
        ));
        match classify_quickswap_tx(Some(router), router, &input) {
            ClassifiedSwap::Direct(swap) => {
                assert_eq!(swap.a, token_a);
                assert_eq!(swap.b, token_b);
            }
            _ => panic!("Expected supported direct swap"),
        }
    }
}
