# Architecture diagrams

GitHub renders Mermaid natively — view this file on github.com to see the rendered diagrams.

---

## 1. System overview (where everything lives)

```mermaid
flowchart TB
    subgraph TRIGGERS["⏰ Triggers"]
        CRON_DAILY["GitHub Actions cron<br/>21:10 UTC daily"]
        CRON_HOURLY["GitHub Actions cron<br/>hourly"]
        CRON_WEEKLY["GitHub Actions cron<br/>weekly"]
        MANUAL["Manual workflow_dispatch<br/>(counted by peek_counter)"]
        DOCKER_LOCAL["Local: docker run<br/>(reference only — not prod)"]
    end

    subgraph WORKFLOWS[".github/workflows"]
        WF_DAILY["daily-run.yml"]
        WF_HOURLY["hourly-reconcile.yml"]
        WF_WEEKLY["weekly-digest.yml"]
        WF_BACKFILL["backfill-journal.yml"]
        WF_ALERTS["readiness-and-dd-alerts.yml"]
        WF_CI["ci.yml"]
    end

    subgraph PREFLIGHT["🛡️ Pre-flight gates (Layer 1)"]
        G1["override_delay.py<br/>SHA + 24h cool-off"]
        G2["peek_counter.py<br/>>3/30d → alert"]
        G3["deployment_anchor.py<br/>locks DD baseline"]
        G4["kill_switch.py<br/>6 triggers"]
        G5["risk_manager.py<br/>9 ladders + freeze state"]
    end

    subgraph DECISION["🧠 Decision pipeline"]
        VARIANTS["variants.py<br/>LIVE = momentum_top15_mom_weighted_v1"]
        STRATEGY["strategy.py<br/>rank + select"]
        SIGNALS["signals.py / vol_signals.py<br/>residual_momentum.py"]
        REGIME["regime.py / hmm_regime.py<br/>+ macro.py overlay"]
        ALLOCATOR["risk_parity.py / hrp.py<br/>weight assignment"]
        PLANNER["order_planner.py<br/>weights → orders"]
    end

    subgraph DATA["📊 Data sources"]
        YF["yfinance<br/>price history"]
        UNIVERSE["universe_pit.py<br/>S&P 500 PIT membership"]
        ALPACA_DATA["Alpaca Market Data<br/>real-time quotes"]
    end

    subgraph BROKER_LAYER["🏦 Broker (abstraction planned v3.49)"]
        ALPACA["Alpaca paper<br/>(today)"]
        PUBLIC["Public.com Roth IRA<br/>(planned, day 90+)"]
    end

    subgraph STORAGE["💾 Storage"]
        JOURNAL[("SQLite<br/>data/trader.db<br/>decisions, orders,<br/>snapshots, lots")]
        CACHE[("Parquet cache<br/>data/cache/")]
        STATE[("State files<br/>deployment_anchor.json<br/>risk_freeze_state.json<br/>peek_log.json")]
        ARTIFACT["GitHub Artifact<br/>trader-journal<br/>(cross-workflow lookup)"]
    end

    subgraph LLM["🤖 LLM augmentation"]
        CRITIC["critic.py<br/>Bull/Bear/Risk swarm"]
        POSTMORTEM["postmortem.py<br/>nightly self-review"]
        NARRATIVE["narrative.py<br/>daily report writer"]
        VERIFIER["agent_verifier.py<br/>TRUST/VERIFY/ABSTAIN gate"]
    end

    subgraph OUTPUTS["📤 Outputs"]
        SLACK["Slack webhook"]
        EMAIL["Email alerts"]
        REPORT["Daily report<br/>SPY-relative dashboard"]
    end

    subgraph RECONCILE["🔄 Reconciliation"]
        REC["reconcile.py<br/>journal vs broker<br/>HALT on drift"]
        BACKFILL["backfill_journal_from_alpaca.py<br/>restore from broker truth"]
    end

    CRON_DAILY --> WF_DAILY
    CRON_HOURLY --> WF_HOURLY
    CRON_WEEKLY --> WF_WEEKLY
    MANUAL --> WF_BACKFILL
    MANUAL --> WF_DAILY
    DOCKER_LOCAL -.-> WF_DAILY

    WF_DAILY --> G1 --> G2 --> G3 --> G4 --> G5
    G5 --> VARIANTS
    VARIANTS --> STRATEGY
    UNIVERSE --> STRATEGY
    YF --> SIGNALS --> STRATEGY
    REGIME --> STRATEGY
    STRATEGY --> ALLOCATOR --> PLANNER

    SIGNALS --> CRITIC
    CRITIC --> VERIFIER
    POSTMORTEM --> VERIFIER
    VERIFIER -. "ABSTAIN: discard" .-> POSTMORTEM
    VERIFIER -. "TRUST/VERIFY: feed back" .-> STRATEGY

    PLANNER --> ALPACA
    PLANNER -.future.-> PUBLIC
    ALPACA_DATA --> PLANNER
    ALPACA --> JOURNAL
    PLANNER --> JOURNAL
    G3 --> STATE
    G5 --> STATE

    WF_HOURLY --> REC
    REC --> JOURNAL
    REC --> ALPACA
    REC -. "drift detected" .-> SLACK
    WF_BACKFILL --> BACKFILL
    BACKFILL --> ALPACA
    BACKFILL --> JOURNAL

    JOURNAL --> ARTIFACT
    ARTIFACT --> WF_DAILY

    YF --> CACHE
    CACHE --> SIGNALS

    JOURNAL --> NARRATIVE
    NARRATIVE --> VERIFIER
    NARRATIVE --> REPORT --> SLACK
    G4 -. "halt fired" .-> EMAIL
    G5 -. "freeze fired" .-> EMAIL

    classDef gate fill:#fde68a,stroke:#92400e,stroke-width:2px,color:#000
    classDef llm fill:#ddd6fe,stroke:#5b21b6,stroke-width:2px,color:#000
    classDef store fill:#bbf7d0,stroke:#14532d,stroke-width:2px,color:#000
    classDef trigger fill:#bfdbfe,stroke:#1e3a8a,stroke-width:2px,color:#000
    classDef broker fill:#fecaca,stroke:#7f1d1d,stroke-width:2px,color:#000

    class G1,G2,G3,G4,G5 gate
    class CRITIC,POSTMORTEM,NARRATIVE,VERIFIER llm
    class JOURNAL,CACHE,STATE,ARTIFACT store
    class CRON_DAILY,CRON_HOURLY,CRON_WEEKLY,MANUAL,DOCKER_LOCAL trigger
    class ALPACA,PUBLIC broker
```

---

## 2. Daily run sequence (what happens at 21:10 UTC)

```mermaid
sequenceDiagram
    autonumber
    participant Cron as GitHub cron
    participant WF as daily-run.yml
    participant Art as trader-journal artifact
    participant Main as src/trader/main.py
    participant Override as override_delay
    participant Peek as peek_counter
    participant Anchor as deployment_anchor
    participant Kill as kill_switch
    participant Risk as risk_manager
    participant Strat as strategy + variants
    participant Plan as order_planner
    participant Alp as Alpaca paper
    participant Jrn as SQLite journal
    participant Rec as reconcile
    participant LLM as Claude (narrative + postmortem)
    participant Ver as agent_verifier
    participant Slk as Slack

    Cron->>WF: 21:10 UTC fire
    WF->>Art: download latest trader-journal (cross-workflow)
    WF->>Main: python scripts/run_daily.py
    Main->>Override: check_override_delay()
    Override-->>Main: allowed? (24h since LIVE config SHA change?)
    alt config recently changed
        Main-->>WF: skip — cool-off active
        WF->>Slk: alert "skipped, override-delay"
    end
    Main->>Peek: record_event(GITHUB_EVENT_NAME)
    Peek-->>Main: count > 3 in 30d? alert
    Main->>Anchor: get_or_set_anchor(equity)
    Main->>Alp: get account equity + positions
    Main->>Kill: check 6 triggers
    Kill-->>Main: HALT? if yes, abort
    Main->>Risk: check_freeze_state(equity, anchor)
    Risk-->>Main: NORMAL / DAILY_LOSS_FREEZE / DEPLOY_DD_FREEZE / LIQUIDATION_GATE
    Main->>Strat: rank top-15 momentum (PIT universe)
    Strat-->>Main: target weights
    Main->>Risk: validate weights (caps, sectors, single-name)
    Risk-->>Main: approved weights
    Main->>Plan: weights × equity → orders
    Plan->>Alp: submit_orders (notional, market hours only)
    Alp-->>Plan: order acks
    Plan->>Jrn: write decisions, orders
    Main->>Jrn: snapshot equity + positions
    Main->>LLM: narrative.py daily report
    LLM->>Ver: verify_citations(narrative)
    Ver-->>LLM: TRUST / VERIFY / ABSTAIN
    LLM-->>Main: narrative (or abstained)
    Main->>Slk: post report
    Main->>Rec: reconcile post-trade
    Rec-->>Main: clean / drift
    alt drift detected
        Main->>Slk: HALT alert
    end
    WF->>Art: upload updated trader-journal
```

---

## 3. Promotion pipeline (3-gate methodology)

```mermaid
flowchart LR
    IDEA["💡 Strategy idea"] --> SHADOW["Implement as<br/>shadow variant<br/>in variants.py"]
    SHADOW --> RUN["Shadow runs alongside LIVE<br/>(no real orders)"]

    RUN --> G1{"Gate 1<br/>5-regime<br/>survivor backtest<br/>(bull/bear/sideways/<br/>vol-spike/grind)"}
    G1 -- "wins ≥ 4/5" --> G2{"Gate 2<br/>PIT validation<br/>universe_pit.py<br/>+ no future leak"}
    G1 -- "fails" --> KILL["📕 Logged in<br/>docs/CRITIQUE.md<br/>kill list — never<br/>re-propose"]

    G2 -- "Sharpe drop < 30%" --> G3{"Gate 3<br/>CPCV<br/>cpcv_backtest.py<br/>PBO < 0.5,<br/>DSR > 0"}
    G2 -- "fails" --> KILL

    G3 -- "passes" --> DECAY{"strategy_decay_check<br/>≥ 30 days outperform<br/>LIVE significantly?"}
    G3 -- "fails" --> KILL

    DECAY -- "yes" --> PRE_REG["Pre-register<br/>params in<br/>PRE_REGISTRATION_OOS.md"]
    DECAY -- "no" --> RUN
    PRE_REG --> PROMOTE["⬆️ status: shadow → live"]
    PROMOTE --> OVERRIDE["override_delay fires<br/>24h cool-off<br/>before next run"]
    OVERRIDE --> LIVE["🟢 LIVE under<br/>new variant"]

    classDef kill fill:#fecaca,stroke:#7f1d1d,stroke-width:2px,color:#000
    classDef gate fill:#fde68a,stroke:#92400e,stroke-width:2px,color:#000
    classDef live fill:#bbf7d0,stroke:#14532d,stroke-width:2px,color:#000

    class KILL kill
    class G1,G2,G3,DECAY gate
    class LIVE,PROMOTE live
```

---

## 4. Defense-in-depth — 4 layers

```mermaid
flowchart TB
    subgraph L1["🔧 Layer 1 — Code (this repo)"]
        L1a["override_delay<br/>24h cool-off on config change"]
        L1b["peek_counter<br/>limit manual triggers"]
        L1c["deployment_anchor<br/>DD math anchored at deploy"]
        L1d["kill_switch — 6 triggers"]
        L1e["risk_manager — 9 ladders<br/>+ DAILY_LOSS / DEPLOY_DD / LIQUIDATION freezes"]
        L1f["agent_verifier<br/>TRUST/VERIFY/ABSTAIN for LLM outputs"]
        L1g["validation — empty/short/stale data raises"]
        L1h["reconcile — HALT on drift"]
    end

    subgraph L2["🏦 Layer 2 — Custodian (broker)"]
        L2a["Alpaca paper / Public.com IRA<br/>NBBO, settlement, regulatory limits<br/>PDT exemption in Roth"]
    end

    subgraph L3["🧑 Layer 3 — Human checkpoint"]
        L3a["BEHAVIORAL_PRECOMMIT.md (signed)<br/>no override after −15% DD<br/>no doubling down<br/>liquidation gate → 7d cool-off + post-mortem"]
        L3b["Spousal pre-brief required<br/>before LIVE flip"]
    end

    subgraph L4["📜 Layer 4 — Document trail"]
        L4a["CRITIQUE.md — kill list"]
        L4b["PRE_REGISTRATION_OOS.md<br/>commits to params before shadow runs"]
        L4c["RESEARCH.md / PAPER.md / ARCHITECTURE.md"]
        L4d["GO_LIVE_CHECKLIST.md — 9 automated gates"]
    end

    REAL_MONEY["💵 Real money in Roth IRA"]

    L1 ==> L2 ==> L3 ==> L4 ==> REAL_MONEY

    classDef code fill:#bfdbfe,stroke:#1e3a8a,stroke-width:2px,color:#000
    classDef broker fill:#fde68a,stroke:#92400e,stroke-width:2px,color:#000
    classDef human fill:#bbf7d0,stroke:#14532d,stroke-width:2px,color:#000
    classDef doc fill:#ddd6fe,stroke:#5b21b6,stroke-width:2px,color:#000
    classDef money fill:#fecaca,stroke:#7f1d1d,stroke-width:3px,color:#000

    class L1a,L1b,L1c,L1d,L1e,L1f,L1g,L1h code
    class L2a broker
    class L3a,L3b human
    class L4a,L4b,L4c,L4d doc
    class REAL_MONEY money
```

---

## 5. Regime overlay composer (v3.49.0 — wired into LIVE)

```mermaid
flowchart LR
    subgraph SIGNALS["📡 3 dormant modules now wired"]
        HMM["hmm_regime.py<br/>3-state Gaussian HMM<br/>on SPY returns"]
        MACRO["macro.py<br/>10y-2y yield curve<br/>+ HYG/LQD ratio"]
        GARCH["garch_vol.py<br/>GARCH(1,1)<br/>vol forecast"]
    end

    subgraph COMPOSE["🧮 regime_overlay.py composer"]
        HMM_M["hmm_mult ∈ {1.15 / 0.85 / 0.30}<br/>bull / transition / bear"]
        MACRO_M["macro_mult ∈ {1.0 / 0.85 / 0.70 / 0.55}<br/>ok / curve / credit / both"]
        GARCH_M["garch_mult ∈ [0.50, 1.10]<br/>vol-target clamped"]
        FINAL["final_mult = product<br/>clamped [0, 1.20]"]
    end

    HMM --> HMM_M --> FINAL
    MACRO --> MACRO_M --> FINAL
    GARCH --> GARCH_M --> FINAL

    FLAG{"REGIME_OVERLAY_ENABLED<br/>env var"}
    FINAL --> FLAG

    APPLY["risk_manager applies<br/>multiplier to gross exposure<br/>(after VIX scaling, before cap)"]
    PASSTHROUGH["risk_manager logs<br/>rationale only<br/>multiplier ignored"]

    FLAG -->|"true"| APPLY
    FLAG -->|"false (default)"| PASSTHROUGH

    classDef sig fill:#bfdbfe,stroke:#1e3a8a,color:#000
    classDef compose fill:#fde68a,stroke:#92400e,color:#000
    classDef apply fill:#bbf7d0,stroke:#14532d,color:#000
    classDef pass fill:#ddd6fe,stroke:#5b21b6,color:#000
    classDef flag fill:#fecaca,stroke:#7f1d1d,color:#000

    class HMM,MACRO,GARCH sig
    class HMM_M,MACRO_M,GARCH_M,FINAL compose
    class APPLY apply
    class PASSTHROUGH pass
    class FLAG flag
```

## 6. Multi-frequency monitoring loop (v3.49.0 — intraday risk added)

```mermaid
flowchart TB
    subgraph TODAY["Today (paper)"]
        DAILY["daily-run<br/>21:10 UTC Mon-Fri<br/>full pipeline"]
        HOURLY["hourly-reconcile<br/>14-20 UTC Mon-Fri<br/>journal vs broker"]
        INTRADAY["intraday-risk-watch<br/>0,30 14-20 UTC Mon-Fri<br/>defensive freeze trigger"]
        WEEKLY["weekly-digest<br/>Sun 5pm PT<br/>SPY-relative + decay"]
        ALERTS["readiness-and-dd-alerts<br/>22:30 UTC Mon-Fri<br/>9-gate + DD tiers"]
    end

    BROKER[("Alpaca paper<br/>(or Public.com IRA<br/>after migration)")]
    JRN[("SQLite journal<br/>+ GitHub artifact")]
    FREEZE[("risk_freeze_state.json<br/>shared by all jobs")]

    DAILY -->|"reads/writes"| JRN
    DAILY -->|"reads/writes"| FREEZE
    DAILY -->|"executes"| BROKER

    HOURLY -->|"reads"| JRN
    HOURLY -->|"reads"| BROKER
    HOURLY -.->|"drift -> alert"| ALERTS

    INTRADAY -->|"reads"| BROKER
    INTRADAY -->|"writes"| FREEZE
    INTRADAY -.->|"freeze fired -> next daily-run skips"| DAILY

    WEEKLY -->|"reads"| JRN
    ALERTS -->|"reads"| BROKER
    ALERTS -->|"reads"| JRN

    classDef cron fill:#bfdbfe,stroke:#1e3a8a,color:#000
    classDef defense fill:#fecaca,stroke:#7f1d1d,color:#000
    classDef store fill:#bbf7d0,stroke:#14532d,color:#000

    class DAILY,HOURLY,WEEKLY,ALERTS cron
    class INTRADAY defense
    class JRN,FREEZE,BROKER store
```

## 7. Broker abstraction (planned, day 60-75)

```mermaid
flowchart LR
    MAIN["main.py<br/>execute.py<br/>reconcile.py<br/>report.py"] --> IFACE["broker.py<br/>(abstract Broker class)<br/>get_account<br/>get_positions<br/>submit_market_order<br/>submit_bracket_order<br/>close_position<br/>get_orders<br/>get_last_price"]

    IFACE -->|"BROKER=alpaca_paper"| ADP_A["broker_alpaca.py<br/>wraps alpaca-py SDK"]
    IFACE -->|"BROKER=public_live"| ADP_P["broker_public.py<br/>wraps publicdotcom-py SDK<br/>UUID order_id, Decimal qty"]

    ADP_A --> ALPACA[("Alpaca paper<br/>Brokerage")]
    ADP_P --> PUBLIC[("Public.com<br/>Roth IRA")]

    GHV["GitHub repo variable<br/>BROKER<br/>(one-click flip)"] -. "configures" .-> IFACE

    classDef interface fill:#ddd6fe,stroke:#5b21b6,stroke-width:2px,color:#000
    classDef adapter fill:#bfdbfe,stroke:#1e3a8a,stroke-width:2px,color:#000
    classDef broker fill:#fecaca,stroke:#7f1d1d,stroke-width:2px,color:#000
    classDef config fill:#fde68a,stroke:#92400e,stroke-width:2px,color:#000

    class IFACE interface
    class ADP_A,ADP_P adapter
    class ALPACA,PUBLIC broker
    class GHV config
```

---

## Where things actually run (today vs future)

| Component | Today | Future (post day 90) |
|---|---|---|
| Daily orchestrator | GitHub Actions cron | GitHub Actions cron |
| Hourly reconcile | GitHub Actions cron | GitHub Actions cron |
| Weekly digest | GitHub Actions cron | GitHub Actions cron |
| Backfill | Manual workflow_dispatch | Manual workflow_dispatch |
| Local debugging | `python scripts/run_daily.py` | same |
| Container | `Dockerfile` exists but **not in prod** — reference for Lightsail/Fly migration if we outgrow GitHub Actions | possibly Cloud Run if needed |
| Brokerage | Alpaca paper | **Public.com Roth IRA** (via broker abstraction) |
| Journal | SQLite + GitHub artifact | same (or Cloud SQL if migrate) |
| Secrets | `.env` local, GitHub secrets in CI | same + Public.com keys added |

---

## Reading order for new contributors

1. Top of `README.md` — what the system does + current state
2. This file — visual mental model
3. `docs/CRITIQUE.md` — what we tried and killed (so you don't re-propose)
4. `docs/PAPER.md` — research framework + evaluation methodology
5. `src/trader/main.py` — entry point; trace through it once
6. `src/trader/variants.py` — read the LIVE variant
7. `src/trader/risk_manager.py` — the ladders that protect real money
8. `docs/SWARM_VERIFICATION_PROTOCOL.md` — required before spawning any LLM agent
9. `docs/MIGRATION_ALPACA_TO_PUBLIC.md` — the next big piece of work
10. `docs/BEHAVIORAL_PRECOMMIT.md` — the human checkpoint
