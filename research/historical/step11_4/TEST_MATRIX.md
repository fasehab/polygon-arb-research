# Step 11 Validation Matrix

| Check | Coverage | Locally executed? |
|---|---|---|
| Old Step 10 Rust tests | 51 previously VPS validated | Step 10 previously |
| New Step 11 Rust tests | 10 schema and adversarial cases | No (VPS required) |
| Old Solidity suites | 17+7+4+5 tests inherited, source unchanged | Previously user verified in stages |
| Python collector tests | 11 new; 12 inherited (23 total) | Yes, offline mocked RPC |
| Package/source manifest | SHA-256 for all source files | Yes |
| Chain + archive validation | `eth_chainId`, historic calls, finality buffer | VPS required |
| Block hash read twice | Rejects changed historical block | Offline mocked test |
| Whole-block RPC fallback | Restart on fallback rather than mix responses | Offline mocked test |
| Known conventional research tokens | DAI, WETH, bridged USDC.e only | Address policy validated; no transfer safety claim |
| Pair existence + token-order orientation | Both V2 factories across 3 pairs | VPS archive RPC required |
| Two router orders + gas-adjusted profit | Quotes both orders; DOES NOT gas-adjust | Historical RPC required |
| Full executor transaction / relay | Not implemented, explicitly blocked | No |

**Safety:** This is quote research only. A positive quote is NOT a tradeable MEV opportunity. Neither a paid Alchemy plan nor a dedicated Polygon node is required by the code; a working historical-state RPC endpoint IS required.
