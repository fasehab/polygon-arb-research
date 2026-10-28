-- Polygon arbitrage DISCOVERY ONLY: 30 completed UTC days, 2026-09-28 to 2026-10-27.
-- Paste into https://dune.com/queries/new and export results as CSV.
-- This is an intentionally high-precision first-pass detector, NOT a proof of
-- profits retained by a bot or of opportunities independently capturable.
-- It examines whole-transaction, ordered 2/3-leg closed loops; more complicated
-- routes/extra swaps and unindexed venues will not appear.
-- Dune Trino SQL; based on dex.trades and gas.fees curated tables.
WITH swaps AS (
    SELECT
        block_time, block_number, tx_hash, tx_from, tx_to, evt_index,
        project, version, project_contract_address,
        token_sold_address, token_bought_address,
        token_sold_symbol, token_bought_symbol,
        token_sold_amount, token_bought_amount,
        amount_usd
    FROM dex.trades
    WHERE blockchain = 'polygon'
      AND block_time >= TIMESTAMP '2026-09-28 00:00:00'
      AND block_time <  TIMESTAMP '2026-10-28 00:00:00'
),
whole_tx AS (
    SELECT
        tx_hash,
        min(block_time) AS block_time,
        min(block_number) AS block_number,
        min_by(tx_from, evt_index) AS tx_from,
        min_by(tx_to, evt_index) AS tx_to,
        count(*) AS leg_count,
        count(DISTINCT project_contract_address) AS distinct_pools,
        array_agg(token_sold_address ORDER BY evt_index) AS sold_tokens,
        array_agg(token_bought_address ORDER BY evt_index) AS bought_tokens,
        array_agg(token_sold_symbol ORDER BY evt_index) AS sold_symbols,
        array_agg(token_bought_symbol ORDER BY evt_index) AS bought_symbols,
        array_agg(token_sold_amount ORDER BY evt_index) AS sold_amounts,
        array_agg(token_bought_amount ORDER BY evt_index) AS bought_amounts,
        array_agg(amount_usd ORDER BY evt_index) AS usd_legs,
        array_agg(project ORDER BY evt_index) AS venues,
        array_agg(version ORDER BY evt_index) AS versions,
        array_agg(to_hex(project_contract_address) ORDER BY evt_index) AS pool_addresses,
        count_if(token_sold_amount <= 0 OR token_bought_amount <= 0 OR amount_usd IS NULL) AS invalid_legs
    FROM swaps
    GROUP BY 1
    HAVING count(*) BETWEEN 2 AND 3
       AND count(DISTINCT project_contract_address) >= 2
),
closed_loops AS (
    SELECT *,
        element_at(sold_amounts, 1) AS initial_token_amount,
        element_at(bought_amounts, leg_count) AS final_token_amount,
        element_at(usd_legs, 1) AS initial_usd,
        (element_at(bought_amounts, leg_count) - element_at(sold_amounts, 1))
          * element_at(usd_legs, 1) / nullif(element_at(sold_amounts, 1), 0) AS approximate_gross_usd
    FROM whole_tx
    WHERE invalid_legs = 0
      AND element_at(sold_tokens, 1) = element_at(bought_tokens, leg_count)
      AND element_at(bought_tokens, 1) = element_at(sold_tokens, 2)
      AND (
          (leg_count = 2
            AND element_at(sold_amounts, 2) BETWEEN element_at(bought_amounts, 1)*0.99
                                                AND element_at(bought_amounts, 1)*1.01)
          OR
          (leg_count = 3
            AND element_at(bought_tokens, 2) = element_at(sold_tokens, 3)
            AND element_at(sold_amounts, 2) BETWEEN element_at(bought_amounts, 1)*0.99
                                                AND element_at(bought_amounts, 1)*1.01
            AND element_at(sold_amounts, 3) BETWEEN element_at(bought_amounts, 2)*0.99
                                                AND element_at(bought_amounts, 2)*1.01)
      )
),
ranked AS (
    SELECT
        c.block_time,
        c.block_number,
        concat('0x', to_hex(c.tx_hash)) AS tx_hash,
        concat('0x', to_hex(c.tx_from)) AS tx_from,
        concat('0x', to_hex(c.tx_to)) AS tx_to,
        c.leg_count,
        element_at(c.sold_symbols, 1) AS start_token_symbol,
        concat('0x', to_hex(element_at(c.sold_tokens, 1))) AS start_token_address,
        c.initial_token_amount,
        c.final_token_amount,
        c.initial_usd,
        c.approximate_gross_usd,
        f.tx_fee_usd AS gas_usd,
        CASE WHEN f.tx_fee_usd IS NOT NULL
             THEN c.approximate_gross_usd - f.tx_fee_usd END AS approximate_after_gas_usd,
        CASE WHEN f.tx_fee_usd IS NULL THEN 'GAS_MISSING' ELSE 'GAS_PRESENT' END AS gas_status,
        array_join(c.venues, ' -> ') AS venue_route,
        array_join(c.pool_addresses, ' -> ') AS pool_route,
        array_join(c.sold_symbols, ' -> ') || ' -> ' || element_at(c.bought_symbols, c.leg_count) AS token_route,
        array_join(c.versions, ' -> ') AS protocol_versions
    FROM closed_loops c
    LEFT JOIN gas.fees f
      ON c.tx_hash = f.tx_hash
     AND f.blockchain = 'polygon'
     AND f.block_time >= TIMESTAMP '2026-09-28 00:00:00'
     AND f.block_time <  TIMESTAMP '2026-10-28 00:00:00'
    WHERE c.initial_usd >= 100
      AND c.approximate_gross_usd >= 2
)
SELECT *
FROM ranked
-- only rank with known network fee: rows missing fee should be investigated separately
ORDER BY approximate_gross_usd DESC
LIMIT 10000;
