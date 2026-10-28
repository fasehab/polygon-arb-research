//! Step 6: bounded, read-only observation controls.
//! No wallet, nonce allocation, signing, or transaction broadcasting.
use ethers::types::{Address, H256};
use serde_json::{Value, json};
use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// Parameters passed to eth_subscribe. These are the exact Alchemy filter
/// shape for Polygon's alchemy_pendingTransactions subscription.
pub(crate) fn filtered_pending_params(router: Address) -> Value {
    json!([
        "alchemy_pendingTransactions",
        { "toAddress": [format!("{router:#x}")], "hashesOnly": true }
    ])
}

/// TTL-bounded hash deduplication. An accepted hash is suppressed for its TTL,
/// including when its RPC lookup fails; this intentionally favors rate safety.
pub(crate) struct SeenHashes {
    ttl: Duration,
    capacity: usize,
    map: HashMap<H256, Instant>,
    order: VecDeque<(H256, Instant)>,
}
impl SeenHashes {
    pub(crate) fn new(ttl: Duration, capacity: usize) -> Self {
        assert!(capacity > 0);
        Self {
            ttl,
            capacity,
            map: HashMap::new(),
            order: VecDeque::new(),
        }
    }
    fn remove_oldest(&mut self) {
        if let Some((hash, at)) = self.order.pop_front() {
            if self.map.get(&hash).copied() == Some(at) {
                self.map.remove(&hash);
            }
        }
    }
    pub(crate) fn mark_if_new(&mut self, hash: H256, now: Instant) -> bool {
        while let Some((_, at)) = self.order.front() {
            if now.saturating_duration_since(*at) < self.ttl {
                break;
            }
            self.remove_oldest();
        }
        if self.map.contains_key(&hash) {
            return false;
        }
        while self.map.len() >= self.capacity {
            self.remove_oldest();
        }
        self.map.insert(hash, now);
        self.order.push_back((hash, now));
        true
    }
}

/// Canonicalizes pair direction so A/B and B/A share the same observation lock.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash)]
struct PairKey(Address, Address);
impl PairKey {
    fn new(a: Address, b: Address) -> Self {
        if a.as_bytes() <= b.as_bytes() {
            Self(a, b)
        } else {
            Self(b, a)
        }
    }
}

#[derive(Clone, Default)]
pub(crate) struct PairGate {
    active: Arc<Mutex<HashSet<PairKey>>>,
}

/// Lease releases even when the Tokio task is cancelled or times out.
pub(crate) struct PairLease {
    gate: PairGate,
    key: PairKey,
}
impl Drop for PairLease {
    fn drop(&mut self) {
        self.gate
            .active
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .remove(&self.key);
    }
}
impl PairGate {
    pub(crate) fn try_enter(&self, a: Address, b: Address) -> Option<PairLease> {
        if a == b || a == Address::zero() || b == Address::zero() {
            return None;
        }
        let key = PairKey::new(a, b);
        let mut active = self.active.lock().unwrap_or_else(|e| e.into_inner());
        if !active.insert(key) {
            return None;
        }
        Some(PairLease {
            gate: self.clone(),
            key,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn addr(x: u64) -> Address {
        Address::from_low_u64_be(x)
    }
    fn hash(x: u64) -> H256 {
        H256::from_low_u64_be(x)
    }

    #[test]
    fn filtered_subscription_is_hash_only_and_router_scoped() {
        let router = addr(7);
        let params = filtered_pending_params(router);
        assert_eq!(params[0], "alchemy_pendingTransactions");
        assert_eq!(params[1]["toAddress"][0], format!("{router:#x}"));
        assert_eq!(params[1]["hashesOnly"], true);
        assert!(params[1].get("fromAddress").is_none());
    }

    #[test]
    fn duplicates_suppressed_until_ttl_expires() {
        let mut cache = SeenHashes::new(Duration::from_secs(60), 3);
        let now = Instant::now();
        assert!(cache.mark_if_new(hash(1), now));
        assert!(!cache.mark_if_new(hash(1), now + Duration::from_secs(59)));
        assert!(cache.mark_if_new(hash(1), now + Duration::from_secs(60)));
    }

    #[test]
    fn bounded_cache_evicts_oldest() {
        let mut cache = SeenHashes::new(Duration::from_secs(3600), 2);
        let now = Instant::now();
        assert!(cache.mark_if_new(hash(1), now));
        assert!(cache.mark_if_new(hash(2), now + Duration::from_millis(1)));
        assert!(!cache.mark_if_new(hash(2), now + Duration::from_millis(2)));
        assert!(cache.mark_if_new(hash(3), now + Duration::from_millis(3)));
        assert!(cache.mark_if_new(hash(1), now + Duration::from_millis(4)));
    }

    #[test]
    fn pair_gate_normalizes_direction_and_releases_on_drop() {
        let gate = PairGate::default();
        let lease = gate.try_enter(addr(1), addr(2)).unwrap();
        assert!(gate.try_enter(addr(2), addr(1)).is_none());
        assert!(gate.try_enter(addr(1), addr(3)).is_some());
        drop(lease);
        assert!(gate.try_enter(addr(2), addr(1)).is_some());
        assert!(gate.try_enter(addr(1), addr(1)).is_none());
    }

    #[tokio::test]
    async fn aborted_task_releases_pair_lease() {
        use tokio::sync::oneshot;
        let gate = PairGate::default();
        let in_task = gate.clone();
        let (tx, rx) = oneshot::channel();
        let task = tokio::spawn(async move {
            let _lease = in_task.try_enter(addr(1), addr(2)).unwrap();
            let _ = tx.send(());
            std::future::pending::<()>().await;
        });
        rx.await.unwrap();
        assert!(gate.try_enter(addr(1), addr(2)).is_none());
        task.abort();
        let _ = task.await;
        assert!(gate.try_enter(addr(2), addr(1)).is_some());
    }
}
