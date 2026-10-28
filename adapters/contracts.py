"""Typed *design interfaces* only. Implementations supplied by downstream operators.

There is intentionally no live network client, wallet loader or transaction sender.
"""
from dataclasses import dataclass
from typing import Protocol, Optional


@dataclass(frozen=True)
class PoolState:
    chain_id: int
    block_number: int
    block_hash: str
    pool_address: str
    token0: str
    token1: str
    state_root_or_id: str


@dataclass(frozen=True)
class ExactInputQuote:
    chain_id: int
    block_hash: str
    pool_address: str
    token_in: str
    token_out: str
    amount_in: int
    amount_out: int
    fee_description: str


@dataclass(frozen=True)
class SimulationAssessment:
    status: str
    chain_id: int
    block_hash: str
    gross_profit_wei: Optional[int]
    gas_cost_wei: Optional[int]
    other_costs_wei: Optional[int]
    details: str


class StateProvider(Protocol):
    def read_pool(self, pool_address: str, block_hash: str) -> PoolState: ...


class QuoteProvider(Protocol):
    def quote_exact_input(self, pool: PoolState, token_in: str, amount_in: int) -> ExactInputQuote: ...


class ExecutionSimulator(Protocol):
    def assess(self, route: tuple[ExactInputQuote, ...]) -> SimulationAssessment: ...


class SubmissionBackend(Protocol):
    """Unsafe by default: intentionally no implementation in this repository."""
    def submit(self, signed_payload: bytes) -> str: ...


def safe_research_status(assessment: SimulationAssessment) -> str:
    """Read-only result classification; never authorizes trading."""
    if assessment.status != "EXACT_STATE_SIMULATED":
        return "UNPROVEN"
    if assessment.chain_id != 137 or not assessment.block_hash:
        return "UNPROVEN"
    values = (assessment.gross_profit_wei, assessment.gas_cost_wei, assessment.other_costs_wei)
    if any(v is None or v < 0 for v in values):
        return "UNPROVEN"
    net = assessment.gross_profit_wei - assessment.gas_cost_wei - assessment.other_costs_wei
    return "SIMULATION_NET_POSITIVE_NOT_EXECUTION_PROOF" if net > 0 else "SIMULATION_NOT_PROFITABLE"
