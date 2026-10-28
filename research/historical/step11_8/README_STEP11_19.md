# Step 11.19 — Cross-venue tick-aware historical quote screen

**Purpose.** After Step 11.18, compare the existing QuickSwap Algebra USDT/WETH pool with the deeper Uniswap V3 USDT/WETH 500-fee pool using *both deployed historical quoters*, not spot estimates.

- Polygon chain 137; block **96179799**, hash `0xfccfb30b9570d2765591c262ec13e89550ba0a2f5904e6e8874423040491e3f4`.
- Quotes both directions, in stablecoin start/end denominations: 10, 100 and 1,000 USDT.
- Uniswap QuoterV2 selector `0xc6a5026a`, tuple input and four-word return.
- QuickSwap Algebra Polygon Quoter selector `0x2d9ebd1d`, four input words and two-word `(amountOut, fee)` return.
- Uses Step 11.9's validated anchor loader to obtain the historically verified Algebra pool address; does not assume an address from memory.
- Verifies chain, block hash, contract code at historical block, tokens and Uniswap fee tier.
- Corrects Step 11.18's wrong Swap-event topic; separately checks **20-block** activity for both pools. Unknown activity is explicitly `UNVERIFIED`, not zero.
- 30 RPC requests max; 180-second monotonic deadline; 200 KB response size per call; no full-block trace, no gas model, no signing or broadcasting.

**Important:** Two historical `eth_call` simulations at block end are NOT the sequential effects of an executed flash-loan transaction, nor a synchronized *intratransaction* quote. Even a positive delta is merely grounds for stricter follow-up replay. Algebra may apply dynamic fees; report fee units as the Quoter returns them (not interpreted as a constant fee).

## Install

Save the ZIP to Windows `E:` and `scp` it to your VPS; then:

```bash
cd /root/polygon_step11_8/step11_8_polygon_tx_boundary_probe
unzip -o /root/step11_19_cross_venue_quotes_patch.zip
sha256sum -c SHA256SUMS_STEP11_19
python3 -m unittest discover -s scripts -p 'test_step11_19_cross_venue_quotes.py' -v
python3 scripts/step11_19_cross_venue_quotes.py
```

Send the 6 `size=...` quote lines, `swap_activity_last_20_blocks` and `verdict=`. Never share `POLYGON_RPC_URL` or API keys. Evidence: `outputs/step11_19_cross_venue_quotes.json`.

Official sources: https://docs.quickswap.exchange/overview/contracts-and-addresses ; https://docs.algebra.finance/algebra-integral-documentation/algebra-v1-technical-reference/contracts/api-reference-v1.0/v1.0-periphery/iquoter ; https://developers.uniswap.org/docs/protocols/v3/deployments/v3-polygon-deployments
