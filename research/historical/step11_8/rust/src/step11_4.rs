//! Step 11.4 swap-only event metadata cross-check, atop Step 11.3 full quote audit.
//! Historical end-of-block pricing is NOT a valid per-transaction replay.
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};

const V2_SWAP:&str="0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822";
const V3_SWAP:&str="0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67";
fn fld<'a>(v:&'a Value,k:&str)->Result<&'a Value,String>{v.get(k).ok_or_else(||format!("missing {k}"))}
fn strf<'a>(v:&'a Value,k:&str)->Result<&'a str,String>{fld(v,k)?.as_str().ok_or_else(||format!("not string {k}"))}
fn u(v:&Value,k:&str)->Result<u64,String>{fld(v,k)?.as_u64().ok_or_else(||format!("not u64 {k}"))}
fn hex(s:&str,n:usize)->bool{s.len()==n&&s.starts_with("0x")&&s[2..].bytes().all(|b|b.is_ascii_hexdigit()&&!b.is_ascii_uppercase())}

fn check_event(e:&Value,bhash:&str,pools:&BTreeMap<String,(String,String,String)>)->Result<(String,String,u64,u64),String>{
 let pool=strf(e,"pool")?;
 let (pair,kind,venue)=pools.get(pool).ok_or("event does not belong to a verified pool")?;
 if strf(e,"pair_id")?!=pair||strf(e,"kind")?!=kind||strf(e,"venue")?!=venue{
  return Err("event pool/pair/protocol mismatch".into());
 }
 if strf(e,"block_hash")?!=bhash{return Err("swap block hash mismatch".into());}
 let hash=strf(e,"tx_hash")?;
 if !hex(hash,66)||hash[2..].bytes().all(|b|b==b'0')||!hex(pool,42){return Err("bad transaction/pool address".into());}
 let topic=strf(e,"topic0")?;
 let expected=if kind=="v2" {V2_SWAP} else {V3_SWAP};
 if topic!=expected{return Err("wrong swap event topic".into());}
 Ok((hash.to_owned(),pair.to_owned(),u(e,"transaction_index")?,u(e,"log_index")?))
}

pub(crate) fn print_report(json:&str)->Result<(),String>{
 // Full existing safety and quote verification first.
 let s=crate::step11_3::assess(json)?;
 let v:Value=serde_json::from_str(json).map_err(|_|"invalid JSON")?;
 let flags=fld(&v,"step11_4")?;
 if strf(flags,"selection")?!="CANONICAL_V2_V3_SWAP_TOPIC_ONLY"||
    fld(flags,"transaction_indices_verified")?.as_bool()!=Some(true)||
    fld(flags,"individual_transaction_state_reconstructed")?.as_bool()!=Some(false)||
    fld(flags,"gross_positive_not_execution_authorization")?.as_bool()!=Some(true){
  return Err("unsafe Step 11.4 assertion".into());
 }
 let mut tx_count=0usize;
 let blocks=fld(&v,"selected_blocks")?.as_array().ok_or("missing blocks")?;
 for block in blocks{
  let bhash=strf(block,"hash")?;
  let pairs=fld(block,"pairs")?.as_array().ok_or("missing pool snapshots")?;
  let mut pools:BTreeMap<String,(String,String,String)>=BTreeMap::new();
  for pair in pairs{
   let id=strf(pair,"pair_id")?.to_owned();
   for (kind,venues) in [("v2",fld(pair,"v2")?),("v3",fld(pair,"v3")?)]{
    let obj=venues.as_object().ok_or("bad venues")?;
    for (venue,pool) in obj{
     if pool.is_null(){continue;}
     let addr=strf(pool,"address")?.to_owned();
     if pools.insert(addr,(id.clone(),kind.to_owned(),venue.clone())).is_some(){
      return Err("duplicate pool in snapshots".into());
     }
    }
   }
  }
  let evs=fld(block,"swap_events")?.as_array().ok_or("missing swap-event provenance")?;
  if evs.is_empty()||evs.len() as u64!=u(block,"activity_event_count")?{
   return Err("swap count mismatch".into());
  }
  let mut previous=None;
  let mut seen_logs=BTreeSet::new();
  let mut tx_indices=BTreeMap::new();
  let mut active=BTreeSet::new();
  for e in evs{
   let (tx,pair,txi,li)=check_event(e,bhash,&pools)?;
   if !seen_logs.insert(li){return Err("duplicate log index".into());}
   if let Some(prev)=previous {if (txi,li)<=prev{return Err("unsorted transaction/log order".into());}}
   previous=Some((txi,li));
   if let Some(old)=tx_indices.insert(tx.clone(),txi){if old!=txi{return Err("tx hash reused with different index".into());}}
   active.insert(pair);
  }
  if tx_indices.len() as u64!=u(block,"distinct_swap_txs")?{
   return Err("distinct transaction count mismatch".into());
  }
  let active_given=fld(block,"active_pairs")?.as_array().ok_or("bad active pairs")?;
  let from_json:BTreeSet<String>=active_given.iter().map(|v|v.as_str().unwrap_or("").to_string()).collect();
  if active!=from_json||active_given.len()!=active.len(){return Err("swap-active pairs mismatch".into());}
  tx_count+=tx_indices.len();
 }
 println!("STEP 11.4 — SWAP-EVENT-SELECTED POLYGON V2/V3 HISTORICAL SCREEN — READ-ONLY");
 println!("selected_blocks={} verified_swap_events={} distinct_transactions_across_blocks={} route_quotes={} gross_rejected={} skipped={} gross_positive_unassessed={}",
  s.blocks,s.events,tx_count,s.quotes,s.gross_rejected,s.skipped,s.positive_unassessed);
 println!("ALWAYS BLOCK: end-of-block eth_call does not reconstruct intra-block transaction state. No net gas, execution or inclusion evidence.");
 println!("NO PRIVATE KEY / NO SIGNER / NO BROADCAST / NO RELAY SUBMISSION");
 Ok(())
}

#[cfg(test)]
mod tests{
 use super::*;
 use serde_json::json;
 fn fixture()->(Value,BTreeMap<String,(String,String,String)>) {
  let pool="0x0000000000000000000000000000000000000001";
  let e=json!({"pool":pool,"pair_id":"DAI_WETH","kind":"v2","venue":"quick",
   "block_hash":"0x1111111111111111111111111111111111111111111111111111111111111111",
   "tx_hash":"0x2222222222222222222222222222222222222222222222222222222222222222",
   "transaction_index":3,"log_index":12,"topic0":V2_SWAP});
  let mut map=BTreeMap::new();map.insert(pool.to_string(),("DAI_WETH".into(),"v2".into(),"quick".into()));
  (e,map)
 }
 #[test]fn accepts_verified_v2_event(){let (e,p)=fixture();assert!(check_event(&e,e["block_hash"].as_str().unwrap(),&p).is_ok());}
 #[test]fn wrong_topic_fails(){let (mut e,p)=fixture();e["topic0"]=json!(V3_SWAP);assert!(check_event(&e,e["block_hash"].as_str().unwrap(),&p).is_err());}
 #[test]fn forged_pair_fails(){let (mut e,p)=fixture();e["pair_id"]=json!("USDCe_WETH");assert!(check_event(&e,e["block_hash"].as_str().unwrap(),&p).is_err());}
 #[test]fn changed_block_fails(){let (e,p)=fixture();assert!(check_event(&e,&format!("0x{}","3".repeat(64)),&p).is_err());}
 #[test]fn unknown_pool_fails(){let (mut e,p)=fixture();e["pool"]=json!("0x0000000000000000000000000000000000000002");assert!(check_event(&e,e["block_hash"].as_str().unwrap(),&p).is_err());}
 #[test]fn wrong_index_type_fails(){let (mut e,p)=fixture();e["transaction_index"]=json!("3");assert!(check_event(&e,e["block_hash"].as_str().unwrap(),&p).is_err());}
}
