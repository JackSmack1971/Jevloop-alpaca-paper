# Research and documentation basis for the 0.3.0 rebuild

These sources informed design choices; none proves `jev-loop` has economic edge or production suitability.

## OpenAI / Codex engineering

- OpenAI Codex Skills documentation: https://developers.openai.com/codex/skills/ — supports progressive disclosure (`name`/`description` first, `SKILL.md` on activation), focused skills, references/scripts/assets, and `agents/openai.yaml` metadata including `allow_implicit_invocation`.
- OpenAI, **“Rethinking skills and prompts for GPT-6 Astra”** (2026-09-11): https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra — recommends a lightweight root skill file that acts as a router pointing at supporting material rather than forcing comprehensive reading upfront, scenario-tied (not blanket) references, and stripping exhaustive recipe-style steps now that the model handles nuance without them. The 0.3.0 pass tightened `SKILL.md`; the 0.3.1 pass went further and rewrote it as an explicit minimal router (invariants + a reference-selection table + one entry-point line), removing the enumerated command list and numbered workflow recipe in favor of `--help` and `references/`.
- OpenAI, **“Harness engineering: leveraging Codex in an agent-first world”** (2026-02-11): https://openai.com/index/harness-engineering/ — motivates concise agent-facing maps, repository-resident knowledge, deterministic checks, and feedback loops rather than giant instruction files.
- OpenAI, **“Running Codex safely at OpenAI”** (2026-05-08): https://openai.com/index/running-codex-safely/ — reinforces the separation between model behavior and actual technical boundaries/permissions. The 0.3 runtime therefore removes the old executable live path instead of relying on a prohibition in prompt text.

## Calibration and time-series evaluation

- Allan H. Murphy (1973), **“A New Vector Partition of the Probability Score,”** *Journal of Applied Meteorology* 12(4), 595–600. DOI: https://doi.org/10.1175/1520-0450(1973)012%3C0595:ANVPOT%3E2.0.CO;2 — decomposes probability-score behavior into uncertainty, reliability, and resolution, motivating reference-aware interpretation.
- Allan H. Murphy (1974), **“A Sample Skill Score for Probability Forecasts,”** *Monthly Weather Review* 102(1), 48–55. DOI: https://doi.org/10.1175/1520-0493(1974)102%3C0048:ASSSFP%3E2.0.CO;2 — motivates sample-frequency/climatology references and difference-based forecast comparison.
- Jeremy Nixon et al. (2019), **“Measuring Calibration in Deep Learning,”** arXiv:1904.01685 — shows that common ECE conclusions are sensitive to design choices such as top-label reduction and binning. The package now reports a classwise adaptive view in addition to legacy top-label ECE.
- Linwei Tao et al. (2025), **“Revisiting Uncertainty Estimation and Calibration of Large Language Models,”** arXiv:2505.23854 — reports that model accuracy, calibration, and error-ranking quality need not coincide, supporting multi-metric evaluation rather than trusting one confidence signal.
- Hamid Arian, Daniel Norouzi Mobarekeh & Luis Seco (2024), **“Backtest overfitting in the machine learning era: A comparison of out-of-sample testing methods in a synthetic controlled environment,”** *Knowledge-Based Systems* 305, 112477. DOI: https://doi.org/10.1016/j.knosys.2024.112477 — explicitly studies non-stationarity, autocorrelation, regime shifts, PBO and DSR; in its controlled experiments CPCV reduced overfitting risk relative to traditional alternatives.
- David H. Bailey & Marcos López de Prado (2014), **“The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality,”** *Journal of Portfolio Management* (subsequent publication) / SSRN — motivates accounting for the number of tried configurations and non-normal returns before interpreting a selected Sharpe ratio.
- Moving/block bootstrap literature is used because ordinary IID resampling destroys serial dependence. The implementation reports block-size sensitivity instead of presenting one arbitrary block length as authoritative.

## Market making

- Marco Avellaneda & Sasha Stoikov (2008), **"High-frequency trading in a limit order book,"** *Quantitative Finance* 8(3), 217-224. DOI: https://doi.org/10.1080/14697680701381228 -- source of the reservation-price and optimal-spread formulas in `jevloop.pricing.avellaneda_stoikov_quotes`. The paper's optimal spread `delta = gamma*sigma^2*(T-t) + (2/gamma)*ln(1+gamma/kappa)` is the *total* bid-ask width (quotes sit at `r +/- delta/2`); a 0.3.0-era revision applied the full `delta` on each side and so quoted roughly double the intended spread. Fixed in 0.3.1; see `references/architecture-and-safety.md`.

## Current platform contracts

- TypeSafe OpenAPI: https://api.typesafe.ai/docs
- Vercel Jev integration changelog (2026-09-21): https://vercel.com/changelog/ai-gateway-now-supports-typesafe-clients-and-http-api-for-jev
- Alpaca Trading/Market Data docs: https://docs.alpaca.markets/ -- order-status enum (terminal vs. non-terminal, including `done_for_day`'s "will not receive further updates until the next trading day" semantics) and asset-schema field documentation (`min_order_size`/`min_trade_increment`/`price_increment` as crypto-only fields) informed the 0.3.1 execution-adapter fixes.
- Requests advanced docs, timeout section: https://requests.readthedocs.io/en/latest/user/advanced/ -- documents that a single scalar `timeout` value applies independently to both the connect and read phases, and recommends a connect timeout "slightly larger than a multiple of 3" (the default TCP retransmission window); informed the 0.3.1 `(connect, read)` timeout tuples in `jevloop/execution/alpaca.py` and `jevloop/client.py`.
- uv project/lock docs: https://docs.astral.sh/uv/

Vendor latency, pricing, calibration, availability, and throughput claims remain vendor claims unless measured on this package's own workload.
