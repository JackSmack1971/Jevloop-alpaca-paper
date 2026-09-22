"""Jev clients with deadline-bounded GET-free request behavior.

Retries apply only to the idempotent decision request, never broker order POSTs.
"""
from __future__ import annotations

import math
import os
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass

import requests

TYPESAFE_DIRECT_URL = "https://api.typesafe.ai/v1/systemone"
GATEWAY_URL = "https://ai-gateway.vercel.sh/typesafe/v1/systemone"
RETRYABLE = {429, 500, 502, 503, 504, 529}
# ~3.05s is slightly above the default TCP retransmission window (Requests' own
# documented rationale for connect timeouts); bounds a hung connect attempt without
# shortening how long we wait for an already-connected, slow-to-respond read.
_CONNECT_TIMEOUT_S = 3.05


class DecisionClientError(RuntimeError):
    pass


class DecisionSchemaError(RuntimeError):
    """A provider response did not satisfy the public decision schema."""


def _response_parts(data: object, *, default_model: str) -> tuple[Mapping, dict]:
    """Extract a provider response without echoing provider-controlled content."""
    if not isinstance(data, Mapping):
        raise DecisionSchemaError("decision response must be a mapping")
    answers = data.get("answers")
    if not isinstance(answers, Mapping):
        raise DecisionSchemaError("decision response field 'answers' must be a mapping")
    return answers, {
        "model": data.get("model", default_model),
        "usage": data.get("usage", {}),
    }


@dataclass
class BaseDecisionClient:
    name: str
    model: str
    def ask(self, state: dict, questions: dict, timeout: float) -> tuple[dict, dict]:
        raise NotImplementedError


def _post(url: str, headers: dict, body: dict, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    for attempt in range(3):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DecisionClientError("decision deadline exceeded")
        try:
            read_timeout = min(remaining, 5.0)
            resp = requests.post(
                url, headers=headers, json=body, timeout=(min(_CONNECT_TIMEOUT_S, read_timeout), read_timeout)
            )
        except requests.RequestException as exc:
            if attempt == 2:
                raise DecisionClientError(f"decision transport failure: {exc}") from exc
            resp = None
        if resp is not None:
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError as exc:
                    raise DecisionClientError("decision provider returned invalid JSON") from exc
            if resp.status_code not in RETRYABLE:
                raise DecisionClientError(f"decision HTTP {resp.status_code}: {resp.text[:240]}")
            retry_after = resp.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else 0.25 * (2 ** attempt)
            except ValueError:
                delay = 0.25 * (2 ** attempt)
        else:
            delay = 0.25 * (2 ** attempt)
        delay += random.uniform(0, 0.05)
        if time.monotonic() + delay >= deadline:
            raise DecisionClientError("decision deadline exceeded during retry backoff")
        time.sleep(delay)
    raise DecisionClientError("decision request failed")


class TypeSafeDirectClient(BaseDecisionClient):
    def __init__(self, api_key: str, model: str):
        super().__init__("TypeSafe direct", model)
        self.api_key = api_key
    def ask(self, state: dict, questions: dict, timeout: float) -> tuple[dict, dict]:
        t0 = time.monotonic()
        data = _post(TYPESAFE_DIRECT_URL, {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                     {"state": state, "model": self.model, "questions": questions}, timeout)
        answers, response_meta = _response_parts(data, default_model=self.model)
        return answers, {"route": self.name, **response_meta,
                         "latency_ms": round((time.monotonic()-t0)*1000, 2)}


class GatewayClient(BaseDecisionClient):
    def __init__(self, api_key: str):
        super().__init__("Vercel AI Gateway", "typesafe-ai/jev")
        self.api_key = api_key
    def ask(self, state: dict, questions: dict, timeout: float) -> tuple[dict, dict]:
        t0 = time.monotonic()
        data = _post(GATEWAY_URL, {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                     {"state": state, "model": self.model, "questions": questions}, timeout)
        answers, response_meta = _response_parts(data, default_model=self.model)
        return answers, {"route": self.name, **response_meta,
                         "latency_ms": round((time.monotonic()-t0)*1000, 2)}


class MockDecisionClient(BaseDecisionClient):
    """Deterministic-enough offline stand-in. Never evidence for provider quality."""
    def __init__(self, seed: int = 7):
        super().__init__("MOCK", "mock-jev-0.2")
        self.rng = random.Random(seed)
    @staticmethod
    def _choice(probs: dict[str, float]) -> dict:
        total = sum(probs.values())
        probs = {k: v/total for k,v in probs.items()}
        choice = max(probs, key=probs.get)
        n = len(probs); peak = probs[choice]
        conf = (n*peak-1)/(n-1) if n > 1 else 1.0
        return {"type":"choice","choice":choice,"probabilities":{k:round(v,4) for k,v in probs.items()},"confidence":round(conf,4)}
    @staticmethod
    def _score(centre: float, labels: list[str]) -> dict:
        weights = [math.exp(-((i-centre)**2)/0.6) for i in range(len(labels))]
        total = sum(weights); probs=[w/total for w in weights]
        score=sum(i*p for i,p in enumerate(probs)); peak=max(probs); n=len(probs)
        conf=(n*peak-1)/(n-1)
        return {"type":"score","score":round(score,4),"legend":{str(i):v for i,v in enumerate(labels)},
                "probabilities":{str(i):round(p,4) for i,p in enumerate(probs)},"confidence":round(conf,4)}
    def ask(self, state: dict, questions: dict, timeout: float) -> tuple[dict, dict]:
        t0=time.monotonic()
        trend = max(-1.0,min(1.0,(state.get("return_1m") or 0.0)*500))
        imb = state.get("imbalance") or 0.0
        up=max(0.05,0.34+0.25*trend+0.15*imb); down=max(0.05,0.34-0.25*trend-0.15*imb); neutral=0.32
        spread=state.get("spread_bps") or 5.0
        toxic=max(0.0,min(1.0,0.35+abs(imb)*0.4+self.rng.uniform(-0.05,0.05)))
        liq=max(0.0,min(1.0,spread/40.0+self.rng.uniform(-0.05,0.05)))
        util=state.get("inventory_utilization") or 0.0
        answers={
            "regime":self._choice({"trending":0.25+abs(trend)*0.4,"mean_reverting":0.3,"high_vol":0.25,"crisis":0.2}),
            "direction":self._choice({"up":up,"down":down,"neutral":neutral}),
            "toxic_flow":{"type":"noul","noul":round(toxic,4)},
            "liquidity_stressed":{"type":"noul","noul":round(liq,4)},
            "quote_environment":self._score(max(0,min(3,2.5-2*liq)),["Do not quote","Marginal","Standard","Excellent"]),
            "inventory_pressure":self._score(max(0,min(3,util*3)),["None","Mild","High","Reduce now"]),
            "execution_health":self._score(2.2,["Broken","Degraded","Normal","Optimal"]),
        }
        return answers,{"route":self.name,"model":self.model,"latency_ms":round((time.monotonic()-t0)*1000,2),"usage":{}}


def resolve_decision_client(*, mock: bool = False) -> BaseDecisionClient:
    if mock:
        return MockDecisionClient()
    direct_key=os.getenv("TYPESAFE_API_KEY")
    direct_model=os.getenv("TYPESAFE_MODEL")
    gateway_key=os.getenv("AI_GATEWAY_API_KEY")
    if direct_key:
        if not direct_model:
            raise DecisionClientError("TYPESAFE_API_KEY is set but TYPESAFE_MODEL is empty; use GET /v1/models and pin one")
        return TypeSafeDirectClient(direct_key,direct_model)
    if gateway_key:
        return GatewayClient(gateway_key)
    raise DecisionClientError("no Jev provider key configured; use --mock only for offline/dry evaluation")
